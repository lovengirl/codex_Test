"""인증된 직원용 고객 목록과 DB 페이지네이션 라우터."""

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from authentication import get_current_user
from customer_queries import customer_page
from database import get_session
from templating import templates

router = APIRouter(prefix="/customers", tags=["고객"], dependencies=[Depends(get_current_user)])


@router.get("")
def customers(request: Request, page: int = Query(default=1, ge=1, le=(2**63 - 1) // 20),
              user=Depends(get_current_user), session: Session = Depends(get_session)):
    result = customer_page(session, page)
    if page > result["total_pages"]:
        raise HTTPException(status_code=404, detail="해당 고객 페이지가 없습니다.")
    return templates.TemplateResponse(
        request=request, name="customers.html", context={"user": user, **result},
        headers={"Cache-Control": "no-store"},
    )
