"""실제 임시 SQLite와 SQL 생성·검증·실행 모듈의 결합을 검증한다."""

from contextlib import closing
import sqlite3

import pytest

from database import connect, initialize_database
import natural_language as nl


@pytest.fixture
def query_connection(tmp_path):
    path = tmp_path / 'queries.db'
    initialize_database(path)
    with closing(connect(path)) as connection:
        with connection:
            connection.execute("ALTER TABLE customer ADD COLUMN registered_at TEXT")
            connection.execute("INSERT INTO employees VALUES (1, '질문직원', 'ask@example.com', '서울', 'test-hash')")
            connection.executemany("INSERT INTO customer VALUES (?, ?, ?, ?, ?, ?)", [
                (1, '고객1', 'one@example.com', '서울', '신규', '2026-01-01'),
                (2, '숨김고객', 'two@example.com', '부산', 'VIP', '2026-02-01'),
                (3, '기존고객', 'three@example.com', '서울', '일반', '2025-12-31'),
                (4, '등록일모름', 'four@example.com', '서울', '일반', None),
            ])
            connection.execute("INSERT INTO hidden_customer VALUES (2)")
            connection.execute("INSERT INTO product VALUES (1, '상품', '식품', 1000)")
            connection.executemany("INSERT INTO orders VALUES (?, ?, 1, 1, 1, ?, ?, ?)", [
                (1, 1, 3000, '2026-11-01', '완료'), (2, 1, 4000, '2026-11-30', '배송중'),
                (3, 1, 8000, '2026-11-10', '취소'), (4, 2, 9000, '2026-11-10', '완료'),
                (5, 3, 1000, '2026-12-01', '완료'),
            ])
    with closing(connect(path, readonly=True)) as connection:
        nl.prepare_visible_views(connection, nl.actual_schema(connection))
        yield connection


@pytest.mark.parametrize("sql", [
    " select 1 AS 결과 ", " \n; /* 시작 */ select 1 AS 결과; -- 끝\n; ",
    "-- SELECT 설명\nSeLeCt 1 AS 결과;", "WITH value AS (SELECT 1 AS 결과) SELECT 결과 FROM value",
    "SELECT 1 AS 결과 /* INSERT UPDATE DELETE DROP */",
])
def test_정상SELECT_공백_세미콜론_주석_소문자_CTE허용(query_connection, sql):
    assert nl.execute_select(query_connection, sql)["rows"] == [(1,)]


def test_문자열과인용식별자의_변경키워드와_주석기호는허용(query_connection):
    result = nl.execute_select(query_connection, "SELECT 'DELETE; -- /* DROP */' AS \"UPDATE\"")
    assert result["rows"] == [("DELETE; -- /* DROP */",)]


@pytest.mark.parametrize("sql", [
    "INSERT INTO customer VALUES (9,'x','x','x','일반',NULL)", "UPDATE customer SET name='x'",
    "DELETE FROM orders", "DROP TABLE customer", "ATTACH ':memory:' AS other", "PRAGMA query_only=OFF",
    "WITH x AS (SELECT 1) DELETE FROM orders", "SELECT 1; DELETE FROM orders",
    "SELECT load_extension('x')",
])
def test_SELECT외구문_복수문장과_뷰우회차단_원본보존(query_connection, sql):
    before = query_connection.execute("SELECT COUNT(*) FROM main.orders").fetchone()
    with pytest.raises((ValueError, sqlite3.Error)):
        nl.execute_select(query_connection, sql)
    assert query_connection.execute("SELECT COUNT(*) FROM main.orders").fetchone() == before


def test_뷰로숨김고객과_취소주문을_모델SQL과무관하게제외(query_connection):
    assert nl.execute_select(query_connection, "SELECT COUNT(*) FROM customer")["rows"] == [(3,)]
    assert nl.execute_select(query_connection, "SELECT SUM(total_amount) FROM orders")["rows"] == [(8000,)]


@pytest.mark.parametrize("sql,value", [
    ('SELECT COUNT(*) FROM main.customer', 3),
    ('SELECT COUNT(*) FROM "main"."customer"', 3),
    ('SELECT SUM(main.orders.total_amount) FROM main.orders', 8000),
])
def test_스키마접두사SELECT도_오탐없이_숨김과취소제외(query_connection, sql, value):
    result = nl.execute_select(query_connection, sql)
    assert result["rows"] == [(value,)]
    assert "temp" in result["executed_sql"]


def test_결과행수제한과_과도한쿼리시간제한(query_connection, monkeypatch):
    sql = "WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM n WHERE x<1000) SELECT x FROM n"
    result = nl.execute_select(query_connection, sql)
    assert len(result["rows"]) == 200
    assert result["truncated"]
    monkeypatch.setattr(nl, "QUERY_TIMEOUT", -1)
    with pytest.raises(sqlite3.OperationalError, match="interrupted"):
        nl.execute_select(query_connection, sql)


