"""환경 파일과 모델 설정의 결합을 검증한다."""

import pytest
import natural_language as nl


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

