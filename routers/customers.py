"""고객 CRUD·검색·필터·페이지 API를 추가할 라우터."""

from fastapi import APIRouter

router = APIRouter(prefix="/customers", tags=["고객"])
