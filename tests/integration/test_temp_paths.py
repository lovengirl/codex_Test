"""pytest 기본 임시 경로가 프로젝트 설정을 실제로 사용하는지 검증한다."""

from pathlib import Path


def test_임시DB경로는_설정한_실행별_폴더에_격리(pytestconfig, tmp_path_factory, tmp_path):
    configured = pytestconfig.option.basetemp
    assert configured is not None
    base = Path(configured).resolve()
    assert tmp_path_factory.getbasetemp() == base
    assert tmp_path.is_relative_to(base)
    sample = tmp_path / "권한확인.txt"
    sample.write_text("임시 경로 읽기·쓰기 정상", encoding="utf-8")
    assert sample.read_text(encoding="utf-8") == "임시 경로 읽기·쓰기 정상"
