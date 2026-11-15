"""pytest 공통 설정. 모든 테스트는 임시 DB를 사용한다."""

import pytest
from fastapi.testclient import TestClient

from main import create_app


def pytest_collection_modifyitems(items):
    """폴더에 맞는 표식을 붙여 경로와 -m 옵션을 모두 지원한다."""
    for item in items:
        layer = next((name for name in ('unit', 'integration', 'api') if name in item.path.parts), None)
        if layer:
            item.add_marker(getattr(pytest.mark, layer))
        if 'browser' in item.path.parts:
            item.add_marker(pytest.mark.browser)
        regression_names = (
            '와일드카드', '퍼센트와밑줄', '만료', '잘못된_JWT',
            '가상칼럼과테이블', '실제등록일없음', '등록일없는신규고객',
            '동일한탈퇴질문', '스키마검증', '숫자생성과', '생성오류와환각',
            '숨김테이블이없으면', '인증정보', '환각',
        )
        if any(name in item.name for name in regression_names):
            item.add_marker(pytest.mark.regression)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    setattr(item, 'report_' + report.when, report)


def pytest_terminal_summary(terminalreporter):
    terminalreporter.write_sep('=', '테스트 층별 실행 결과')
    for folder, label in [('unit', 'Unit 함수'), ('integration', 'Integration 결합'), ('api', 'API 요청·응답')]:
        counts = {}
        for result in ('passed', 'failed', 'error', 'skipped'):
            counts[result] = sum(f'tests/{folder}/' in report.nodeid.replace('\\', '/')
                                 for report in terminalreporter.stats.get(result, []))
        if any(counts.values()):
            terminalreporter.write_line(
                f"{label}: 통과 {counts['passed']}건 / 실패 {counts['failed']}건 / "
                f"오류 {counts['error']}건 / 건너뜀 {counts['skipped']}건"
            )


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path / "test.db"), base_url="https://testserver") as test_client:
        yield test_client
