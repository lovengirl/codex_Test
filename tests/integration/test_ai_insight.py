"""추가 SQL·대기 없이 생성하는 Insight의 수치 근거와 실패 격리를 검증한다."""

from datetime import datetime, timedelta, timezone
import json
from time import perf_counter
from types import SimpleNamespace

import jwt
import pytest
from sqlalchemy import event, text

import ai_insight
from authentication import ALGORITHM, COOKIE_NAME, create_token
from routers import dashboard


ACTION = "총매출 {{total_sales}}을 참고해 고객의 주문 내역과 후속 상담 대상을 점검하세요."
FACTS = {"customer_count": 100, "order_count": 200, "total_sales": 100000, "average_order": 500}


@pytest.fixture
def insight_client(client):
    with client.app.state.engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO employees VALUES (1, '요약직원', 'insight@example.com', '서울', 'test-hash')"
        ))
    client.cookies.set(COOKIE_NAME, create_token(1, client.app.state.jwt_secret))
    return client


def model_reply(monkeypatch, content=None, error=None):
    calls = []

    class FakeAsyncOpenAI:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def create(self, **kwargs):
            calls.append(kwargs)
            if error:
                raise error
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])

    monkeypatch.setenv("OPENAI_API_KEY", "insight-test-key")
    monkeypatch.setattr(ai_insight, "AsyncOpenAI", FakeAsyncOpenAI)
    return calls


def test_대시보드응답은_LLM을기다리지않고_기존조회만실행(insight_client, monkeypatch):
    async def unexpected(*args):
        pytest.fail("대시보드 응답 중 LLM을 호출했습니다.")

    monkeypatch.setattr(dashboard, "generate_insight", unexpected)
    statements = []
    def capture(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement)
    engine = insight_client.app.state.engine
    event.listen(engine, "before_cursor_execute", capture)
    try:
        started = perf_counter()
        response = insight_client.get("/dashboard")
        elapsed = perf_counter() - started
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert response.status_code == 200 and elapsed < 1
    assert len([sql for sql in statements if sql.lstrip().upper().startswith(("SELECT", "WITH"))]) == 4
    assert 'aria-live="polite" hidden' in response.text
    assert "fetch('/dashboard/insight'" in response.text
    assert "CSV 다운로드" in response.text and "월별 매출" in response.text
    assert response.context["insight_snapshot"]
    print(f"[통과] 기존 인증·집계 SELECT 4회 유지, LLM 대기 없이 대시보드 {elapsed:.3f}초")


def test_Insight는_서명된기존집계만전달하고_SQL은한건도추가하지않음(insight_client, monkeypatch):
    calls = model_reply(monkeypatch, json.dumps({"action": ACTION}, ensure_ascii=False))
    client = insight_client
    snapshot = client.get("/dashboard").context["insight_snapshot"]
    statements = []
    def capture(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement)
    event.listen(client.app.state.engine, "before_cursor_execute", capture)
    try:
        response = client.post("/dashboard/insight", json={"snapshot": snapshot})
    finally:
        event.remove(client.app.state.engine, "before_cursor_execute", capture)
    assert response.status_code == 200
    assert statements == []
    sentences = response.json()["sentences"]
    assert len(sentences) == 3
    assert "주간 비교 집계값이 없어" in sentences[0]
    assert "고객별 매출 집계값이 없어" in sentences[1]
    assert "총매출 0원" in sentences[2]
    assert len(calls) == 1
    assert json.loads(calls[0]["messages"][1]["content"]) == {
        "customer_count": 0, "order_count": 0, "total_sales": 0, "average_order": 0,
    }
    assert calls[0]["response_format"] == {"type": "json_object"}
    assert response.headers["cache-control"] == "no-store"
    print("[통과] Insight 인증·생성 중 SQL 0회, 입력 수치만 인용한 한국어 세 문장")


@pytest.mark.parametrize("content,error", [
    ('{"action":"매출을 99% 높이세요."}', None),
    ('{"action":"{{invented}}을 확인하세요."}', None),
    ('올바른 JSON이 아닙니다.', None),
    (None, TimeoutError("모델 시간 초과")),
    (None, RuntimeError("모델 호출 오류")),
])
def test_생성오류와환각은_204로숨기고_서비스는정상(insight_client, monkeypatch, content, error):
    model_reply(monkeypatch, content=content, error=error)
    client = insight_client
    snapshot = client.get("/dashboard").context["insight_snapshot"]
    response = client.post("/dashboard/insight", json={"snapshot": snapshot})
    assert response.status_code == 204 and response.content == b""
    page = client.get("/dashboard")
    assert page.status_code == 200
    assert 'aria-live="polite" hidden' in page.text
    assert 'name="question"' in page.text and "PDF 다운로드" in page.text
    assert client.get("/dashboard/export/csv").status_code == 200


def test_키가없어도_나머지대시보드는정상(insight_client, monkeypatch, tmp_path):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(ai_insight, "ENV_PATH", tmp_path / ".env")
    page = insight_client.get("/dashboard")
    response = insight_client.post("/dashboard/insight", json={"snapshot": page.context["insight_snapshot"]})
    assert page.status_code == 200 and response.status_code == 204


@pytest.mark.parametrize("mode", ["로그인없음", "다른사용자", "서명변조", "만료", "다른용도"])
def test_집계와로그인서명이_유효한본인만사용(insight_client, monkeypatch, mode):
    calls = model_reply(monkeypatch, json.dumps({"action": ACTION}))
    client = insight_client
    key = client.app.state.jwt_secret
    snapshot = ai_insight.make_snapshot(FACTS, 1, key)
    if mode == "로그인없음":
        client.cookies.clear()
    elif mode == "다른사용자":
        client.cookies.set(COOKIE_NAME, create_token(2, key))
    elif mode == "서명변조":
        snapshot = ai_insight.make_snapshot(FACTS, 1, "different-signing-key-32-characters")
    else:
        payload = jwt.decode(snapshot, key, algorithms=[ALGORITHM])
        if mode == "만료":
            payload["exp"] = datetime.now(timezone.utc) - timedelta(seconds=1)
        else:
            payload["purpose"] = "other"
        snapshot = jwt.encode(payload, key, algorithm=ALGORITHM)
    response = client.post("/dashboard/insight", json={"snapshot": snapshot})
    assert response.status_code == 401
    assert calls == []


def test_외부출처의생성요청차단(insight_client):
    page = insight_client.get("/dashboard")
    response = insight_client.post("/dashboard/insight", json={"snapshot": page.context["insight_snapshot"]},
                                   headers={"Origin": "https://other.example"})
    assert response.status_code == 403


