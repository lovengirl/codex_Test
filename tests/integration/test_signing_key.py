"""HTTP 없이 함수 입력과 반환값을 검증한다."""

from concurrent.futures import ThreadPoolExecutor


import pytest


from authentication import load_signing_key


ACCOUNT = {"name": "홍길동", "email": "hong@example.com", "address": "서울",
           "password": "안전한비밀번호123!"}


def test_동시초기화_동일한키_환경설정(tmp_path, monkeypatch):
    path = tmp_path / "keys.db"
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
    with ThreadPoolExecutor(max_workers=2) as executor:
        keys = list(executor.map(lambda _: load_signing_key(path), range(2)))
    assert keys[0] == keys[1]
    monkeypatch.setenv("JWT_SECRET_KEY", "configured-secret-key-" * 3)
    assert load_signing_key(path) == "configured-secret-key-" * 3
    monkeypatch.setenv("JWT_SECRET_KEY", "short")
    with pytest.raises(ValueError, match="32바이트"):
        load_signing_key(path)

