"""임시 DB에서 자연어 질문의 스키마·SQL 안전성·한국어 결과를 검증한다."""

from contextlib import closing
from datetime import datetime
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from authentication import COOKIE_NAME, create_token
from database import connect
import natural_language as nl


@pytest.fixture
def question_client(client):
    with client.app.state.engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO employees VALUES (1, '질문직원', 'ask@example.com', '서울', 'test-hash')"
        ))
        connection.execute(text("ALTER TABLE customer ADD COLUMN registered_at TEXT"))
        connection.execute(text(
            "INSERT INTO customer VALUES "
            "(1, '고객1', 'one@example.com', '서울', '신규', '2026-01-01'), "
            "(2, '숨김고객', 'two@example.com', '부산', 'VIP', '2026-02-01'), "
            "(3, '기존고객', 'three@example.com', '서울', '일반', '2025-12-31'), "
            "(4, '등록일모름', 'four@example.com', '서울', '일반', NULL)"
        ))
        connection.execute(text("INSERT INTO hidden_customer VALUES (2)"))
        connection.execute(text("INSERT INTO product VALUES (1, '상품', '식품', 1000)"))
        connection.execute(text(
            "INSERT INTO orders VALUES "
            "(1, 1, 1, 1, 1, 3000, '2026-11-01', '완료'), "
            "(2, 1, 1, 1, 1, 4000, '2026-11-30', '배송중'), "
            "(3, 1, 1, 1, 1, 8000, '2026-11-10', '취소'), "
            "(4, 2, 1, 1, 1, 9000, '2026-11-10', '완료'), "
            "(5, 3, 1, 1, 1, 1000, '2026-12-01', '완료')"
        ))
    client.cookies.set(COOKIE_NAME, create_token(1, client.app.state.jwt_secret))
    return client


def database_path(client):
    with client.app.state.engine.connect() as connection:
        return Path(connection.execute(text("PRAGMA database_list")).first()[2])


@pytest.fixture
def query_connection(question_client):
    with closing(connect(database_path(question_client), readonly=True)) as connection:
        nl.prepare_visible_views(connection, nl.actual_schema(connection))
        yield connection


def mock_model(monkeypatch, sql, explanation="실제 DB를 집계했습니다."):
    monkeypatch.setattr(nl, "generate_sql", lambda question, schema: {"sql": sql, "explanation": explanation})


@pytest.mark.parametrize("question,sql,value", [
    ("2026년 신규 고객수", "SELECT COUNT(*) AS 신규고객수 FROM customer "
     "WHERE registered_at >= '2026-01-01' AND registered_at < '2027-01-01'", 1),
    ("이번 달 매출", "SELECT COALESCE(SUM(total_amount),0) AS 매출 FROM orders "
     "WHERE order_date >= '2026-11-01' AND order_date < '2026-12-01'", 7000),
])
def test_질문_POST_한국어답변_SQL과실제숫자표시(question_client, monkeypatch, question, sql, value):
    mock_model(monkeypatch, sql)
    response = question_client.post("/dashboard/ask", data={"question": question})
    assert response.status_code == 200
    result = response.context["question_result"]
    assert result["error"] is None
    assert result["rows"] == [(value,)]
    assert f"조회 결과는 {value:,}입니다." in response.text
    assert question in response.text
    assert "생성된 SQL" in response.text
    assert "gpt-5-mini" in response.text
    assert 'action="/dashboard/ask"' in response.text
    assert response.context["metrics"]["customer_count"] == 3
    assert response.headers["cache-control"] == "no-store"
    print(f"[통과] {question} → 실제 임시 DB 결과 {value:,}, 한국어 답변 및 SQL 표시")


