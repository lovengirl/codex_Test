"""pytest 공통 설정. 모든 테스트는 임시 DB를 사용한다."""

import pytest
from fastapi.testclient import TestClient

from main import create_app


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path / "test.db"), base_url="https://testserver") as test_client:
        yield test_client
