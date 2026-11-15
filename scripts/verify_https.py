"""실제 Uvicorn의 HTTPS 응답과 기존 DB 보존을 검증하고 한글 로그를 남긴다."""

from datetime import datetime, timezone, timedelta
import hashlib
import json
from pathlib import Path
import socket
import sqlite3
import ssl
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import ProxyHandler, HTTPSHandler, build_opener

ROOT = Path(__file__).resolve().parents[1]


def snapshot():
    path = ROOT / "customer.db"
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as connection:
        schema = connection.execute(
            "SELECT name, sql FROM sqlite_master ORDER BY name"
        ).fetchall()
        counts = {}
        for name, in connection.execute("SELECT name FROM sqlite_master WHERE type='table'"):
            quoted = name.replace('"', '""')
            counts[name] = connection.execute(f'SELECT count(*) FROM "{quoted}"').fetchone()[0]
    return hashlib.sha256(path.read_bytes()).hexdigest(), schema, counts


def main():
    lines = []

    def report(message):
        print(message, flush=True)
        lines.append(message)

    server = None
    try:
        report("검증 시각: " + datetime.now(timezone(timedelta(hours=9))).isoformat())
        report("Python 버전: " + sys.version.split()[0])
        with socket.socket() as probe:
            if probe.connect_ex(("127.0.0.1", 8000)) == 0:
                raise RuntimeError("8000 포트가 사용 중입니다. 기존 서버 종료 후 다시 실행해 주세요.")
        before = snapshot()
        context = ssl.create_default_context(cafile=str(ROOT / ".certs/localhost.crt"))
        opener = build_opener(ProxyHandler({}), HTTPSHandler(context=context))
        with (ROOT / "uvicorn-verification.log").open("w", encoding="utf-8") as output:
            server = subprocess.Popen(
                [sys.executable, "-m", "uvicorn", "main:app", "--host", "127.0.0.1",
                 "--port", "8000", "--ssl-keyfile", ".certs/localhost.key",
                 "--ssl-certfile", ".certs/localhost.crt"],
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
        (ROOT / "verification.log").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
