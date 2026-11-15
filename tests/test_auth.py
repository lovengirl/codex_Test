"""임시 DB에서 인증 성공·실패와 기존 데이터 호환성을 검증한다."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta, timezone

import bcrypt
from fastapi.testclient import TestClient
import jwt
import pytest
from sqlalchemy import text

from authentication import ALGORITHM, COOKIE_NAME, load_signing_key
from database import connect
from main import create_app

ACCOUNT = {"name": "홍길동", "email": "hong@example.com", "address": "서울",
           "password": "안전한비밀번호123!"}


def signup(client, **changes):
    return client.post("/auth/register", data=ACCOUNT | changes, follow_redirects=False)


def signin(client, **changes):
    return client.post("/auth/login", data=ACCOUNT | changes, follow_redirects=False)


def test_가입_로그인_고객진입_쿠키와_비밀번호해시(client):
    response = signup(client)
    assert response.status_code == 303
    assert response.headers["location"] == "/auth/login?registered=true"
    assert "회원가입이 완료되었습니다" in client.get(response.headers["location"]).text
    with client.app.state.engine.connect() as connection:
        employee = connection.execute(text("SELECT * FROM employees")).mappings().one()
    assert employee["name"] == ACCOUNT["name"]
    assert employee["address"] == ACCOUNT["address"]
    assert employee["password_hash"] != ACCOUNT["password"]
    assert bcrypt.checkpw(ACCOUNT["password"].encode(), employee["password_hash"].encode())
    response = signin(client)
    assert response.status_code == 303
    assert response.headers["location"] == "/customers"
    cookie = response.headers["set-cookie"].lower()
    for flag in ("httponly", "secure", "samesite=strict", "max-age=3600", "path=/"):
        assert flag in cookie
    claims = jwt.decode(client.cookies[COOKIE_NAME], client.app.state.jwt_secret, algorithms=[ALGORITHM])
    assert claims["exp"] - claims["iat"] == 3600
    assert claims["sub"] == str(employee["id"])
    assert "password" not in claims
    assert client.get("/customers").status_code == 200


@pytest.mark.parametrize("path", ["/", "/customers", "/dashboard", "/auth/login",
                                 "/auth/register", "/docs", "/redoc"])
def test_모든_HTML페이지_접속자_공통헤더(client, path):
    signup(client)
    signin(client)
    response = client.get(path)
    assert response.status_code == 200
    assert "홍길동님 안녕하세요" in response.text
    assert 'action="/auth/logout"' in response.text
    assert "로그아웃" in response.text
    assert response.headers["cache-control"] == "no-store"


def test_로그아웃_쿠키삭제_로그인화면_보호라우트차단(client):
    signup(client)
    signin(client)
    response = client.post("/auth/logout", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/auth/login"
    assert "Max-Age=0" in response.headers["set-cookie"]
    assert COOKIE_NAME not in client.cookies
    page = client.get(response.headers["location"])
    assert page.status_code == 200
    assert "홍길동님 안녕하세요" not in page.text
    assert "로그아웃" not in page.text
    assert client.get("/customers").status_code == 401


def test_로그인중_가입오류_로그인오류에도_공통헤더(client):
    signup(client)
    signin(client)
    for response in (signup(client), signup(client, email="invalid"),
                     signup(client, name=""), signin(client, password="wrong")):
        assert "홍길동님 안녕하세요" in response.text
        assert "로그아웃" in response.text


@pytest.mark.parametrize("path", ["/customers", "/dashboard"])
def test_토큰없이_접근하면_401(client, path):
    assert client.get(path).status_code == 401
    assert client.get(path).json()["detail"] == "로그인이 필요합니다."


def test_계정없음_비밀번호오류_동일한_한국어응답(client):
    signup(client)
    missing = signin(client, email="missing@example.com")
    wrong = signin(client, password="틀린비밀번호")
    assert missing.status_code == wrong.status_code == 401
    assert missing.text == wrong.text
    assert "이메일 또는 비밀번호를 확인해 주세요." in wrong.text
    assert COOKIE_NAME not in client.cookies


def test_중복가입_대소문자_공백_한국어안내(client):
    signup(client)
    response = signup(client, email=" HONG@EXAMPLE.COM ")
    assert response.status_code == 409
    assert "이미 가입된 이메일입니다." in response.text
    with client.app.state.engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM employees")).scalar() == 1
    assert signin(client, email=" HONG@EXAMPLE.COM ").status_code == 303


@pytest.mark.parametrize("changes", [
    {"email": "잘못된이메일"}, {"name": " "}, {"address": " "},
    {"password": ""}, {"password": "가" * 25},
])
def test_잘못된_가입입력_한국어안내(client, changes):
    response = signup(client, **changes)
    assert response.status_code == 422
    assert "입력해 주세요" in response.text
    assert "password_hash" not in response.text


def test_bcrypt_72바이트_경계(client):
    assert signup(client, password="가" * 24).status_code == 303
    assert signin(client, password="가" * 24).status_code == 303
    client.cookies.clear()
    assert signin(client, password="가" * 25).status_code == 401


@pytest.mark.parametrize("kind", ["expired", "tampered", "wrong-key", "missing-exp",
                                 "invalid-sub", "huge-sub", "missing-user", "wrong-algorithm"])
def test_잘못된_JWT는_401(client, kind):
    signup(client)
    now = datetime.now(timezone.utc)
    claims = {"sub": "1", "iat": now, "exp": now + timedelta(hours=1)}
    key = client.app.state.jwt_secret
    algorithm = ALGORITHM
    if kind == "expired":
        claims["iat"] = now - timedelta(hours=2)
        claims["exp"] = now - timedelta(hours=1)
    elif kind == "wrong-key":
        key = "other-key-" * 6
    elif kind == "missing-exp":
        del claims["exp"]
    elif kind == "invalid-sub":
        claims["sub"] = "invalid"
    elif kind == "huge-sub":
        claims["sub"] = "9" * 19
    elif kind == "missing-user":
        claims["sub"] = "987654"
    elif kind == "wrong-algorithm":
        algorithm = "HS384"
    token = jwt.encode(claims, key, algorithm=algorithm)
    if kind == "tampered":
        token = "broken." + token
    client.cookies.set(COOKIE_NAME, token)
    assert client.get("/customers").status_code == 401


def test_기존직원_NULL해시_잘못된해시_로그인실패(client):
    with client.app.state.engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO employees (name,email,address) VALUES ('기존직원',:email,'서울')"
        ), {"email": ACCOUNT["email"]})
    assert signin(client).status_code == 401
    assert signup(client).status_code == 409
    with client.app.state.engine.begin() as connection:
        connection.execute(text("UPDATE employees SET password_hash = 'invalid'"))
    assert signin(client).status_code == 401


def test_기존_DB_칼럼추가_행보존_반복시작(tmp_path):
    path = tmp_path / "legacy.db"
    with closing(connect(path)) as connection:
        with connection:
            connection.execute("CREATE TABLE employees (id INTEGER PRIMARY KEY, name TEXT NOT NULL, "
                               "email TEXT NOT NULL UNIQUE, address TEXT NOT NULL)")
            connection.execute("INSERT INTO employees VALUES (7, '기존직원', 'old@example.com', '서울')")
        original_columns = connection.execute("PRAGMA table_info(employees)").fetchall()
    for _ in range(2):
        with TestClient(create_app(path)) as test_client:
            assert test_client.get("/health").status_code == 200
    with closing(connect(path)) as connection:
        columns = connection.execute("PRAGMA table_info(employees)").fetchall()
        assert columns[:-1] == original_columns
        assert columns[-1][1:4] == ("password_hash", "TEXT", 0)
        assert connection.execute("SELECT * FROM employees").fetchall() == [
            (7, "기존직원", "old@example.com", "서울", None)
        ]
        assert {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )} == {"customer", "product", "employees", "orders"}


def test_앱재시작_새클라이언트에서도_인증지속(tmp_path):
    path = tmp_path / "restart.db"
    with TestClient(create_app(path), base_url="https://testserver") as first:
        signup(first)
        signin(first)
        token = first.cookies[COOKIE_NAME]
    with TestClient(create_app(path), base_url="https://testserver") as second:
        second.cookies.set(COOKIE_NAME, token)
        assert second.get("/customers").status_code == 200
        assert "홍길동님 안녕하세요" in second.get("/").text


def test_동시가입_한계정만_생성(client):
    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(lambda _: signup(client), range(2)))
    assert sorted(response.status_code for response in responses) == [303, 409]


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


@pytest.mark.parametrize("path", ["/auth/login", "/auth/register", "/auth/logout"])
def test_외부출처_POST_거부(client, path):
    response = client.post(path, data=ACCOUNT, headers={"Origin": "https://other.example"})
    assert response.status_code == 403


def test_이름_HTML이스케이프(client):
    signup(client, name="<script>alert(1)</script>")
    signin(client)
    page = client.get("/")
    assert "<script>alert(1)</script>" not in page.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page.text


def test_HTTP에서는_보안쿠키_전송차단(client):
    signup(client)
    signin(client)
    assert client.get("http://testserver/customers").status_code == 401
