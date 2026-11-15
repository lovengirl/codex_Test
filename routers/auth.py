"""기존 직원 테이블을 이용하는 회원가입·로그인·로그아웃."""

from typing import Annotated
from urllib.parse import urlsplit

import bcrypt
from email_validator import EmailNotValidError
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from authentication import clear_auth_cookie, get_optional_user, normalize_email, set_auth_cookie
from database import DATABASE_BUSY_MESSAGE, get_session, is_database_busy
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


def login_destination(destination):
    """대시보드 복귀 요청을 허용하며 기본 로그인 도착지는 고객 목록으로 유지한다."""
    return "/dashboard" if destination == "/dashboard" else "/customers"


def auth_page(request, mode, *, user=None, error=None, status_code=200, registered=False,
              expired=False, destination=None):
    destination = login_destination(destination if destination is not None else request.query_params.get("next"))
    return templates.TemplateResponse(request=request, name="auth.html", context={
        "user": user, "mode": mode, "error": error, "registered": registered, "expired": expired,
        "destination": destination,
    }, status_code=status_code, headers={"Cache-Control": "no-store"})


@router.get("/register")
def register_page(request: Request, user=Depends(get_optional_user)):
    return auth_page(request, "register", user=user)


@router.get("/login")
def login_page(request: Request, registered: bool = False, expired: bool = False,
               user=Depends(get_optional_user)):
    if user and request.query_params.get("next") == "/dashboard":
        return RedirectResponse("/dashboard", status_code=303, headers={"Cache-Control": "no-store"})
    return auth_page(request, "login", user=user, registered=registered, expired=expired)


@router.post("/register")
def register(request: Request, session: Annotated[Session, Depends(get_session)],
             name: Annotated[str, Form()] = "", email: Annotated[str, Form()] = "",
             address: Annotated[str, Form()] = "", password: Annotated[str, Form()] = "",
             destination: Annotated[str, Form(alias="next")] = "",
             user=Depends(get_optional_user)):
    name, address = name.strip(), address.strip()
    try:
        email = normalize_email(email)
    except EmailNotValidError:
        return auth_page(request, "register", user=user,
                         error="올바른 이메일을 입력해 주세요.", status_code=422, destination=destination)
    if not name or not address or not 1 <= len(password.encode("utf-8")) <= 72:
        return auth_page(request, "register", user=user, error=(
            "이름과 주소를 입력하고 비밀번호를 UTF-8 기준 1~72바이트로 입력해 주세요."
        ), status_code=422, destination=destination)
    password_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("ascii")
    try:
        # 기존 이메일의 대소문자를 고려하고 동시 가입도 직렬화한다.
        session.rollback()
        session.execute(text("BEGIN IMMEDIATE"))
        exists = session.execute(text("SELECT id FROM employees WHERE casefold(email) = :email"),
                                 {"email": email}).first()
        if exists:
            session.rollback()
            return auth_page(request, "register", user=user,
                             error="이미 가입된 이메일입니다.", status_code=409, destination=destination)
        session.execute(text(
            "INSERT INTO employees (name, email, address, password_hash) "
            "VALUES (:name, :email, :address, :password_hash)"
        ), {"name": name, "email": email, "address": address, "password_hash": password_hash})
        session.commit()
    except IntegrityError:
        session.rollback()
        return auth_page(request, "register", user=user,
                         error="이미 가입된 이메일입니다.", status_code=409, destination=destination)
    except OperationalError as error:
        session.rollback()
        if not is_database_busy(error):
            raise
        response = auth_page(request, "register", user=user,
                             error=DATABASE_BUSY_MESSAGE,
                             status_code=503, destination=destination)
        response.headers["Retry-After"] = "1"
        return response
    location = "/auth/login?registered=true"
    if login_destination(destination) == "/dashboard":
        location += "&next=/dashboard"
    return RedirectResponse(location, status_code=303)


@router.post("/login")
def login(request: Request, session: Annotated[Session, Depends(get_session)],
          email: Annotated[str, Form()] = "", password: Annotated[str, Form()] = "",
          destination: Annotated[str, Form(alias="next")] = "",
          user=Depends(get_optional_user)):
    try:
        email = normalize_email(email)
    except EmailNotValidError:
        email = ""
    employee = session.execute(text(
        "SELECT id, password_hash FROM employees WHERE casefold(email) = :email"
    ), {"email": email}).mappings().first()
    stored_hash = employee["password_hash"] if employee else None
    password_bytes = password.encode("utf-8")
    valid_length = 1 <= len(password_bytes) <= 72
    try:
        verified = bcrypt.checkpw(password_bytes if valid_length else b"invalid-password",
                                  stored_hash.encode("ascii") if stored_hash else DUMMY_HASH)
    except (ValueError, UnicodeError):
        verified = False
    if not employee or not stored_hash or not valid_length or not verified:
        return auth_page(request, "login", user=user, error=LOGIN_ERROR, status_code=401,
                         destination=destination)
    response = RedirectResponse(login_destination(destination), status_code=303)
    set_auth_cookie(response, employee["id"], request.app.state.jwt_secret)
    return response


@router.post("/logout")
def logout():
    response = RedirectResponse("/auth/login", status_code=303)
    clear_auth_cookie(response)
    return response
