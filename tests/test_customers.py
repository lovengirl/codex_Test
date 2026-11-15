"""임시 DB에서 고객 목록·숨김 제외·페이지 경계·응답 시간을 검증한다."""

from contextlib import closing
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
