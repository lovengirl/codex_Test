"""실제 Uvicorn의 HTTPS 응답과 기존 DB 보존을 검증하고 한글 로그를 남긴다."""

from datetime import datetime, timezone, timedelta
from contextlib import closing
import hashlib
import json
from pathlib import Path
import socket
import sqlite3
import ssl
import subprocess
import sys
import tempfile
import time
from urllib.error import URLError
from urllib.request import ProxyHandler, HTTPSHandler, build_opener

import httpx

ROOT = Path(__file__).resolve().parents[1]


def snapshot():
    path = ROOT / "customer.db"
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as connection:
        schema = connection.execute(
            "SELECT name, sql FROM sqlite_master ORDER BY name"
        ).fetchall()
        counts = {}
        for name, in connection.execute("SELECT name FROM sqlite_master WHERE type='table'"):
            quoted = name.replace('"', '""')
            counts[name] = connection.execute(f'SELECT count(*) FROM "{quoted}"').fetchone()[0]
    return hashlib.sha256(path.read_bytes()).hexdigest(), schema, counts


def verify_migrated_copy(copy_path):
    """읽기 전용 원본과 비교해 칼럼 확장 후에도 기존 행·칼럼을 보존했는지 확인한다."""
    with closing(sqlite3.connect((ROOT / "customer.db").as_uri() + "?mode=ro", uri=True)) as source:
        with closing(sqlite3.connect(copy_path.as_uri() + "?mode=ro", uri=True)) as target:
            source_tables = source.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()
            assert source_tables == target.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall(), "테이블 목록이 다릅니다."
            for name, in source_tables:
                quoted = '"' + name.replace('"', '""') + '"'
                original = source.execute(f"PRAGMA table_info({quoted})").fetchall()
                migrated = target.execute(f"PRAGMA table_info({quoted})").fetchall()
                columns = [column[1] for column in original]
                if name == "employees" and "password_hash" not in columns:
                    assert migrated[:-1] == original
                    assert migrated[-1][1:4] == ("password_hash", "TEXT", 0)
                else:
                    assert migrated == original
                selected = ", ".join('"' + column.replace('"', '""') + '"' for column in columns)
                query = f"SELECT {selected} FROM {quoted} ORDER BY id"
                assert source.execute(query).fetchall() == target.execute(query).fetchall(), (
                    "기존 행이 변경되었습니다."
                )


def verify_authentication(context, report):
    account = {"name": "인증검증직원", "email": "https-auth-" + str(time.time_ns()) + "@example.com",
               "address": "검증용 주소", "password": "검증용비밀번호123!"}
    with httpx.Client(base_url="https://127.0.0.1:8000", verify=context,
                      trust_env=False, follow_redirects=False) as client:
        assert client.get("/customers").status_code == 401
        report("[통과] 토큰 없는 고객 페이지 접근 → 401")
        assert client.post("/auth/register", data=account).status_code == 303
        duplicate = client.post("/auth/register", data=account)
        assert duplicate.status_code == 409 and "이미 가입된 이메일" in duplicate.text
        report("[통과] 회원가입 및 중복 가입 한국어 안내")
        wrong = client.post("/auth/login", data=account | {"password": "wrong"})
        missing = client.post("/auth/login", data=account | {"email": "missing-" + account["email"]})
        assert wrong.status_code == missing.status_code == 401 and wrong.text == missing.text
        report("[통과] 계정 없음·비밀번호 오류의 동일한 한국어 응답")
        login = client.post("/auth/login", data=account)
        assert login.status_code == 303 and login.headers["location"] == "/customers"
        cookie = login.headers["set-cookie"].lower()
        assert all(flag in cookie for flag in ("httponly", "secure", "samesite=strict", "max-age=3600"))
        report("[통과] 로그인 및 HTTPS 전용 httpOnly JWT 쿠키, 만료 60분")
        for path in ("/", "/customers", "/dashboard", "/auth/login", "/auth/register", "/docs", "/redoc"):
            page = client.get(path)
            assert page.status_code == 200
            assert "인증검증직원님 안녕하세요" in page.text and "로그아웃" in page.text
        report("[통과] 페이지 이동 후 인증 유지 및 모든 HTML 페이지의 이름·로그아웃 표시")
        logout = client.post("/auth/logout")
        assert logout.status_code == 303 and logout.headers["location"] == "/auth/login"
        assert "Max-Age=0" in logout.headers["set-cookie"]
        assert "access_token" not in client.cookies
        assert client.get("/customers").status_code == 401
        assert client.get("/auth/login").status_code == 200
        report("[통과] 로그아웃 쿠키 삭제·로그인 화면 이동·보호 라우트 접근 차단")


