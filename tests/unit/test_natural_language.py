"""HTTP 없이 함수 입력과 반환값을 검증한다."""


import pytest


import natural_language as nl


@pytest.mark.parametrize("content", [
    '{"sql":"SELECT 1", "explanation":"조회"}',
    '```json\n{"sql":"SELECT 1", "explanation":"조회"}\n```',
    '잡텍스트 {잘못된 앞부분} {"sql":"SELECT 1", "explanation":"조회"} 뒤 텍스트',
])
def test_JSON_코드펜스와_잡텍스트제거(content):
    assert nl.parse_model_json(content)["sql"] == "SELECT 1"


def test_JSON파싱불가_원문콘솔로그(caplog):
    with pytest.raises(ValueError):
        nl.parse_model_json("원문 응답 그대로 기록")
    assert "원문 응답 그대로 기록" in caplog.text






def test_숨김테이블이없으면_숨김대안을상상하지않음():
    schema = [{"name": "customer", "columns": [{"name": "id"}]}]
    answer = nl.unavailable_answer("탈퇴 고객수", schema, "탈퇴 기록이 없습니다.")
    assert "숨김" not in answer
    assert "전체 고객 수" in answer

