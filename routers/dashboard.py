"""DB 집계로 표시하는 직원 전용 매출 대시보드."""

from fastapi import APIRouter, Depends, Request
from sqlalchemy import column, func, select, table, text
from sqlalchemy.orm import Session

from authentication import get_current_user
from customer_queries import visible_customers
from database import get_session
from templating import templates

router = APIRouter(prefix="/dashboard", tags=["대시보드"], dependencies=[Depends(get_current_user)])
orders = table("orders", column("customer_id"), column("total_amount"),
               column("order_date"), column("status"))


@router.get("")
def dashboard(request: Request, user=Depends(get_current_user), session: Session = Depends(get_session)):
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
    return templates.TemplateResponse(
        request=request, name="dashboard.html",
        context={"user": user, "metrics": metrics, "monthly_sales": monthly_sales},
        headers={"Cache-Control": "no-store"},
    )