def test_전체런타임스키마와_정확한ChatCompletions인자(question_client, monkeypatch):
    with question_client.app.state.engine.begin() as connection:
        connection.execute(text('CREATE TABLE "추가 테이블" (id INTEGER, "새 칼럼" TEXT, 복제 TEXT GENERATED ALWAYS AS ("새 칼럼") VIRTUAL)'))
    with closing(connect(database_path(question_client), readonly=True)) as connection:
        schema = nl.actual_schema(connection)
        actual = connection.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
    assert [item["name"] for item in schema] == [row[0] for row in actual]
    assert {item["name"] for item in schema[-1]["columns"]} == {"id", "새 칼럼", "복제"}
    captured = []

    class FakeOpenAI:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def create(self, **kwargs):
            captured.append(kwargs)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
                content=json.dumps({"sql": "SELECT 1 AS 결과", "explanation": "조회 기준"}),
            ))])

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(nl, "OpenAI", FakeOpenAI)
    result = nl.generate_sql("2026년 신규 고객수", schema)
    assert result["sql"] == "SELECT 1 AS 결과"
    kwargs = captured[0]
    assert kwargs["model"] == "gpt-5-mini"
    assert kwargs["response_format"] == {"type": "json_object"}
    assert [item["role"] for item in kwargs["messages"]] == ["system", "user"]
    system = kwargs["messages"][0]["content"]
    assert json.dumps(schema, ensure_ascii=False) in system
    for name in ("registered_at", "order_date", "total_amount", "password_hash", "새 칼럼", "복제"):
        assert name in system
    assert datetime.now(nl.KOREA_TIME).date().isoformat() in system
    print("[통과] 전체 실제 테이블·칼럼·추가 등록일·주문일·금액을 Chat Completions에 전달")


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


def test_읽기전용URI는_검사없이도_원본쓰기를차단(question_client):
    with closing(connect(database_path(question_client), readonly=True)) as connection:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            connection.execute("UPDATE customer SET name='변경'")


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


@pytest.mark.parametrize("content", [
    '{"sql":"SELECT 1", "explanation":"조회"}',
    '```json\n{"sql":"SELECT 1", "explanation":"조회"}\n```',
    '잡텍스트 {잘못된 앞부분} {"sql":"SELECT 1", "explanation":"조회"} 뒤 텍스트',
])
def test_JSON_코드펜스와_잡텍스트제거(content):
    assert nl.parse_model_json(content)["sql"] == "SELECT 1"


def test_JSON파싱불가_원문콘솔로그(caplog):
    with pytest.raises(ValueError):
        nl.parse_model_json("원문 응답 그대로 기록")
    assert "원문 응답 그대로 기록" in caplog.text


def test_생성예외타입과_메시지를_화면과콘솔에표시(question_client, monkeypatch, caplog):
    def raise_error(question, schema):
        raise TypeError("실제 예외 <원문> 그대로")

    monkeypatch.setattr(nl, "generate_sql", raise_error)
    response = question_client.post("/dashboard/ask", data={"question": "이번 달 매출"})
    assert response.context["question_result"]["error"] == "TypeError: 실제 예외 <원문> 그대로"
    assert "TypeError: 실제 예외 &lt;원문&gt; 그대로" in response.text
    assert "TypeError: 실제 예외 <원문> 그대로" in caplog.text


def test_SQL예외도_생성SQL과함께노출(question_client, monkeypatch, caplog):
    mock_model(monkeypatch, "SELECT 없는칼럼 FROM customer")
    response = question_client.post("/dashboard/ask", data={"question": "조회"})
    assert "OperationalError: no such column: 없는칼럼" in response.text
    assert "SELECT 없는칼럼 FROM customer" in response.text
    assert "OperationalError: no such column: 없는칼럼" in caplog.text


def test_실제등록일없음은_다른날짜로대체하거나_0명으로위장하지않음(client, monkeypatch):
    captured = []

    def missing_column(question, schema):
        captured.extend(schema)
        return {"sql": None, "explanation": "고객 등록일 칼럼이 실제 DB에 없어 계산할 수 없습니다."}

    monkeypatch.setattr(nl, "generate_sql", missing_column)
    result = nl.ask_database(database_path(client), "2026년 신규 고객수")
    assert "등록일 칼럼이 실제 DB에 없어" in result["answer"]
    assert result["rows"] == []
    assert {item["name"] for item in next(item for item in captured if item["name"] == "customer")["columns"]} == {
        "id", "name", "email", "address", "grade",
    }


