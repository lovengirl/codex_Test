"""사용자·동시 실행 간 권한 충돌을 방지하는 pytest 임시 경로 설정."""

from uuid import uuid4

import pytest


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config):
    # pytest가 TempPathFactory를 생성하기 전에 기본 경로를 정한다.
    if config.option.basetemp is None:
        temporary_root = config.rootpath / ".pytest_tmp"
        temporary_root.mkdir(exist_ok=True)
        config.option.basetemp = temporary_root / f"run-{uuid4().hex}"
