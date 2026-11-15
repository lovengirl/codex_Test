"""고객 조회의 공통 숨김 제외 조건과 DB 페이지네이션."""

import hashlib
import json

from sqlalchemy import column, exists, func, or_, select, table, text
from sqlalchemy.orm import Session

from database import compact_name


PAGE_SIZE = 20
# casefold 확장과 LIKE 이스케이프 후에도 SQLite 패턴 길이 한도 이내로 제한한다.
MAX_SEARCH_LENGTH = 1000
customer = table("customer", *(column(name) for name in ("id", "name", "email", "address", "grade")))
hidden_customer = table("hidden_customer", column("customer_id"))


def visible_customers(source=customer):
    """모든 고객 조회에서 재사용할 숨김 제외 SELECT를 반환한다."""
    hidden = exists().where(hidden_customer.c.customer_id == source.c.id)
    return select(source).where(~hidden)


def visible_customer(session: Session, customer_id: int):
    """상세 조회와 변경 전 확인에도 공통 숨김 제외 조건을 적용한다."""
    return session.execute(
        visible_customers().where(customer.c.id == customer_id)
    ).mappings().first()


def customer_version(item):
    """수정 폼이 읽은 고객 값의 버전을 비교한다. 저장된 행은 변경하지 않는다."""
    original = json.dumps(dict(item), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(original.encode("utf-8")).hexdigest()


def customer_page(session: Session, page: int, q: str = "", grade: str = "", region: str = ""):
    """전체 행을 로드하지 않고 건수와 최대 20명의 고객만 조회한다."""
    # sqlite3 legacy 모드에서는 Session의 SELECT만으로 읽기 트랜잭션이 시작되지 않는다.
    if not session.connection().connection.driver_connection.in_transaction:
        session.execute(text("BEGIN"))
    visible = visible_customers()
    q = q.strip()
    grade, region = grade.strip(), region.strip()
    if q:
        visible = visible.where(or_(
            func.compact_name(customer.c.name).contains(compact_name(q), autoescape=True),
            func.casefold(customer.c.email).contains(q.casefold(), autoescape=True),
        ))
    if grade:
        visible = visible.where(customer.c.grade == grade)
    if region:
        visible = visible.where(func.address_region(customer.c.address) == region)
    total = session.scalar(select(func.count()).select_from(visible.subquery()))
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    items = session.execute(
        visible.order_by(customer.c.id).limit(PAGE_SIZE).offset((page - 1) * PAGE_SIZE)
    ).mappings().all()
    region_column = func.address_region(customer.c.address)
    regions = session.scalars(
        visible_customers().with_only_columns(region_column).where(region_column != "")
        .distinct().order_by(region_column)
    ).all()
    filters = {key: value for key, value in (("q", q), ("grade", grade), ("region", region)) if value}
    return {"items": items, "page": page, "total": total, "total_pages": total_pages,
            "q": q, "grade": grade, "region": region, "regions": regions, "filters": filters}