def main():
    lines = []

    def report(message):
        print(message, flush=True)
        lines.append(message)

    server = None
    temporary = None
    try:
        report("검증 시각: " + datetime.now(timezone(timedelta(hours=9))).isoformat())
        report("Python 버전: " + sys.version.split()[0])
        with socket.socket() as probe:
            if probe.connect_ex(("127.0.0.1", 8000)) == 0:
                raise RuntimeError("8000 포트가 사용 중입니다. 기존 서버 종료 후 다시 실행해 주세요.")
        before = snapshot()
        temporary = tempfile.TemporaryDirectory(prefix="customer-auth-https-")
        copy_path = Path(temporary.name) / "verification.db"
        with closing(sqlite3.connect((ROOT / "customer.db").as_uri() + "?mode=ro", uri=True)) as source:
            with closing(sqlite3.connect(copy_path)) as target:
                source.backup(target)
        report("[준비] 원본 DB를 읽기 전용으로 열어 임시 검증 DB에 백업")
        context = ssl.create_default_context(cafile=str(ROOT / ".certs/localhost.crt"))
        opener = build_opener(ProxyHandler({}), HTTPSHandler(context=context))
        with (ROOT / "uvicorn-verification.log").open("w", encoding="utf-8") as output:
            server = subprocess.Popen(
                [sys.executable, "-c", "\n".join([
                    "from pathlib import Path", "import sys", "import uvicorn",
                    "from main import create_app",
                    "uvicorn.run(create_app(Path(sys.argv[1])), host='127.0.0.1', port=8000, "
                    "ssl_keyfile='.certs/localhost.key', ssl_certfile='.certs/localhost.crt')",
                ]), str(copy_path)],
                cwd=ROOT, stdout=output, stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
            try:
                deadline = time.monotonic() + 20
                while True:
                    if server.poll() is not None:
                        raise RuntimeError("Uvicorn 시작 실패: uvicorn-verification.log를 확인해 주세요.")
                    try:
                        response = opener.open("https://127.0.0.1:8000/", timeout=2)
                        with response:
                            assert response.status == 200
                            assert "Customer Manager" in response.read().decode("utf-8")
                        break
                    except URLError:
                        if time.monotonic() >= deadline:
                            raise
                        time.sleep(0.2)
                report("[통과] GET https://127.0.0.1:8000/ → 200 OK")
                for route in ("/health", "/docs", "/openapi.json"):
                    with opener.open("https://127.0.0.1:8000" + route, timeout=5) as response:
                        assert response.status == 200
                        if route == "/health":
                            assert json.load(response) == {"status": "ok", "database": "ok"}
                    report(f"[통과] GET {route} → 200 OK")
                report("[통과] 인증서 신뢰 및 127.0.0.1 호스트명 검증")
                verify_migrated_copy(copy_path)
                report("[통과] 임시 복사본의 nullable password_hash 확장 및 기존 전체 행·칼럼 보존")
                verify_authentication(context, report)
            finally:
                server.terminate()
                try:
                    server.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait()
        after = snapshot()
        assert before == after, "검증 전후 DB 내용이 다릅니다."
        report("[통과] 기존 DB 파일 SHA-256·스키마·행 수 변경 없음")
        report("DB SHA-256: " + after[0])
        report("테이블별 행 수: " + json.dumps(after[2], ensure_ascii=False))
        report("[완료] HTTPS 실행 검증 성공, 검증용 서버 종료")
    except Exception as exc:
        report(f"[실패] {type(exc).__name__}: {exc}")
        raise
    finally:
        if temporary is not None:
            temporary.cleanup()
        (ROOT / "verification.log").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
