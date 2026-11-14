"""서비스 및 DB 상태 라우터."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from database import get_session

router = APIRouter(tags=["상태 확인"])


@router.get("/health")
def health(session: Annotated[Session, Depends(get_session)]):
    try:
        # 실제 업무 테이블을 조회해 DB 연결과 스키마를 함께 확인한다.
        session.execute(text("SELECT id FROM customer LIMIT 1")).first()
    except SQLAlchemyError:
        raise HTTPException(status_code=503, detail="데이터베이스 연결을 확인해 주세요.") from None
    return {"status": "ok", "database": "ok"}
