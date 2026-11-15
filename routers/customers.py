"""인증된 직원용 고객 목록·상세·등록·수정·숨김 삭제."""

from typing import Annotated

from email_validator import EmailNotValidError, validate_email
from fastapi import APIRouter, Depends, Form, HTTPException, Path, Query, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from authentication import get_current_user
from customer_queries import customer_page, visible_customer
from database import get_session
from routers.auth import check_origin
from templating import templates

router = APIRouter(prefix="/customers", tags=["고객"],
                   dependencies=[Depends(get_current_user), Depends(check_origin)])
CustomerId = Annotated[int, Path(ge=1, le=2**63 - 1)]
GRADES = ("VIP", "일반", "신규")


def require_customer(session, customer_id):
    result = visible_customer(session, customer_id)
    if result is None:
        raise HTTPException(status_code=404, detail="고객을 찾을 수 없습니다.")
    return result


def customer_input(name: Annotated[str, Form()] = "", email: Annotated[str, Form()] = "",
                   address: Annotated[str, Form()] = "", grade: Annotated[str, Form()] = ""):
    values = {"name": name.strip(), "email": email.strip(),
              "address": address.strip(), "grade": grade.strip()}
    if not values["name"] or not values["address"]:
        return values, "이름과 주소를 입력해 주세요."
    if values["grade"] not in GRADES:
        return values, "등급은 VIP, 일반, 신규 중에서 선택해 주세요."
    try:
        values["email"] = validate_email(values["email"], check_deliverability=False).normalized.casefold()
    except EmailNotValidError:
        return values, "올바른 이메일을 입력해 주세요."
    return values, None


def customer_form_page(request, user, values=None, *, customer_id=None, error=None, status_code=200):
    return templates.TemplateResponse(
        request=request, name="customer_form.html", context={
            "user": user, "values": values or {}, "customer_id": customer_id,
            "grades": GRADES, "error": error,
        }, status_code=status_code, headers={"Cache-Control": "no-store"},
    )


def email_in_use(session, email, customer_id=0):
    # UNIQUE 제약과의 호환성을 위해 숨김 고객도 이메일 중복 검사에 포함한다.
    return session.execute(text(
        "SELECT id FROM customer WHERE lower(email) = :email AND id != :id LIMIT 1"
    ), {"email": email, "id": customer_id}).first() is not None


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


@router.get("/new")
def new_customer(request: Request, user=Depends(get_current_user)):
    return customer_form_page(request, user)


@router.post("")
def create_customer(request: Request, form=Depends(customer_input),
                    user=Depends(get_current_user), session: Session = Depends(get_session)):
    values, error = form
    if error:
        return customer_form_page(request, user, values, error=error, status_code=422)
    try:
        session.rollback()
        session.execute(text("BEGIN IMMEDIATE"))
        if email_in_use(session, values["email"]):
            session.rollback()
            return customer_form_page(request, user, values, error="이미 등록된 이메일입니다.", status_code=409)
        result = session.execute(text(
            "INSERT INTO customer (name, email, address, grade) VALUES (:name, :email, :address, :grade)"
        ), values)
        customer_id = result.lastrowid
        session.commit()
    except IntegrityError:
        session.rollback()
        return customer_form_page(request, user, values, error="이미 등록된 이메일입니다.", status_code=409)
    return RedirectResponse(f"/customers/{customer_id}", status_code=303)


@router.get("/{customer_id}")
def customer_detail(request: Request, customer_id: CustomerId, user=Depends(get_current_user),
                    session: Session = Depends(get_session)):
    item = require_customer(session, customer_id)
    orders = session.execute(text(
        "SELECT o.id, p.name AS product_name, e.name AS employee_name, o.quantity, "
        "o.total_amount, o.order_date, o.status FROM orders AS o "
        "LEFT JOIN product AS p ON p.id = o.product_id "
        "LEFT JOIN employees AS e ON e.id = o.employee_id "
        "WHERE o.customer_id = :id ORDER BY o.order_date DESC, o.id DESC"
    ), {"id": customer_id}).mappings().all()
    return templates.TemplateResponse(
        request=request, name="customer_detail.html",
        context={"user": user, "customer": item, "orders": orders},
        headers={"Cache-Control": "no-store"},
    )


@router.get("/{customer_id}/edit")
def edit_customer_page(request: Request, customer_id: CustomerId, user=Depends(get_current_user),
                       session: Session = Depends(get_session)):
    item = require_customer(session, customer_id)
    return customer_form_page(request, user, item, customer_id=customer_id)


@router.post("/{customer_id}/edit")
def update_customer(request: Request, customer_id: CustomerId, form=Depends(customer_input),
                    user=Depends(get_current_user), session: Session = Depends(get_session)):
    values, error = form
    try:
        # 숨김 삭제와 수정을 직렬화해 숨김 처리된 고객의 수정을 막는다.
        session.rollback()
        session.execute(text("BEGIN IMMEDIATE"))
        require_customer(session, customer_id)
        if error:
            session.rollback()
            return customer_form_page(request, user, values, customer_id=customer_id,
                                      error=error, status_code=422)
        if email_in_use(session, values["email"], customer_id):
            session.rollback()
            return customer_form_page(request, user, values, customer_id=customer_id,
                                      error="이미 등록된 이메일입니다.", status_code=409)
        session.execute(text(
            "UPDATE customer SET name=:name, email=:email, address=:address, grade=:grade WHERE id=:id"
        ), {**values, "id": customer_id})
        session.commit()
    except IntegrityError:
        session.rollback()
        return customer_form_page(request, user, values, customer_id=customer_id,
                                  error="이미 등록된 이메일입니다.", status_code=409)
    return RedirectResponse(f"/customers/{customer_id}", status_code=303)


@router.post("/{customer_id}/delete")
def delete_customer(customer_id: CustomerId, session: Session = Depends(get_session)):
    session.rollback()
    session.execute(text("BEGIN IMMEDIATE"))
    require_customer(session, customer_id)
    session.execute(text("INSERT INTO hidden_customer (customer_id) VALUES (:id)"), {"id": customer_id})
    session.commit()
    return RedirectResponse("/customers", status_code=303)
