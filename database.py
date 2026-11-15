"""SQLite 연결과 테이블 생성. python database.py로 빈 DB를 준비한다."""

import sqlite3
from contextlib import closing
from pathlib import Path

from fastapi import Request
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import QueuePool

from models import SCHEMA


DEFAULT_DB_PATH = Path(__file__).resolve().with_name("customer.db")


def connect(db_path=DEFAULT_DB_PATH, *, check_same_thread=True):
    """외래키 검사가 활성화된 연결을 반환한다. 호출자가 연결을 닫는다."""
    connection = sqlite3.connect(db_path, check_same_thread=check_same_thread)
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def create_tables(connection):
    """현재 연결에 테이블을 생성한다. 커밋은 호출자가 관리한다."""
    for statement in SCHEMA:
        connection.execute(statement)


def initialize_database(db_path=DEFAULT_DB_PATH):
    """기존 스키마를 유지하며 없는 테이블만 생성한다."""
    with closing(connect(db_path)) as connection:
        with connection:
            create_tables(connection)


def make_engine(db_path=DEFAULT_DB_PATH):
    """기존 연결 함수를 재사용해 외래키 검사를 유지한다."""
    return create_engine(
        "sqlite://",
        creator=lambda: connect(db_path, check_same_thread=False),
        poolclass=QueuePool,
    )


def get_session(request: Request):
    """요청마다 세션을 열고 종료한다. 쓰기 작업의 커밋은 호출자가 담당한다."""
    with Session(request.app.state.engine) as session:
        yield session


if __name__ == "__main__":
    initialize_database()
    print(f"DB 준비 완료: {DEFAULT_DB_PATH}")
