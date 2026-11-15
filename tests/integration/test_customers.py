"""임시 DB에서 고객 목록·숨김 제외·페이지 경계·응답 시간을 검증한다."""

from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from html import unescape
import re
import sqlite3
from time import perf_counter
from urllib.parse import parse_qs, urlsplit

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session
from sqlalchemy import text

from authentication import COOKIE_NAME, create_token
from customer_queries import MAX_SEARCH_LENGTH, customer, visible_customers
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
    assert len(statements) == 3
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
    response = client.post(detail_url + "/edit", data=changed | {"version": form_page.context["version"]},
                           follow_redirects=False)
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
        values = CUSTOMER_INPUT | changes
        if path.endswith("/edit"):
            values["version"] = client.get(path).context["version"]
        response = client.post(path, data=values)
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
            values = CUSTOMER_INPUT | {"email": " existing@example.com "}
            if path.endswith("/edit"):
                values["version"] = client.get(path).context["version"]
            response = client.post(path, data=values)
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
    version = client.get("/customers/1/edit").context["version"]
    with ThreadPoolExecutor(max_workers=2) as executor:
        update = executor.submit(client.post, "/customers/1/edit",
                                 data=CUSTOMER_INPUT | {"version": version}, follow_redirects=False)
        delete = executor.submit(client.post, "/customers/1/delete", follow_redirects=False)
        assert update.result().status_code in (303, 404)
        assert delete.result().status_code == 303
    assert client.get("/customers/1").status_code == 404
    with client.app.state.engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM customer WHERE id=1")).scalar() == 1
        assert connection.execute(text("SELECT count(*) FROM hidden_customer WHERE customer_id=1")).scalar() == 1
        assert connection.execute(text("SELECT count(*) FROM orders WHERE customer_id=1")).scalar() == 2


@pytest.mark.parametrize("q,ids", [
    ("고객2", [2, 20, 21, 22]), ("CUSTOMER2@EXAMPLE", [2]),
    ("  고객22  ", [22]), ("없는고객", []),
])
def test_이름과이메일_부분검색_대소문자_공백정리(authenticated_client, q, ids):
    client = authenticated_client
    seed_customers(client, 22)
    response = client.get("/customers", params={"q": q})
    assert response.status_code == 200
    assert response.context["q"] == q.strip()
    assert response.context["total"] == len(ids)
    assert [item["id"] for item in response.context["items"]] == ids
    assert 'role="search"' in response.text
    if not ids:
        assert "검색 결과가 없습니다." in response.text


def test_검색_숨김제외_페이지이동시_한글과특수문자검색어유지(authenticated_client):
    client = authenticated_client
    seed_customers(client, 41)
    q = "검색 대상 &+한글"
    with client.app.state.engine.begin() as connection:
        connection.execute(text("UPDATE customer SET name=:name WHERE id<=25"), {"name": q})
        connection.execute(text("INSERT INTO hidden_customer VALUES (1)"))
    first = client.get("/customers", params={"q": q})
    assert first.status_code == 200
    assert first.context["total"] == 24
    assert first.context["total_pages"] == 2
    assert [item["id"] for item in first.context["items"]] == list(range(2, 22))
    next_url = unescape(re.search(r'href="([^"]+)" rel="next"', first.text).group(1))
    assert parse_qs(urlsplit(next_url).query) == {"q": [q], "page": ["2"]}
    second = client.get(next_url)
    assert [item["id"] for item in second.context["items"]] == [22, 23, 24, 25]
    previous_url = unescape(re.search(r'href="([^"]+)" rel="prev"', second.text).group(1))
    assert parse_qs(urlsplit(previous_url).query) == {"q": [q], "page": ["1"]}
    assert 'href="/customers">검색 초기화</a>' in second.text
    assert client.get("/customers", params={"q": q, "page": 3}).status_code == 404
    assert client.get("/customers").context["total"] == 40