def test_결과행수제한과_과도한쿼리시간제한(query_connection, monkeypatch):
    sql = "WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM n WHERE x<1000) SELECT x FROM n"
    result = nl.execute_select(query_connection, sql)
    assert len(result["rows"]) == 200
    assert result["truncated"]
    monkeypatch.setattr(nl, "QUERY_TIMEOUT", -1)
    with pytest.raises(sqlite3.OperationalError, match="interrupted"):
        nl.execute_select(query_connection, sql)


def test_빈질문과_긴질문은_LLM호출전_입력오류(question_client, monkeypatch):
    def unexpected_call(*args):
        pytest.fail("잘못된 입력으로 LLM을 호출했습니다.")

    monkeypatch.setattr(nl, "generate_sql", unexpected_call)
    for question in ("   ", "가" * 2001):
        response = question_client.post("/dashboard/ask", data={"question": question})
        assert "ValueError:" in response.text


def test_인증과_출처검사필수(client, question_client):
    # 두 fixture는 같은 client 인스턴스를 사용하므로 쿠키를 명시적으로 제거한다.
    client.cookies.clear()
    assert client.post("/dashboard/ask", data={"question": "질문"},
                       headers={"Accept": "application/json"}).status_code == 401
    client.cookies.set(COOKIE_NAME, create_token(1, client.app.state.jwt_secret))
    assert client.post("/dashboard/ask", data={"question": "질문"},
                       headers={"Origin": "https://other.example"}).status_code == 403


def test_환경파일경로는_작업디렉터리에독립(monkeypatch, tmp_path):
    env = tmp_path / ".env"
    env.write_text("OPENAI_API_KEY=local-test-key\n", encoding="utf-8")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(nl, "ENV_PATH", env)
    def check_key(**kwargs):
        assert kwargs["api_key"] == "local-test-key"
        raise RuntimeError("환경파일 로딩 확인")
    monkeypatch.setattr(nl, "OpenAI", check_key)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(RuntimeError, match="환경파일 로딩 확인"):
        nl.generate_sql("질문", [])


def test_환경파일수정은_다음질문에_즉시반영(monkeypatch, tmp_path):
    env = tmp_path / ".env"
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(nl, "ENV_PATH", env)
    keys = []
    def check_key(**kwargs):
        keys.append(kwargs["api_key"])
        raise RuntimeError("환경파일 재조회 확인")
    monkeypatch.setattr(nl, "OpenAI", check_key)
    for value in ("first-test-key", "second-test-key"):
        env.write_text(f"OPENAI_API_KEY={value}\n", encoding="utf-8")
        with pytest.raises(RuntimeError, match="환경파일 재조회 확인"):
            nl.generate_sql("질문", [])
    assert keys == ["first-test-key", "second-test-key"]


def test_키미설정은_설정경로와실제예외표시(monkeypatch, tmp_path, question_client, caplog):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(nl, "ENV_PATH", tmp_path / "missing.env")
    response = question_client.post("/dashboard/ask", data={"question": "이번 달 매출"})
    expected = "ValueError: 프로젝트 .env 또는 앱 환경에 OPENAI_API_KEY가 설정되어 있지 않습니다."
    assert expected in response.text
    assert expected in caplog.text


def test_스칼라NULL은_한국어로표시(question_client, monkeypatch):
    mock_model(monkeypatch, "SELECT NULL AS 결과")
    response = question_client.post("/dashboard/ask", data={"question": "조회"})
    assert "조회 결과는 값 없음입니다." in response.text
    assert "<td>값 없음</td>" in response.text
