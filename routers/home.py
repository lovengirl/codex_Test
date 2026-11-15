"""홈 화면 라우터."""

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
def docs(user=Depends(get_optional_user)):
    page = get_swagger_ui_html(openapi_url="/openapi.json", title="Customer Manager API")
    return documentation_page(page, user)


@router.get("/redoc", response_class=HTMLResponse, include_in_schema=False)
def redoc(user=Depends(get_optional_user)):
    page = get_redoc_html(openapi_url="/openapi.json", title="Customer Manager API")
    return documentation_page(page, user)


def documentation_page(page, user):
    header = templates.get_template("header.html").render(user=user)
    return HTMLResponse(page.body.decode("utf-8").replace("<body>", "<body>" + header, 1),
                        headers={"Cache-Control": "no-store"})
