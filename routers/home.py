"""홈 화면 라우터."""

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter()


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
def home():
    return """<!doctype html>
<html lang="ko">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Customer Manager</title></head>
<body><main><h1>Customer Manager</h1>
<p>고객관리 서비스의 S1 실행 골격이 준비되었습니다.</p>
<ul><li><a href="/docs">API 문서</a></li>
<li><a href="/health">서비스 및 데이터베이스 상태</a></li></ul>
</main></body></html>"""
