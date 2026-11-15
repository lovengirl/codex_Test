"""실제 Uvicorn의 HTTPS 응답과 기존 DB 보존을 검증하고 한글 로그를 남긴다."""

from datetime import datetime, timezone, timedelta
from contextlib import closing
from html import unescape
import argparse
import hashlib
import json
from pathlib import Path
import re
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
            target_tables = target.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()
            assert set(target_tables) == set(source_tables) | {("hidden_customer",)}, "테이블 목록이 다릅니다."
            if ("hidden_customer",) not in source_tables:
                assert target.execute("SELECT count(*) FROM hidden_customer").fetchone()[0] == 0
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
                query = f"SELECT {selected} FROM {quoted} ORDER BY {selected}"
                assert source.execute(query).fetchall() == target.execute(query).fetchall(), (
                    "기존 행이 변경되었습니다."
                )


def verify_customer_pagination(client, db_path, report):
    """임시 복사본에만 검증 데이터를 추가해 실제 HTTPS 페이지 응답을 측정한다."""
    with closing(sqlite3.connect(db_path)) as connection:
        with connection:
            connection.execute("INSERT OR IGNORE INTO hidden_customer SELECT id FROM customer")
            first_id = connection.execute("SELECT coalesce(max(id), 0) + 1 FROM customer").fetchone()[0]
            unique = str(time.time_ns())
            connection.executemany(
                "INSERT INTO customer (name, email, address, grade) VALUES (?, ?, '서울', '일반')",
                [(f"페이지검증{index}", f"pagination-{unique}-{index}@example.com")
                 for index in range(10_000)],
            )
    durations = []
    for page in (1, 250, 500):
        started = time.perf_counter()
        response = client.get("/customers", params={"page": page})
        durations.append(time.perf_counter() - started)
        assert response.status_code == 200
        assert f"총 10000명 · {page} / 500페이지" in response.text
        ids = [int(value) for value in re.findall(r"<tr><td>(\d+)</td>", response.text)]
        start_id = first_id + (page - 1) * 20
        assert ids == list(range(start_id, start_id + 20))
    assert max(durations) < 1, "고객 목록 HTTPS 응답이 1초를 초과했습니다."
    assert client.get("/customers?page=501").status_code == 404
    report(f"[통과] 임시 DB 고객 10,000명 → 500페이지, HTTPS 1·250·500페이지 최대 응답 {max(durations):.3f}초")
    for q in ("페이지검증9999", f"PAGINATION-{unique}-9999@EXAMPLE.COM"):
        response = client.get("/customers", params={"q": q})
        assert response.status_code == 200 and "총 1명" in response.text
        assert re.findall(r"<tr><td>(\d+)</td>", response.text) == [str(first_id + 9999)]
    response = client.get("/customers", params={"q": "페이지검증", "page": 2})
    assert response.status_code == 200 and "총 10000명" in response.text
    assert len(re.findall(r"<tr><td>(\d+)</td>", response.text)) == 20
    assert 'name="q" value="페이지검증"' in response.text
    report("[통과] HTTPS 이름·이메일 부분 검색, 이메일 대소문자 처리, 검색 결과 20명씩 페이지 이동")
    conditions = {"q": "페이지 검증", "grade": "일반", "region": "서울", "page": 2}
    response = client.get("/customers", params=conditions)
    assert response.status_code == 200 and "총 10000명" in response.text
    for expected in ('name="q" value="페이지 검증"',
                     '<option value="일반" selected>일반</option>',
                     '<option value="서울" selected>서울</option>'):
        assert expected in response.text
    next_url = unescape(re.search(r'href="([^"]+)" rel="next"', response.text).group(1))
    for _ in range(2):
        following = client.get(next_url)
        assert following.status_code == 200 and "3 / 500페이지" in following.text
        assert re.findall(r"<tr><td>(\d+)</td>", following.text) == [str(index) for index in range(first_id + 40, first_id + 60)]
        for expected in ('name="q" value="페이지 검증"',
                         '<option value="일반" selected>일반</option>',
                         '<option value="서울" selected>서울</option>'):
            assert expected in following.text
    empty = client.get("/customers", params={"grade": "VIP", "region": "서울"})
    assert empty.status_code == 200 and "검색 결과가 없습니다." in empty.text
    assert "<table" not in empty.text
    report("[통과] HTTPS 이름 공백 제거·등급·지역 복합 조회, URL 공유·새로고침·페이지 이동 후 조건과 실제 목록 유지")


