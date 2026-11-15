"""기존 직원 테이블을 이용하는 회원가입·로그인·로그아웃."""

from typing import Annotated
from urllib.parse import urlsplit

import bcrypt
from email_validator import EmailNotValidError, validate_email
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from authentication import clear_auth_cookie, get_optional_user, set_auth_cookie
from database import get_session
from templating import templates

LOGIN_ERROR = "이메일 또는 비밀번호를 확인해 주세요."
DUMMY_HASH = b"$2b$12$hyfI0TmJSppObzlWa8/bpO4vM34xrCcdmOAMmuVEUPjfCI5.6p1Km"


def check_origin(request: Request):
    if request.method == "POST" and (origin := request.headers.get("origin")):
        source = urlsplit(origin)
        target = urlsplit(str(request.base_url))
        if (source.scheme, source.netloc) != (target.scheme, target.netloc):
            raise HTTPException(status_code=403, detail="요청 출처를 확인해 주세요.")


router = APIRouter(prefix="/auth", tags=["인증"], dependencies=[Depends(check_origin)])


def auth_page(request, mode, *, user=None, error=None, status_code=200, registered=False,
              expired=False):
    return templates.TemplateResponse(request=request, name="auth.html", context={
        "user": user, "mode": mode, "error": error, "registered": registered, "expired": expired,
    }, status_code=status_code, headers={"Cache-Control": "no-store"})


@router.get("/register")
def register_page(request: Request, user=Depends(get_optional_user)):
    return auth_page(request, "register", user=user)


@router.get("/login")
def login_page(request: Request, registered: bool = False, expired: bool = False,
               user=Depends(get_optional_user)):
    return auth_page(request, "login", user=user, registered=registered, expired=expired)


@router.post("/register")
def register(request: Request, session: Annotated[Session, Depends(get_session)],
             name: Annotated[str, Form()] = "", email: Annotated[str, Form()] = "",
             address: Annotated[str, Form()] = "", password: Annotated[str, Form()] = "",
             user=Depends(get_optional_user)):
    name, address = name.strip(), address.strip()
    try:
        email = validate_email(email.strip(), check_deliverability=False).normalized.casefold()
    except EmailNotValidError:
        return auth_page(request, "register", user=user,
                         error="올바른 이메일을 입력해 주세요.", status_code=422)
    if not name or not address or not 1 <= len(password.encode("utf-8")) <= 72:
        return auth_page(request, "register", user=user, error=(
            "이름과 주소를 입력하고 비밀번호를 UTF-8 기준 1~72바이트로 입력해 주세요."
        ), status_code=422)
    password_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("ascii")
    try:
        # 기존 이메일의 대소문자를 고려하고 동시 가입도 직렬화한다.
        session.rollback()
        session.execute(text("BEGIN IMMEDIATE"))
        exists = session.execute(text("SELECT id FROM employees WHERE lower(email) = :email"),
                                 {"email": email}).first()
        if exists:
            session.rollback()
            return auth_page(request, "register", user=user,
                             error="이미 가입된 이메일입니다.", status_code=409)
        session.execute(text(
            "INSERT INTO employees (name, email, address, password_hash) "
            "VALUES (:name, :email, :address, :password_hash)"
        ), {"name": name, "email": email, "address": address, "password_hash": password_hash})
        session.commit()
    except IntegrityError:
        session.rollback()
        return auth_page(request, "register", user=user,
                         error="이미 가입된 이메일입니다.", status_code=409)
    return RedirectResponse("/auth/login?registered=true", status_code=303)


@router.post("/login")
def login(request: Request, session: Annotated[Session, Depends(get_session)],
          email: Annotated[str, Form()] = "", password: Annotated[str, Form()] = "",
          user=Depends(get_optional_user)):
    employee = session.execute(text(
        "SELECT id, password_hash FROM employees WHERE lower(email) = :email"
    ), {"email": email.strip().casefold()}).mappings().first()
    stored_hash = employee["password_hash"] if employee else None
    password_bytes = password.encode("utf-8")
    valid_length = 1 <= len(password_bytes) <= 72
    try:
        verified = bcrypt.checkpw(password_bytes if valid_length else b"invalid-password",
                                  stored_hash.encode("ascii") if stored_hash else DUMMY_HASH)
    except (ValueError, UnicodeError):
        verified = False
    if not employee or not stored_hash or not valid_length or not verified:
        return auth_page(request, "login", user=user, error=LOGIN_ERROR, status_code=401)
    response = RedirectResponse("/customers", status_code=303)
    set_auth_cookie(response, employee["id"], request.app.state.jwt_secret)
    return response


@router.post("/logout")
def logout():
    response = RedirectResponse("/auth/login", status_code=303)
    clear_auth_cookie(response)
    return response
