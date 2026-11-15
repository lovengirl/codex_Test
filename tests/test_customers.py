"""임시 DB에서 고객 목록·숨김 제외·페이지 경계·응답 시간을 검증한다."""

from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
import sqlite3
from time import perf_counter

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session
from sqlalchemy import text

from authentication import COOKIE_NAME, create_token
from customer_queries import customer, visible_customers
from database import connect
from main import create_app
from models import SCHEMA


@pytest.fixture
def authenticated_client(client):
    with client.app.state.engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO employees (id, name, email, address, password_hash) "
            "VALUES (1, '목록검증직원', 'list@example.com', '서울', 'test-hash')"
        ))
    client.cookies.set(COOKIE_NAME, create_token(1, client.app.state.jwt_secret))
    return client


def seed_customers(client, count):
    with client.app.state.engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO customer (id, name, email, address, grade) "
            "VALUES (:id, :name, :email, '서울', '일반')"
        ), [{"id": index, "name": f"고객{index}", "email": f"customer{index}@example.com"}
            for index in range(1, count + 1)])


def test_1만명_500페이지_중복누락없음_각응답_1초이내(authenticated_client):
    client = authenticated_client
    seed_customers(client, 10_000)
    seen = []
    elapsed = []
    for page in range(1, 501):
        started = perf_counter()
        response = client.get(f"/customers?page={page}")
        elapsed.append(perf_counter() - started)
        assert response.status_code == 200
        assert response.context["total"] == 10_000
        assert response.context["total_pages"] == 500
        assert response.context["page"] == page
        assert len(response.context["items"]) == 20
        assert response.text.count("<tr><td>") == 20
        seen.extend(item["id"] for item in response.context["items"])
    assert seen == list(range(1, 10_001))
    assert max(elapsed) < 1, f"최대 응답 시간 {max(elapsed):.3f}초"
    print(f"[통과] 고객 10,000명, 500페이지 전체 확인, 최대 응답 {max(elapsed):.3f}초")


def test_조회_SQL은_공통숨김조건과_LIMIT_OFFSET_사용(authenticated_client):
    client = authenticated_client
    seed_customers(client, 21)
    statements = []

    def capture(connection, cursor, statement, parameters, context, executemany):
        if "FROM customer" in statement:
            statements.append((statement, parameters))

    engine = client.app.state.engine
    event.listen(engine, "before_cursor_execute", capture)
    try:
        response = client.get("/customers?page=2")
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert response.status_code == 200
    assert len(statements) == 2
    assert "count(*)" in statements[0][0]
    assert all("NOT (EXISTS" in sql and "hidden_customer" in sql for sql, _ in statements)
    assert "ORDER BY customer.id" in statements[1][0]
    assert "LIMIT ? OFFSET ?" in statements[1][0]
    assert statements[1][1] == (20, 20)
    assert [item["id"] for item in response.context["items"]] == [21]
    assert 'rel="next"' not in response.text
    assert 'rel="prev"' in response.text


def test_숨김고객은_목록_건수_재사용조회에서_제외(authenticated_client):
    client = authenticated_client
    seed_customers(client, 22)
    with client.app.state.engine.begin() as connection:
        connection.execute(text("INSERT INTO hidden_customer VALUES (1), (22)"))
    response = client.get("/customers")
    assert response.context["total"] == 20
    assert response.context["total_pages"] == 1
    assert [item["id"] for item in response.context["items"]] == list(range(2, 22))
    with Session(client.app.state.engine) as session:
        query = visible_customers().where(customer.c.email == "customer1@example.com")
        assert session.execute(query).first() is None
        assert session.scalar(select(customer.c.id).where(customer.c.id == 1)) == 1
    assert client.get("/customers?page=2").status_code == 404


def test_빈목록과_전체숨김은_첫페이지_빈상태(authenticated_client):
    client = authenticated_client
    for _ in range(2):
        response = client.get("/customers")
        assert response.status_code == 200
        assert response.context["total"] == 0
        assert response.context["total_pages"] == 1
        assert response.context["items"] == []
        assert "표시할 고객이 없습니다." in response.text
        assert 'rel="next"' not in response.text
        assert 'rel="prev"' not in response.text
        assert client.get("/customers?page=2").status_code == 404
        if not _:
            seed_customers(client, 1)
            with client.app.state.engine.begin() as connection:
                connection.execute(text("INSERT INTO hidden_customer VALUES (1)"))


@pytest.mark.parametrize("page", ["0", "-1", "1.5", "abc", "", str(2**63)])
def test_잘못된_페이지는_422(authenticated_client, page):
    assert authenticated_client.get("/customers", params={"page": page}).status_code == 422


def test_페이지링크와_고객정보_HTML이스케이프(authenticated_client):
    client = authenticated_client
    seed_customers(client, 21)
    with client.app.state.engine.begin() as connection:
        connection.execute(text("UPDATE customer SET name = '<script>alert(1)</script>' WHERE id=1"))
    response = client.get("/customers")
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in response.text
    assert "<script>alert(1)</script>" not in response.text
    assert 'href="https://testserver/customers?page=2" rel="next"' in response.text
    assert 'rel="prev"' not in response.text
    assert response.headers["cache-control"] == "no-store"