def verify_customer_crud(client, db_path, employee_email, report):
    """임시 DB의 HTTPS 등록·수정·숨김 삭제 및 주문 이력을 검증한다."""
    listing = client.get("/customers")
    original_total = int(re.search(r"총 (\d+)명", listing.text).group(1))
    values = {"name": "HTTPS등록고객", "email": f"crud-{time.time_ns()}@example.com",
              "address": "부산", "grade": "신규"}
    assert client.get("/customers/new").status_code == 200
    response = client.post("/customers", data=values)
    assert response.status_code == 303
    detail_url = response.headers["location"]
    customer_id = int(detail_url.rsplit("/", 1)[1])
    last_page = (original_total + 1 + 19) // 20
    listing = client.get("/customers", params={"page": last_page})
    assert f"총 {original_total + 1}명" in listing.text
    assert f'href="{detail_url}"' in listing.text
    report("[통과] HTTPS 고객 등록 → 목록 총건수 증가 및 상세 링크 반영")
    with closing(sqlite3.connect(db_path)) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        with connection:
            employee_id = connection.execute("SELECT id FROM employees WHERE email=?", (employee_email,)).fetchone()[0]
            product_id = connection.execute(
                "INSERT INTO product (name, category, price) VALUES ('HTTPS주문검증상품', '식품', 1000)"
            ).lastrowid
            order_id = connection.execute(
                "INSERT INTO orders (customer_id, product_id, employee_id, quantity, total_amount, order_date, status) "
                "VALUES (?, ?, ?, 2, 2000, '2026-11-15', '완료')",
                (customer_id, product_id, employee_id),
            ).lastrowid
    assert client.get(detail_url + "/edit").status_code == 200
    changed = values | {"name": "HTTPS수정고객", "address": "서울", "grade": "VIP"}
    response = client.post(detail_url + "/edit", data=changed)
    assert response.status_code == 303 and response.headers["location"] == detail_url
    detail = client.get(detail_url)
    assert detail.status_code == 200
    for expected in ("HTTPS수정고객", "서울", "VIP", "HTTPS주문검증상품", "2,000원", "2026-11-15"):
        assert expected in detail.text
    report("[통과] HTTPS 고객 수정 → 상세 반영 및 본인 주문 이력 표시")
    assert client.post(detail_url + "/delete").status_code == 303
    assert client.get(detail_url).status_code == 404
    assert client.get(detail_url + "/edit").status_code == 404
    listing = client.get("/customers")
    assert f"총 {original_total}명" in listing.text
    assert f'href="{detail_url}"' not in listing.text
    searched = client.get("/customers", params={"q": changed["email"]})
    assert searched.status_code == 200 and "총 0명" in searched.text
    assert "검색 결과가 없습니다." in searched.text
    with closing(sqlite3.connect(db_path)) as connection:
        stored = connection.execute("SELECT name, email, address, grade FROM customer WHERE id=?", (customer_id,)).fetchone()
        assert stored == tuple(changed[field] for field in ("name", "email", "address", "grade"))
        assert connection.execute("SELECT customer_id FROM hidden_customer WHERE customer_id=?", (customer_id,)).fetchone() == (customer_id,)
        assert connection.execute("SELECT customer_id FROM orders WHERE id=?", (order_id,)).fetchone() == (customer_id,)
    report("[통과] HTTPS 숨김 삭제 → 목록·상세에서 제외, customer 원본 행·hidden_customer 기록·주문 유지")
    report("[통과] 숨김 고객은 이메일 검색 결과에서도 제외")


