"""DB 집계로 표시하는 직원 전용 매출 대시보드."""

from fastapi import APIRouter, Body, Depends, Form, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from typing import Annotated, Literal
import jwt
from sqlalchemy import column, func, select, table, text
from sqlalchemy.orm import Session

from authentication import get_current_user
from authentication import COOKIE_NAME
from ai_insight import generate_insight, make_snapshot, read_snapshot
from customer_queries import visible_customers
from database import get_session
from dashboard_exports import CONTENT_TYPES, dashboard_report
from natural_language import MODEL, ask_database
from routers.auth import check_origin
from templating import templates

router = APIRouter(prefix="/dashboard", tags=["대시보드"],
                   dependencies=[Depends(get_current_user), Depends(check_origin)])
# Insight는 이미 인증된 화면의 서명된 집계를 사용해 DB 재조회 없이 생성한다.
insight_router = APIRouter(prefix="/dashboard", tags=["대시보드"], dependencies=[Depends(check_origin)])
orders = table("orders", column("customer_id"), column("total_amount"),
               column("order_date"), column("status"))


def dashboard_data(session):
    # SQLite legacy 모드에서도 카드와 차트가 동일한 읽기 스냅샷을 사용한다.
    if not session.connection().connection.driver_connection.in_transaction:
        session.execute(text("BEGIN"))
    visible = visible_customers().cte("visible_customers")
    eligible_orders = select(orders).join(
        visible, visible.c.id == orders.c.customer_id,
    ).where(orders.c.status != "취소").cte("eligible_orders")
    customer_count = session.scalar(select(func.count()).select_from(visible))
    metrics = dict(session.execute(select(
        func.count().label("order_count"),
        func.coalesce(func.sum(eligible_orders.c.total_amount), 0).label("total_sales"),
        func.coalesce(func.avg(eligible_orders.c.total_amount), 0).label("average_order"),
    ).select_from(eligible_orders)).mappings().one())
    metrics["customer_count"] = customer_count
    month = func.substr(eligible_orders.c.order_date, 1, 7)
    monthly_sales = session.execute(select(
        month.label("month"), func.sum(eligible_orders.c.total_amount).label("sales"),
    ).group_by(month).order_by(month)).mappings().all()
    # 이미 DB에서 집계한 결과를 JSON 직렬화할 수 있는 형태로만 변환한다.
    monthly_sales = [dict(row) for row in monthly_sales]
    return {"metrics": metrics, "monthly_sales": monthly_sales}


def dashboard_page(request, user, session, question_result=None):
    data = dashboard_data(session)
    snapshot = make_snapshot(data["metrics"], user["id"], getattr(request.app.state, "jwt_secret", None))
    return templates.TemplateResponse(
        request=request, name="dashboard.html",
        context={"user": user, "model": MODEL, "question_result": question_result,
                 "insight_snapshot": snapshot, **data},
        headers={"Cache-Control": "no-store"},
    )


@router.get("")
def dashboard(request: Request, user=Depends(get_current_user), session: Session = Depends(get_session)):
    return dashboard_page(request, user, session)


@router.post("/ask")
def ask(request: Request, question: str = Form(""), user=Depends(get_current_user),
        session: Session = Depends(get_session)):
    db_path = session.execute(text("PRAGMA database_list")).mappings().first()["file"]
    result = ask_database(db_path, question)
    return dashboard_page(request, user, session, result)


@router.get("/export/{file_format}")
def export_dashboard(file_format: Literal["csv", "xlsx", "pdf"],
                     session: Session = Depends(get_session)):
    content = dashboard_report(dashboard_data(session), file_format)
    return Response(content=content, media_type=CONTENT_TYPES[file_format], headers={
        "Content-Disposition": f'attachment; filename="dashboard-report.{file_format}"',
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
    })


@insight_router.post("/insight")
async def dashboard_insight(request: Request, snapshot: Annotated[str, Body(embed=True, max_length=8000)]):
    try:
        facts = read_snapshot(snapshot, request.cookies.get(COOKIE_NAME), request.app.state.jwt_secret)
    except (jwt.InvalidTokenError, ValueError, TypeError):
        raise HTTPException(status_code=401, detail="유효한 로그인과 집계 데이터가 필요합니다.")
    sentences = await generate_insight(facts)
    if sentences is None:
        return Response(status_code=204, headers={"Cache-Control": "no-store"})
    return JSONResponse({"sentences": sentences}, headers={"Cache-Control": "no-store"})
