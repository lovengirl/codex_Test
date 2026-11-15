"""매출 대시보드 API를 추가할 라우터."""

from fastapi import APIRouter

router = APIRouter(prefix="/dashboard", tags=["대시보드"])