@pytest.mark.parametrize("q,ids", [("%", [1]), ("_", [2]), ("/", [3]), ("' OR 1=1 --", [])])
def test_검색_와일드카드는_문자그대로_SQL구문은_실행하지않음(authenticated_client, q, ids):
    client = authenticated_client
    seed_customers(client, 3)
    with client.app.state.engine.begin() as connection:
        connection.execute(text("UPDATE customer SET name=:name WHERE id=:id"), [
            {"id": 1, "name": "특수%고객"}, {"id": 2, "name": "특수_고객"}, {"id": 3, "name": "특수/고객"},
        ])
    response = client.get("/customers", params={"q": q})
    assert response.status_code == 200
    assert [item["id"] for item in response.context["items"]] == ids
    assert response.context["total"] == len(ids)


def test_검색어_HTML이스케이프와_숨김고객_검색결과없음(authenticated_client):
    client = authenticated_client
    seed_customers(client, 1)
    q = '\"><script>alert(1)</script>'
    response = client.get("/customers", params={"q": q})
    assert response.status_code == 200
    assert '<script>alert(1)</script>' not in response.text
    assert '&lt;script&gt;alert(1)&lt;/script&gt;' in response.text
    with client.app.state.engine.begin() as connection:
        connection.execute(text("INSERT INTO hidden_customer VALUES (1)"))
    for q in ("고객1", "customer1@example.com"):
        response = client.get("/customers", params={"q": q})
        assert response.context["total"] == 0
        assert response.context["items"] == []


@pytest.mark.parametrize("q", ["", "   "])
def test_빈검색어는_전체목록과_기존페이지네이션유지(authenticated_client, q):
    client = authenticated_client
    seed_customers(client, 21)
    response = client.get("/customers", params={"q": q})
    assert response.context["q"] == ""
    assert response.context["total"] == 21
    assert len(response.context["items"]) == 20
    assert 'href="https://testserver/customers?page=2" rel="next"' in response.text


def test_검색도_DB에서_조건과_LIMIT_OFFSET_적용(authenticated_client):
    client = authenticated_client
    seed_customers(client, 41)
    statements = []

    def capture(connection, cursor, statement, parameters, context, executemany):
        if "FROM customer" in statement:
            statements.append((statement, parameters))

    engine = client.app.state.engine
    event.listen(engine, "before_cursor_execute", capture)
    try:
        response = client.get("/customers", params={"q": "고객", "page": 2})
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert response.status_code == 200
    assert len(statements) == 3
    assert all("LIKE" in sql and "hidden_customer" in sql for sql, _ in statements[:2])
    assert all("고객" not in sql for sql, _ in statements)
    assert "LIMIT ? OFFSET ?" in statements[1][0]
    assert statements[1][1][-2:] == (20, 20)
    assert response.context["total"] == 41
    assert len(response.context["items"]) == 20


@pytest.mark.parametrize("q,ids", [
    ("aLiCe", [1, 2]), ("EXAMPLE.COM", [1, 2, 3]), ("길동", [3]),
    ("MÜLLER", [2]), ("STRASSE", [2]), ("ÜSER@", [2]),
])
def test_이름과이메일_대소문자무시_부분일치_유니코드지원(authenticated_client, q, ids):
    client = authenticated_client
    with client.app.state.engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO customer (id, name, email, address, grade) "
            "VALUES (:id, :name, :email, '서울', '일반')"
        ), [
            {"id": 1, "name": "ALICE Kim", "email": "Alice@Example.COM"},
            {"id": 2, "name": "Alice Müller Straße", "email": "üser@example.com"},
            {"id": 3, "name": "홍길동", "email": "hong@example.com"},
        ])
    response = client.get("/customers", params={"q": q})
    assert response.status_code == 200
    assert response.context["total"] == len(ids)
    assert [item["id"] for item in response.context["items"]] == ids