def test_기존DB에_숨김테이블_생성_반복시작_기존행과스키마보존(tmp_path):
    path = tmp_path / "legacy-customers.db"
    with closing(connect(path)) as connection:
        with connection:
            for statement in SCHEMA:
                if "hidden_customer" not in statement:
                    connection.execute(statement)
            connection.execute("INSERT INTO customer VALUES (7, '기존고객', 'old@example.com', '부산', 'VIP')")
        before = connection.execute("SELECT name, sql FROM sqlite_master ORDER BY name").fetchall()
    for _ in range(2):
        with TestClient(create_app(path)) as client:
            assert client.get("/health").status_code == 200
    with closing(connect(path)) as connection:
        after = connection.execute("SELECT name, sql FROM sqlite_master ORDER BY name").fetchall()
        assert [row for row in after if row[0] != "hidden_customer"] == before
        assert connection.execute("SELECT * FROM hidden_customer").fetchall() == []
        assert connection.execute("SELECT * FROM customer").fetchall() == [
            (7, "기존고객", "old@example.com", "부산", "VIP")
        ]
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            connection.execute("INSERT INTO hidden_customer VALUES (999)")


CUSTOMER_INPUT = {"name": "등록고객", "email": "new-customer@example.com", "address": "부산", "grade": "신규"}