def verify_authentication(context, report, db_path, base_url):
    account = {"name": "인증검증직원", "email": "https-auth-" + str(time.time_ns()) + "@example.com",
               "address": "검증용 주소", "password": "검증용비밀번호123!"}
    with httpx.Client(base_url=base_url, verify=context,
                      trust_env=False, follow_redirects=False,
                      headers={"Accept": "application/json"}) as client:
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
            assert page.text.count('<header class="site-header">') == 1
            header = page.text.partition('<header class="site-header">')[2].partition("</header>")[0]
            assert "홈</a>" in header and "고객 목록</a>" in header
            if path in ("/", "/customers"):
                assert f'href="{path}" aria-current="page"' in header
            else:
                assert 'aria-current="page"' not in header
            if path == "/":
                assert "인증검증직원님, 환영합니다." in page.text
                assert "고객 목록 바로가기" in page.text
        report("[통과] 페이지 이동 후 인증 유지 및 모든 HTML 페이지의 이름·로그아웃 표시")
        report("[통과] 모든 HTML 페이지의 공통 내비게이션·현재 메뉴 활성 표시·로그인 홈 인사와 목록 바로가기")
        verify_customer_crud(client, db_path, account["email"], report)
        verify_customer_pagination(client, db_path, report)
        logout = client.post("/auth/logout")
        assert logout.status_code == 303 and logout.headers["location"] == "/auth/login"
        assert "Max-Age=0" in logout.headers["set-cookie"]
        assert "access_token" not in client.cookies
        assert client.get("/customers").status_code == 401
        assert client.get("/auth/login").status_code == 200
        home = client.get("/")
        assert home.status_code == 200 and "로그인해 주세요" in home.text
        assert "인증검증직원" not in home.text
        assert 'action="/auth/logout"' not in home.text
        report("[통과] 로그아웃 후 홈 로그인 유도 및 공통 헤더 로그인 링크")
        report("[통과] 로그아웃 쿠키 삭제·로그인 화면 이동·보호 라우트 접근 차단")


def main():
    parser = argparse.ArgumentParser(description="임시 DB를 사용하는 HTTPS 실행 검증")
    parser.add_argument("--port", type=int, default=8000, help="검증 서버 포트 (기본 8000)")
    port = parser.parse_args().port
    if not 1 <= port <= 65535:
        parser.error("포트는 1부터 65535 사이여야 합니다.")
    base_url = f"https://127.0.0.1:{port}"
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
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                raise RuntimeError(f"{port} 포트가 사용 중입니다. --port로 빈 포트를 지정해 주세요.")
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
                    "uvicorn.run(create_app(Path(sys.argv[1])), host='127.0.0.1', port=int(sys.argv[2]), "
                    "ssl_keyfile='.certs/localhost.key', ssl_certfile='.certs/localhost.crt')",
                ]), str(copy_path), str(port)],
                cwd=ROOT, stdout=output, stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
            try:
                deadline = time.monotonic() + 20
                while True:
                    if server.poll() is not None:
                        raise RuntimeError("Uvicorn 시작 실패: uvicorn-verification.log를 확인해 주세요.")
                    try:
                        response = opener.open(base_url + "/", timeout=2)
                        with response:
                            assert response.status == 200
                            assert "Customer Manager" in response.read().decode("utf-8")
                        break
                    except URLError:
                        if time.monotonic() >= deadline:
                            raise
                        time.sleep(0.2)
                report(f"[통과] GET {base_url}/ → 200 OK")
                for route in ("/health", "/docs", "/openapi.json"):
                    with opener.open(base_url + route, timeout=5) as response:
                        assert response.status == 200
                        if route == "/health":
                            assert json.load(response) == {"status": "ok", "database": "ok"}
                    report(f"[통과] GET {route} → 200 OK")
                report("[통과] 인증서 신뢰 및 127.0.0.1 호스트명 검증")
                verify_migrated_copy(copy_path)
                report("[통과] 임시 복사본의 hidden_customer 생성·nullable password_hash 확장 및 기존 전체 행·칼럼 보존")
                verify_authentication(context, report, copy_path, base_url)
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