@pytest.fixture
def filter_client(authenticated_client):
    client = authenticated_client
    seed_customers(client, 64)
    with client.app.state.engine.begin() as connection:
        connection.execute(text(
            "UPDATE customer SET name=CASE WHEN id%2=0 THEN '이민수' ELSE '이 민수' END, "
            "grade='VIP', address='서울특별시 강남구' WHERE id<=55"
        ))
        connection.execute(text("UPDATE customer SET address='부산광역시 해운대구' WHERE id BETWEEN 46 AND 50"))
        connection.execute(text("UPDATE customer SET grade='일반' WHERE id BETWEEN 51 AND 55"))
        connection.execute(text("UPDATE customer SET address='제주특별자치도 제주시' WHERE id=64"))
        connection.execute(text("INSERT INTO hidden_customer VALUES (1), (64)"))
    return client


@pytest.mark.parametrize("q", ["이민수", "이 민수", "이\t민\u3000수"])
def test_저장된이름과_검색어_공백제거_동일고객조회(filter_client, q):
    response = filter_client.get("/customers", params={"q": q, "grade": "VIP", "region": "서울특별시"})
    assert response.status_code == 200
    assert response.context["total"] == 44
    assert response.context["total_pages"] == 3
    assert [item["id"] for item in response.context["items"]] == list(range(2, 22))
    assert response.text.count("<tr><td>") == 20


@pytest.mark.parametrize("filters,total", [
    ({"grade": "VIP"}, 49), ({"grade": "일반"}, 13),
    ({"region": "서울특별시"}, 49), ({"region": "부산광역시"}, 5),
    ({"grade": "VIP", "region": "부산광역시"}, 5),
    ({"q": "CUSTOMER50@", "grade": "VIP", "region": "부산광역시"}, 1),
])
def test_등급지역_단독복합필터_실제목록건수반영(filter_client, filters, total):
    response = filter_client.get("/customers", params=filters)
    assert response.status_code == 200
    assert response.context["total"] == total
    for item in response.context["items"]:
        if filters.get("grade"):
            assert item["grade"] == filters["grade"]
        if filters.get("region"):
            assert item["address"].split()[0] == filters["region"]
    assert response.context["regions"] == ["부산광역시", "서울", "서울특별시"]


def test_조건URL공유_새로고침_모든페이지링크와폼값_유지(filter_client):
    client = filter_client
    filters = {"q": "이 민수", "grade": "VIP", "region": "서울특별시"}
    response = client.get("/customers", params={**filters, "page": 2})
    for page in (response, client.get(str(response.url))):
        assert page.context["filters"] == filters
        assert page.context["page"] == 2
        assert [item["id"] for item in page.context["items"]] == list(range(22, 42))
        assert 'name="q" value="이 민수"' in page.text
        assert '<option value="VIP" selected>VIP</option>' in page.text
        assert '<option value="서울특별시" selected>서울특별시</option>' in page.text
        form = page.text.partition('role="search">')[2].partition("</form>")[0]
        assert 'name="page"' not in form
        navigation = page.text.partition('<nav aria-label="고객 목록 페이지">')[2].partition("</nav>")[0]
        links = re.findall(r'href="([^"]+)"', navigation)
        assert len(links) == 4
        for link in links:
            params = parse_qs(urlsplit(unescape(link)).query)
            assert {key: params[key] for key in filters} == {key: [value] for key, value in filters.items()}
        next_url = unescape(re.search(r'href="([^"]+)" rel="next"', navigation).group(1))
        last_page = client.get(next_url)
        assert [item["id"] for item in last_page.context["items"]] == [42, 43, 44, 45]
        assert last_page.context["total"] == 44


@pytest.mark.parametrize("filters", [
    {"grade": "신규"}, {"region": "제주특별자치도"},
    {"q": "이민수", "grade": "일반", "region": "부산광역시"},
])
def test_필터만으로도_결과없음안내_빈표없음_현재조건유지(filter_client, filters):
    response = filter_client.get("/customers", params=filters)
    assert response.status_code == 200
    assert response.context["total"] == 0
    assert response.context["items"] == []
    assert "검색 결과가 없습니다." in response.text
    assert "<table" not in response.text
    for key, value in filters.items():
        assert response.context[key] == value
    if filters.get("region"):
        region = filters["region"]
        assert f'<option value="{region}" selected>{region}</option>' in response.text