def test_등록_목록반영_수정_상세반영_숨김삭제_원본행보존(authenticated_client):
    client = authenticated_client
    assert 'action="/customers"' in client.get("/customers/new").text
    response = client.post("/customers", data=CUSTOMER_INPUT, follow_redirects=False)
    assert response.status_code == 303
    detail_url = response.headers["location"]
    item = client.get(detail_url).context["customer"]
    customer_id = item["id"]
    assert dict(item) == {"id": customer_id, **CUSTOMER_INPUT}
    listing = client.get("/customers")
    assert listing.context["items"] == [item]
    assert f'href="{detail_url}"' in listing.text
    assert "주문 이력이 없습니다." in client.get(detail_url).text
    form_page = client.get(detail_url + "/edit")
    assert form_page.status_code == 200
    assert 'value="등록고객"' in form_page.text
    changed = CUSTOMER_INPUT | {"name": "수정고객", "address": "서울", "grade": "VIP"}
    response = client.post(detail_url + "/edit", data=changed, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == detail_url
    assert dict(client.get(detail_url).context["customer"]) == {"id": customer_id, **changed}
    response = client.post(detail_url + "/delete", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/customers"
    assert client.get("/customers").context["total"] == 0
    for path in (detail_url, detail_url + "/edit"):
        assert client.get(path).status_code == 404
    assert client.post(detail_url + "/edit", data=CUSTOMER_INPUT).status_code == 404
    assert client.post(detail_url + "/delete").status_code == 404
    with client.app.state.engine.connect() as connection:
        assert dict(connection.execute(text("SELECT * FROM customer")).mappings().one()) == {
            "id": customer_id, **changed,
        }
        assert connection.execute(text("SELECT customer_id FROM hidden_customer")).scalars().all() == [customer_id]
    print("[통과] 등록 → 목록, 수정 → 상세, 숨김 삭제 → 목록 제외 및 원본 행 유지")


def seed_orders(client):
    seed_customers(client, 2)
    with client.app.state.engine.begin() as connection:
        connection.execute(text("INSERT INTO product VALUES (1, '주문검증상품', '식품', 1000)"))
        connection.execute(text(
            "INSERT INTO orders VALUES "
            "(1, 1, 1, 1, 1, 1000, '2026-10-01', '완료'), "
            "(2, 1, 1, 1, 2, 2000, '2026-11-01', '배송중'), "
            "(3, 2, 1, 1, 3, 3000, '2026-11-02', '취소')"
        ))


def test_고객상세_본인주문만_최신순_표시_삭제후주문보존(authenticated_client):
    client = authenticated_client
    seed_orders(client)
    response = client.get("/customers/1")
    assert response.status_code == 200
    assert [order["id"] for order in response.context["orders"]] == [2, 1]
    for expected in ("주문검증상품", "목록검증직원", "2,000원", "배송중", "2026-10-01"):
        assert expected in response.text
    assert "2026-11-02" not in response.text
    assert response.headers["cache-control"] == "no-store"
    assert client.post("/customers/1/delete", follow_redirects=False).status_code == 303
    assert client.get("/customers/1").status_code == 404
    with client.app.state.engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM orders")).scalar() == 3
        assert connection.execute(text("SELECT count(*) FROM customer")).scalar() == 2
    print("[통과] 고객 본인의 주문 이력만 최신순으로 표시, 숨김 삭제 후 주문 3건 보존")


@pytest.mark.parametrize("changes", [
    {"name": " "}, {"address": " "}, {"email": "invalid"}, {"email": ""}, {"grade": "잘못된등급"},
])
def test_등록수정_입력검증_실패시_기존행보존(authenticated_client, changes):
    client = authenticated_client
    seed_customers(client, 1)
    for path in ("/customers", "/customers/1/edit"):
        response = client.post(path, data=CUSTOMER_INPUT | changes)
        assert response.status_code == 422
        assert 'role="alert"' in response.text
        assert "목록검증직원님 안녕하세요" in response.text
    with client.app.state.engine.connect() as connection:
        assert connection.execute(text("SELECT name, email FROM customer")).all() == [
            ("고객1", "customer1@example.com")
        ]


def test_이메일중복_기존대문자와_숨김고객도_409_수정충돌시보존(authenticated_client):
    client = authenticated_client
    seed_customers(client, 2)
    with client.app.state.engine.begin() as connection:
        connection.execute(text("UPDATE customer SET email='EXISTING@example.com' WHERE id=1"))
    for _ in range(2):
        for path in ("/customers", "/customers/2/edit"):
            response = client.post(path, data=CUSTOMER_INPUT | {"email": " existing@example.com "})
            assert response.status_code == 409
            assert "이미 등록된 이메일입니다." in response.text
        with client.app.state.engine.begin() as connection:
            connection.execute(text("INSERT OR IGNORE INTO hidden_customer VALUES (1)"))
    with client.app.state.engine.connect() as connection:
        assert connection.execute(text("SELECT name, email FROM customer WHERE id=2")).one() == (
            "고객2", "customer2@example.com"
        )


def test_공백정리_이메일정규화와_HTML이스케이프(authenticated_client):
    client = authenticated_client
    values = CUSTOMER_INPUT | {"name": " <script>alert(1)</script> ", "email": " NEW@example.com ", "address": " 부산 "}
    response = client.post("/customers", data=values)
    assert response.status_code == 200
    assert response.context["customer"]["email"] == "new@example.com"
    assert response.context["customer"]["address"] == "부산"
    assert "<script>alert(1)</script>" not in response.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in response.text
    edit = client.get(str(response.url) + "/edit")
    assert 'value="&lt;script&gt;alert(1)&lt;/script&gt;"' in edit.text


@pytest.mark.parametrize("method,path", [
    ("get", "/customers/new"), ("get", "/customers/1"), ("get", "/customers/1/edit"),
    ("post", "/customers"), ("post", "/customers/1/edit"), ("post", "/customers/1/delete"),
])
def test_고객등록상세수정삭제_인증필수(client, method, path):
    response = client.request(method, path, data=CUSTOMER_INPUT, headers={"Accept": "application/json"})
    assert response.status_code == 401


@pytest.mark.parametrize("path", ["/customers", "/customers/1/edit", "/customers/1/delete"])
def test_외부출처_고객변경차단(authenticated_client, path):
    client = authenticated_client
    seed_customers(client, 1)
    response = client.post(path, data=CUSTOMER_INPUT, headers={"Origin": "https://other.example"})
    assert response.status_code == 403
    assert client.get("/customers").context["total"] == 1
    assert client.get("/customers/1").context["customer"]["name"] == "고객1"


@pytest.mark.parametrize("customer_id,status", [(999, 404), (0, 422), (2**63, 422)])
def test_없는고객과_잘못된ID_안전응답(authenticated_client, customer_id, status):
    client = authenticated_client
    for suffix in ("", "/edit"):
        assert client.get(f"/customers/{customer_id}{suffix}").status_code == status
    for suffix in ("/edit", "/delete"):
        assert client.post(f"/customers/{customer_id}{suffix}", data=CUSTOMER_INPUT).status_code == status


def test_동시등록_중복이메일은_한명만생성(authenticated_client):
    client = authenticated_client
    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(
            lambda _: client.post("/customers", data=CUSTOMER_INPUT, follow_redirects=False), range(2)
        ))
    assert sorted(response.status_code for response in responses) == [303, 409]
    assert client.get("/customers").context["total"] == 1


def test_동시수정과숨김삭제_숨김과원본행_주문유지(authenticated_client):
    client = authenticated_client
    seed_orders(client)
    with ThreadPoolExecutor(max_workers=2) as executor:
        update = executor.submit(client.post, "/customers/1/edit", data=CUSTOMER_INPUT, follow_redirects=False)
        delete = executor.submit(client.post, "/customers/1/delete", follow_redirects=False)
        assert update.result().status_code in (303, 404)
        assert delete.result().status_code == 303
    assert client.get("/customers/1").status_code == 404
    with client.app.state.engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM customer WHERE id=1")).scalar() == 1
        assert connection.execute(text("SELECT count(*) FROM hidden_customer WHERE customer_id=1")).scalar() == 1
        assert connection.execute(text("SELECT count(*) FROM orders WHERE customer_id=1")).scalar() == 2
