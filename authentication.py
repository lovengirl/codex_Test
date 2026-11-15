"""JWT 쿠키 검증과 무상태 인증 의존성."""

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import secrets
import tempfile
from typing import Annotated

from fastapi import Depends, Request
from fastapi.responses import JSONResponse, RedirectResponse
import jwt
from sqlalchemy import text
from sqlalchemy.orm import Session

from database import get_session

COOKIE_NAME = "access_token"
TOKEN_SECONDS = 60 * 60
REFRESH_SECONDS = 5 * 60
ALGORITHM = "HS256"


class AuthenticationRequired(Exception):
    """보호 라우트의 미인증 응답을 요청 형식에 따라 처리한다."""


async def authentication_required_response(request: Request, exc: AuthenticationRequired):
    accepted = {part.split(";", 1)[0].strip().lower()
                for part in request.headers.get("accept", "").split(",")}
    if "application/json" in accepted:
        response = JSONResponse({"detail": "로그인이 필요합니다."}, status_code=401)
    else:
        response = RedirectResponse("/auth/login?expired=true", status_code=303)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Vary"] = "Accept"
    clear_auth_cookie(response)
    return response


def set_auth_cookie(response, employee_id: int, key: str):
    response.set_cookie(COOKIE_NAME, create_token(employee_id, key), max_age=TOKEN_SECONDS,
                        httponly=True, secure=True, samesite="strict", path="/")
    response.headers["Cache-Control"] = "no-store"


def clear_auth_cookie(response):
    response.delete_cookie(COOKIE_NAME, path="/", httponly=True, secure=True, samesite="strict")
    response.headers["Cache-Control"] = "no-store"


async def refresh_auth_cookie(request: Request, call_next):
    response = await call_next(request)
    employee_id = getattr(request.state, "refresh_employee_id", None)
    cookie_changed = any(header.lstrip().startswith(COOKIE_NAME + "=")
                         for header in response.headers.getlist("set-cookie"))
    if employee_id is not None and response.status_code < 400 and not cookie_changed:
        set_auth_cookie(response, employee_id, request.app.state.jwt_secret)
    return response


def load_signing_key(db_path: Path) -> str:
    """환경 설정 또는 로컬 키 파일을 사용해 재시작 후에도 서명을 검증한다."""
    configured = os.environ.get("JWT_SECRET_KEY")
    if configured is not None:
        if len(configured.encode("utf-8")) < 32:
            raise ValueError("JWT_SECRET_KEY는 32바이트 이상으로 설정해 주세요.")
        return configured
    key_path = Path(db_path).resolve().with_suffix(".jwt-key")
    if not key_path.exists():
        # 완성된 파일을 원자적으로 공개해 동시 시작 시에도 같은 키를 사용한다.
        with tempfile.NamedTemporaryFile(mode="w", dir=key_path.parent, suffix=".jwt-key",
                                         encoding="utf-8", delete=False) as temporary:
            temporary.write(secrets.token_urlsafe(48))
            temporary_path = Path(temporary.name)
        try:
            try:
                os.link(temporary_path, key_path)
            except FileExistsError:
                pass
        finally:
            temporary_path.unlink()
    key = key_path.read_text(encoding="utf-8").strip()
    if len(key.encode("utf-8")) < 32:
        raise ValueError("JWT 서명키 파일을 확인해 주세요.")
    return key


def create_token(employee_id: int, key: str) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {"sub": str(employee_id), "iat": now, "exp": now + timedelta(seconds=TOKEN_SECONDS)},
        key,
        algorithm=ALGORITHM,
    )


def get_optional_user(request: Request, session: Annotated[Session, Depends(get_session)]):
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    try:
        payload = jwt.decode(token, request.app.state.jwt_secret, algorithms=[ALGORITHM],
                             options={"require": ["sub", "iat", "exp"]})
        subject = payload["sub"]
        if not subject.isascii() or not subject.isdecimal() or len(subject) > 19:
            return None
        employee_id = int(subject)
        if employee_id > 2**63 - 1:
            return None
    except jwt.InvalidTokenError:
        return None
    user = session.execute(text(
        "SELECT id, name FROM employees WHERE id = :id AND password_hash IS NOT NULL"
    ), {"id": employee_id}).mappings().first()
    remaining = int(payload["exp"]) - datetime.now(timezone.utc).timestamp()
    if user is not None and 0 < remaining <= REFRESH_SECONDS:
        request.state.refresh_employee_id = user["id"]
    return user


def get_current_user(user=Depends(get_optional_user)):
    if user is None:
        raise AuthenticationRequired()
    return user
