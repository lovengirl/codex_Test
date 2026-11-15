"""임시 DB·자동 할당 포트·실제 HTTPS 브라우저를 사용한다."""

import asyncio
import socket
from threading import Thread
from time import monotonic, sleep

import pytest
import uvicorn
from playwright.sync_api import sync_playwright

from main import create_app
from scripts import create_dev_cert


@pytest.fixture
def browser_page(tmp_path, monkeypatch, request):
    # 인증서·DB·서명키·실패 증거를 전부 pytest 임시 폴더에 격리한다.
    monkeypatch.setattr(create_dev_cert, 'ROOT', tmp_path)
    create_dev_cert.main()
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    config = uvicorn.Config(
        create_app(tmp_path / 'ui.db'), host='127.0.0.1', port=port,
        ssl_keyfile=str(tmp_path / '.certs/localhost.key'),
        ssl_certfile=str(tmp_path / '.certs/localhost.crt'), log_level='warning',
        timeout_graceful_shutdown=2, timeout_keep_alive=1,
    )
    server = uvicorn.Server(config)
    # Windows Proactor에서 TLS 연결 종료가 지연되지 않도록 서버만 Selector로 실행한다.
    def run_server():
        asyncio.run(server.serve(sockets=[sock]), loop_factory=asyncio.SelectorEventLoop)

    thread = Thread(target=run_server, daemon=True)
    thread.start()
    try:
        deadline = monotonic() + 15
        while not server.started and thread.is_alive() and monotonic() < deadline:
            sleep(0.05)
        assert server.started, 'UI 검증용 HTTPS 서버를 시작하지 못했습니다.'
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(
                base_url=f'https://127.0.0.1:{port}', ignore_https_errors=True,
                locale='ko-KR',
            )
            context.tracing.start(screenshots=True, snapshots=True)
            page = context.new_page()
            page.set_default_timeout(10_000)
            try:
                yield page
            finally:
                failed = getattr(request.node, 'report_call', None)
                if failed is not None and failed.failed:
                    page.screenshot(path=str(tmp_path / '실패화면.png'), full_page=True)
                    context.tracing.stop(path=str(tmp_path / '실패추적.zip'))
                    print(f'UI 실패 증거: {tmp_path}')
                else:
                    context.tracing.stop()
                context.close()
                browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()
        assert not thread.is_alive(), 'UI 검증용 서버가 종료되지 않았습니다.'
