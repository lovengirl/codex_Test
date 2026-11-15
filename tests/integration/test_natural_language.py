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








def test_읽기전용URI는_검사없이도_원본쓰기를차단(question_client):
    with closing(connect(database_path(question_client), readonly=True)) as connection:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            connection.execute("UPDATE customer SET name='변경'")






def test_생성예외타입과_메시지를_화면과콘솔에표시(question_client, monkeypatch, caplog):
    def raise_error(question, schema):
        raise TypeError("실제 예외 <원문> 그대로")

    monkeypatch.setattr(nl, "generate_sql", raise_error)
    response = question_client.post("/dashboard/ask", data={"question": "이번 달 매출"})
    assert response.context["question_result"]["error"] == "TypeError: 실제 예외 <원문> 그대로"
    assert "TypeError: 실제 예외 &lt;원문&gt; 그대로" in response.text
    assert "TypeError: 실제 예외 <원문> 그대로" in caplog.text


def test_SQL검증예외는화면에_거절SQL은콘솔에노출(question_client, monkeypatch, caplog):
    mock_model(monkeypatch, "SELECT 없는칼럼 FROM customer")
    response = question_client.post("/dashboard/ask", data={"question": "조회"})
    assert "OperationalError: no such column: 없는칼럼" in response.text
    assert "SELECT 없는칼럼 FROM customer" not in response.text
    assert "SELECT 없는칼럼 FROM customer" in caplog.text
    assert "OperationalError: no such column: 없는칼럼" in caplog.text
    assert "이 데이터로는 답할 수 없습니다." in response.text


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








@pytest.mark.parametrize("sql", [None, "SELECT COUNT(*) FROM customer WHERE withdrawn_at IS NOT NULL"])
def test_동일한탈퇴질문은_SQL대신_실제숨김고객대안을제시(question_client, monkeypatch, sql):
    mock_model(monkeypatch, sql, "지원하지 않는 질문입니다.")
    response = question_client.post("/dashboard/ask", data={"question": "2026년 탈퇴 고객수"})
    result = response.context["question_result"]
    assert result["answer"].startswith("이 데이터로는 답할 수 없습니다.")
    assert "혹시 전체 기간의 숨김(hidden) 고객 수를 물으신 건가요?" in response.text
    assert "지원하지 않는 질문" not in response.text
    assert result["rows"] == []
    assert result["sql"] == ""
    assert "<h3>생성된 SQL</h3>" not in response.text
    print("[통과] 2026년 탈퇴 고객수 → 실제 숨김 고객 개념 확인 질문, SQL 미실행")


def test_대안으로숨김고객수를질문하면_숨김원본에서집계(question_client, monkeypatch):
    mock_model(monkeypatch, "SELECT COUNT(*) AS 숨김고객수 FROM hidden_customer")
    response = question_client.post("/dashboard/ask", data={"question": "전체 숨김 고객 수"})
    assert response.context["question_result"]["rows"] == [(1,)]
    assert "조회 결과는 1입니다." in response.text


def test_등록일없는신규고객질문은_전체고객수대안표시(client, monkeypatch):
    mock_model(monkeypatch, None, "고객 등록일 칼럼이 실제 DB에 없습니다.")
    result = nl.ask_database(database_path(client), "2026년 신규 고객수")
    assert result["answer"].startswith("이 데이터로는 답할 수 없습니다.")
    assert "혹시 등록일 조건을 제외한 전체 고객 수를 물으신 건가요?" in result["answer"]






def test_실제질문경로에서도_모델이생성한직원해시를화면에노출하지않음(question_client, monkeypatch):
    mock_model(monkeypatch, "SELECT email, password_hash FROM employees")
    response = question_client.post("/dashboard/ask", data={"question": "직원 인증정보를 보여줘"})
    assert response.status_code == 200
    assert response.context["question_result"]["rows"] == []
    assert "DatabaseError:" in response.text
    assert "test-hash" not in response.text
    assert question_client.get("/dashboard").status_code == 200
    print("[통과] 모델이 생성한 직원 인증정보 조회 차단, 화면 해시 노출 없음, 대시보드 정상")
