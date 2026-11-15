"""매출 대시보드 API를 추가할 라우터."""

from fastapi import APIRouter, Depends, Request

from authentication import get_current_user
from templating import templates

router = APIRouter(prefix="/dashboard", tags=["대시보드"], dependencies=[Depends(get_current_user)])


@router.get("")
def dashboard(request: Request, user=Depends(get_current_user)):
    return templates.TemplateResponse(request=request, name="dashboard.html", context={"user": user},
                                      headers={"Cache-Control": "no-store"})