@pytest.mark.parametrize("sql", [
    "SELECT withdrawn_at FROM customer",
    'SELECT "withdrawn_at" FROM customer',
    "SELECT COUNT(*) FROM withdrawn_customer",
    "SELECT c.id FROM customer c WHERE c.deleted_at IS NOT NULL",
    "WITH withdrawn AS (SELECT withdrawn_at FROM customer) SELECT COUNT(*) FROM withdrawn",
    "SELECT (SELECT MAX(ended_at) FROM orders) FROM customer LIMIT 1",
])
def test_가상칼럼과테이블은_데이터조회실행전에차단(query_connection, sql):
    statements = []
    query_connection.set_trace_callback(statements.append)
    try:
        with pytest.raises(nl.SchemaValidationError):
            nl.execute_select(query_connection, sql)
    finally:
        query_connection.set_trace_callback(None)
    assert sql not in statements
    print(f"[통과] 가상 테이블·칼럼 사전 차단, 데이터 조회 미실행: {sql}")


@pytest.mark.parametrize("sql,expected", [
    ('SELECT c.id AS 고객번호 FROM customer c ORDER BY 고객번호 LIMIT 1', [(1,)]),
    ('WITH x AS (SELECT id AS 고객번호 FROM customer) SELECT 고객번호 FROM x ORDER BY 고객번호 LIMIT 1', [(1,)]),
    ('SELECT c.id FROM customer c WHERE EXISTS (SELECT 1 FROM orders o WHERE o.customer_id=c.id) ORDER BY c.id LIMIT 1', [(1,)]),
    ('SELECT c.id FROM customer c JOIN orders o ON o.customer_id=c.id GROUP BY c.id HAVING COUNT(*)>1', [(1,)]),
])
def test_스키마검증은_별칭_CTE_상관서브쿼리_집계오탐없이허용(query_connection, sql, expected):
    assert nl.execute_select(query_connection, sql)["rows"] == expected


def test_사전검증은_조회함수도실행하지않음(query_connection):
    calls = []
    query_connection.create_function("검증확인", 1, lambda value: calls.append(value) or value)
    query_connection.set_authorizer(nl.select_authorizer)
    try:
        nl.validate_sql_schema(query_connection, "SELECT 검증확인(id) FROM customer")
        assert calls == []
    finally:
        query_connection.set_authorizer(None)


@pytest.mark.parametrize("sql", [
    "SELECT email, password_hash FROM employees",
    "SELECT * FROM employees",
    "SELECT substr(e.password_hash,1,4) AS 해시일부 FROM employees e",
    "WITH staff AS (SELECT password_hash AS 비밀값 FROM employees) SELECT 비밀값 FROM staff",
    "SELECT CASE WHEN password_hash!='' THEN 1 ELSE 0 END FROM employees",
    "SELECT COUNT(password_hash) FROM employees",
    'SELECT e."PASSWORD_HASH" FROM "EMPLOYEES" e',
])
def test_직원인증정보는_별칭_가공_CTE_집계로도조회차단(query_connection, sql):
    with pytest.raises(sqlite3.DatabaseError, match="password_hash"):
        nl.execute_select(query_connection, sql)


def test_직원인증정보차단후에도_직원수와업무정보조회는정상(query_connection):
    with pytest.raises(sqlite3.DatabaseError):
        nl.execute_select(query_connection, "SELECT password_hash FROM employees")
    assert nl.execute_select(query_connection, "SELECT COUNT(*) AS 직원수 FROM employees")["rows"] == [(1,)]
    assert nl.execute_select(query_connection, "SELECT name, email FROM employees")["rows"] == [
        ("질문직원", "ask@example.com"),
    ]


@pytest.mark.parametrize('reply,expected', [
    ({'sql': 'SELECT 1', 'explanation': None}, 'explanation 항목은 한국어 문자열'),
    ({'sql': None, 'explanation': '기록 없음', 'suggestion': []}, 'suggestion 항목은 한국어 문자열'),
])
def test_환각_모델JSON필드타입오류는_조회없이실제예외표시(query_connection, monkeypatch, reply, expected):
    monkeypatch.setattr(nl, 'generate_sql', lambda *args: reply)
    path = query_connection.execute('PRAGMA database_list').fetchone()[2]
    result = nl.ask_database(path, '고객 조회')
    assert result['rows'] == []
    assert result['error'].startswith('ValueError:')
    assert expected in result['error']


@pytest.mark.parametrize('sql,rows', [
    ('SELECT id FROM customer ORDER BY id', [(1,), (3,), (4,)]),
    ('SELECT id FROM customer WHERE id < 0', []),
])
def test_여러행과빈결과_한국어안내및숨김조건유지(query_connection, monkeypatch, sql, rows):
    monkeypatch.setattr(nl, 'generate_sql', lambda *args: {'sql': sql, 'explanation': '표시 고객 조회'})
    path = query_connection.execute('PRAGMA database_list').fetchone()[2]
    result = nl.ask_database(path, '고객 조회')
    assert result['error'] is None
    assert result['rows'] == rows
    assert result['answer'] == f'조회 결과 {len(rows)}개 행을 표시합니다.'

