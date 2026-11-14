"""인증 기능 확장 지점. 로그인 등 실제 API는 후속 단계에서 구현한다."""

from fastapi import APIRouter

router = APIRouter(prefix="/auth", tags=["인증"])
