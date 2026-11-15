"""홈 화면 라우터."""

import re

from fastapi import APIRouter, Depends, Request
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.responses import HTMLResponse

from authentication import get_optional_user
from templating import templates

router = APIRouter()


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
def home(request: Request, user=Depends(get_optional_user)):
    return templates.TemplateResponse(request=request, name="home.html", context={"user": user},
                                      headers={"Cache-Control": "no-store"})


@router.get("/docs", response_class=HTMLResponse, include_in_schema=False)
def docs(request: Request, user=Depends(get_optional_user)):
    page = get_swagger_ui_html(openapi_url="/openapi.json", title="Customer Manager API")
    return documentation_page(request, page, user)


@router.get("/redoc", response_class=HTMLResponse, include_in_schema=False)
def redoc(request: Request, user=Depends(get_optional_user)):
    page = get_redoc_html(openapi_url="/openapi.json", title="Customer Manager API")
    return documentation_page(request, page, user)


def documentation_page(request, page, user):
    # FastAPI가 생성한 문서 자산만 삽입한다. 사용자 이름은 base 템플릿이 이스케이프한다.
    html = page.body.decode("utf-8")
    head = html.partition("<head>")[2].partition("</head>")[0]
    head = re.sub(r"<title>.*?</title>|<meta\b[^>]*>", "", head, flags=re.DOTALL)
    body = html.partition("<body>")[2].partition("</body>")[0]
    return templates.TemplateResponse(
        request=request, name="documentation.html", context={
            "user": user, "documentation_head": head, "documentation_body": body,
        }, headers={"Cache-Control": "no-store"},
    )