@pytest.mark.parametrize("filters", [{}, {"q": "", "grade": "", "region": ""},
                                      {"q": "  ", "grade": " ", "region": " "}])
def test_검색필터가_비면_전체표시고객목록_기존페이지네이션(filter_client, filters):
    response = filter_client.get("/customers", params=filters)
    assert response.status_code == 200
    assert response.context["total"] == 62
    assert len(response.context["items"]) == 20
    assert response.context["filters"] == {}
    assert 'href="https://testserver/customers?page=2" rel="next"' in response.text


def test_공백제거검색에서도_퍼센트와밑줄_문자그대로_비교(authenticated_client):
    client = authenticated_client
    seed_customers(client, 2)
    with client.app.state.engine.begin() as connection:
        connection.execute(text("UPDATE customer SET name='이 %민_수' WHERE id=1"))
        connection.execute(text("UPDATE customer SET name='이 XX민A수' WHERE id=2"))
    response = client.get("/customers", params={"q": "이%민_수", "region": "서울", "grade": "일반"})
    assert response.status_code == 200
    assert response.context["total"] == 1
    assert [item["id"] for item in response.context["items"]] == [1]


def test_동시편집_이전폼은_409_최초수정값보존_다시불러온후저장(authenticated_client):
    client = authenticated_client
    seed_customers(client, 1)
    first = client.get("/customers/1/edit")
    second = client.get("/customers/1/edit")
    original = dict(first.context["values"])
    version = first.context["version"]
    assert second.context["version"] == version
    assert f'name="version" value="{version}"' in first.text
    saved = client.post("/customers/1/edit", data=original | {"address": "부산", "version": version},
                        follow_redirects=False)
    assert saved.status_code == 303
    stale_values = original | {"grade": "VIP", "version": version}
    for _ in range(2):
        conflict = client.post("/customers/1/edit", data=stale_values)
        assert conflict.status_code == 409
        assert "최신 정보를 다시 불러와 주세요" in conflict.text
        assert conflict.context["values"]["grade"] == "VIP"
        assert conflict.context["version"] == version
        assert client.get("/customers/1").context["customer"]["address"] == "부산"
    latest = client.get("/customers/1/edit")
    assert latest.context["version"] != version
    merged = dict(latest.context["values"]) | {"grade": "VIP", "version": latest.context["version"]}
    assert client.post("/customers/1/edit", data=merged, follow_redirects=False).status_code == 303
    stored = client.get("/customers/1").context["customer"]
    assert stored["address"] == "부산" and stored["grade"] == "VIP"


@pytest.mark.parametrize("version", ["", "invalid-version"])
def test_버전없는_기존폼과_잘못된버전은_덮어쓰기거부(authenticated_client, version):
    client = authenticated_client
    seed_customers(client, 1)
    response = client.post("/customers/1/edit", data=CUSTOMER_INPUT | {"version": version})
    assert response.status_code == 409
    assert client.get("/customers/1").context["customer"]["name"] == "고객1"


def test_동시수정_같은버전은_한번만저장(authenticated_client):
    client = authenticated_client
    seed_customers(client, 1)
    form = client.get("/customers/1/edit")
    original = dict(form.context["values"]) | {"version": form.context["version"]}
    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(
            lambda address: client.post("/customers/1/edit", data=original | {"address": address},
                                        follow_redirects=False), ("부산", "대구"),
        ))
    assert sorted(response.status_code for response in responses) == [303, 409]


