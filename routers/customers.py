"""고객 CRUD·검색·필터·페이지 API를 추가할 라우터."""

from fastapi import APIRouter, Depends, Request

from authentication import get_current_user
from templating import templates

router = APIRouter(prefix="/customers", tags=["고객"], dependencies=[Depends(get_current_user)])


@router.get("")
def customers(request: Request, user=Depends(get_current_user)):
    return templates.TemplateResponse(request=request, name="customers.html", context={"user": user},
                                      headers={"Cache-Control": "no-store"})