@pytest.mark.parametrize("operation", ["create", "edit", "delete"])
@pytest.mark.parametrize("lock_type", ["write", "read"])
def test_DB잠금_시작과커밋실패_503_행보존_해제후재시도(authenticated_client, operation, lock_type):
    client = authenticated_client
    seed_customers(client, 1)
    engine = client.app.state.engine
    form = client.get("/customers/1/edit")
    original = dict(form.context["values"])
    values = CUSTOMER_INPUT | {"version": form.context["version"]}
    path = "/customers" if operation == "create" else f"/customers/1/{operation}"
    with engine.connect() as connection:
        db_path = connection.execute(text("PRAGMA database_list")).first()[2]

    def short_timeout(connection, cursor, statement, parameters, context, executemany):
        if statement == "BEGIN IMMEDIATE":
            cursor.execute("PRAGMA busy_timeout=50")

    event.listen(engine, "before_cursor_execute", short_timeout)
    try:
        with closing(connect(db_path)) as blocker:
            blocker.execute("BEGIN IMMEDIATE" if lock_type == "write" else "BEGIN")
            if lock_type == "read":
                blocker.execute("SELECT * FROM customer").fetchall()
            response = client.post(path, data=values, follow_redirects=False)
            blocker.rollback()
    finally:
        event.remove(engine, "before_cursor_execute", short_timeout)
    assert response.status_code == 503
    assert response.headers["retry-after"] == "1"
    assert "잠시 후 다시 시도해 주세요" in response.text
    if operation != "delete":
        assert response.context["values"] == CUSTOMER_INPUT
        assert 'value="등록고객"' in response.text
    if operation == "edit":
        assert response.context["version"] == form.context["version"]
    with engine.connect() as connection:
        assert dict(connection.execute(text("SELECT * FROM customer")).mappings().one()) == original
        assert connection.execute(text("SELECT count(*) FROM hidden_customer")).scalar() == 0
    assert client.post(path, data=values, follow_redirects=False).status_code == 303
    assert client.get("/customers").context["total"] == {"create": 2, "edit": 1, "delete": 0}[operation]


def test_총건수와목록_동일스냅샷_조회중숨김에도_일관성유지(authenticated_client):
    client = authenticated_client
    seed_customers(client, 21)
    engine = client.app.state.engine
    with engine.connect() as connection:
        db_path = connection.execute(text("PRAGMA database_list")).first()[2]
        assert connection.execute(text("PRAGMA journal_mode=WAL")).scalar() == "wal"
    changed = []

    def hide_after_count(connection, cursor, statement, parameters, context, executemany):
        if not changed and statement.startswith("SELECT customer.id"):
            changed.append(True)
            with closing(connect(db_path)) as writer:
                with writer:
                    writer.execute("INSERT INTO hidden_customer VALUES (21)")

    event.listen(engine, "before_cursor_execute", hide_after_count)
    try:
        response = client.get("/customers?page=2")
    finally:
        event.remove(engine, "before_cursor_execute", hide_after_count)
    assert changed == [True]
    assert response.status_code == 200
    assert response.context["total"] == 21
    assert response.context["total_pages"] == 2
    assert [item["id"] for item in response.context["items"]] == [21]
    assert client.get("/customers").context["total"] == 20
    assert client.get("/customers?page=2").status_code == 404


@pytest.mark.parametrize("q", ["x" * (MAX_SEARCH_LENGTH + 1), "x" * 50001])
def test_긴검색어_SQL실행전_422로거부(authenticated_client, q):
    client = authenticated_client
    seed_customers(client, 1)
    statements = []

    def capture(connection, cursor, statement, parameters, context, executemany):
        if "FROM customer" in statement:
            statements.append(statement)

    engine = client.app.state.engine
    event.listen(engine, "before_cursor_execute", capture)
    try:
        response = client.get("/customers", params={"q": q})
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert response.status_code == 422
    assert statements == []


@pytest.mark.parametrize("character", ["x", "%", "_", "ß"])
def test_검색어길이_경계값과_이스케이프확장은_정상조회(authenticated_client, character):
    client = authenticated_client
    seed_customers(client, 1)
    response = client.get("/customers", params={"q": character * MAX_SEARCH_LENGTH})
    assert response.status_code == 200
    assert f'maxlength="{MAX_SEARCH_LENGTH}"' in response.text
