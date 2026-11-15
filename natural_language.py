"""실제 스키마를 사용하는 OpenAI SQL 생성 및 SQLite 읽기 전용 실행."""

from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
import logging
import os
from pathlib import Path
import re
import sqlite3
from time import monotonic

from dotenv import dotenv_values
from openai import OpenAI
from sqlalchemy import column, table
from sqlglot import exp, parse

from customer_queries import visible_customers
from database import connect


logger = logging.getLogger(__name__)
MODEL = "gpt-5-mini"
ENV_PATH = Path(__file__).resolve().with_name(".env")
MAX_ROWS = 200
QUERY_TIMEOUT = 3
KOREA_TIME = timezone(timedelta(hours=9))
# 문자열·식별자 안의 주석 기호나 SQL 단어는 그대로 보존한다.
SQL_TOKENS = re.compile(
    r"'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|`(?:``|[^`])*`|\[[^\]]*\]"
    r"|--[^\r\n]*|/\*.*?\*/|\s+|;|[^\s]", re.DOTALL,
)


class SchemaValidationError(ValueError):
    """생성 SQL의 테이블·칼럼이 실제 SQLite 스키마와 일치하지 않는다."""


def actual_schema(connection):
    """매 요청마다 모든 테이블·뷰와 생성 칼럼까지 실제 DB에서 조회한다."""
    schema = []
    objects = connection.execute(
        "SELECT type, name, sql FROM main.sqlite_master "
        "WHERE type IN ('table', 'view') ORDER BY name"
    ).fetchall()
    for kind, name, ddl in objects:
        quoted = '"' + name.replace('"', '""') + '"'
        info = connection.execute(f"PRAGMA main.table_xinfo({quoted})").fetchall()
        schema.append({
            "type": kind, "name": name, "sql": ddl,
            "columns": [dict(zip(
                ("cid", "name", "type", "notnull", "default", "pk", "hidden"), row,
            )) for row in info],
        })
    return schema


def prepare_visible_views(connection, schema):
    """임시 뷰로 공통 숨김 제외 조건과 매출 조건을 강제한다."""
    customer_schema = next(item for item in schema if item["name"] == "customer")
    source = table("customer", *(column(item["name"]) for item in customer_schema["columns"]),
                   schema="main")
    visible_sql = str(visible_customers(source).compile(compile_kwargs={"literal_binds": True}))
    connection.execute("CREATE TEMP VIEW customer AS " + visible_sql)
    connection.execute(
        "CREATE TEMP VIEW orders AS SELECT * FROM main.orders "
        "WHERE customer_id IN (SELECT id FROM temp.customer) AND status != '취소'"
    )


def parse_model_json(content):
    """JSON 객체를 추출하며, 파싱 불가 응답은 원문 그대로 기록한다."""
    try:
        if not isinstance(content, str):
            raise ValueError("모델 응답 본문이 비어 있습니다.")
        decoder = json.JSONDecoder()
        for match in re.finditer(r"\{", content):
            try:
                value, _ = decoder.raw_decode(content[match.start():])
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict) and "sql" in value:
                return value
        raise ValueError("모델 응답에서 sql 항목이 있는 JSON 객체를 찾을 수 없습니다.")
    except ValueError:
        logger.error("모델 원문 응답: %s", content)
        raise


def generate_sql(question, schema):
    api_key = os.getenv("OPENAI_API_KEY") or dotenv_values(ENV_PATH, encoding="utf-8-sig").get("OPENAI_API_KEY")
    if not api_key or not api_key.strip():
        raise ValueError("프로젝트 .env 또는 앱 환경에 OPENAI_API_KEY가 설정되어 있지 않습니다.")
    today = datetime.now(KOREA_TIME).date().isoformat()
    system = (
        "당신은 SQLite 조회 SQL을 작성하는 한국어 데이터 분석가입니다. "
        "반드시 JSON 객체만 출력하세요. 설명문, 마크다운, 백틱을 JSON 밖에 쓰지 마세요. "
        '형식은 {"sql": "SELECT ...", "explanation": "한국어 집계 기준", "suggestion": ""}입니다. '
        "SQL은 단 하나의 SELECT 또는 SELECT로 끝나는 WITH 문이어야 합니다. "
        "아래 런타임 스키마의 실제 테이블과 칼럼만 사용하고 없는 칼럼을 상상하지 마세요. "
        "직원의 password_hash는 인증용 비밀 값이므로 조회·가공·필터·집계에 사용하지 마세요. "
        "인증 비밀 값을 요청하면 sql=null로 반환하고 직원 수 등 공개 가능한 업무 집계를 대안으로 제시하세요. "
        "사용자 질문에 나온 개념이나 기간 조건에 필요한 실제 칼럼이 없으면 sql을 null로 반환하세요. "
        "explanation에는 필요한 개념과 실제 스키마 사이의 차이를 한국어로 설명하고, "
        "suggestion에는 실제 데이터로 답할 수 있는 가장 관련 있는 대안 질문의 주제를 명사구로 쓰세요. "
        "'지원하지 않는 질문'이라는 일반 거절로 끝내지 마세요. "
        "hidden_customer는 고객 숨김 상태를 기록합니다. 탈퇴 기록이나 탈퇴일을 추정하지 마세요. "
        "탈퇴·해지 질문의 기록이 없고 hidden_customer가 존재하면 "
        "sql=null과 suggestion='전체 기간의 숨김(hidden) 고객 수'로 확인 질문을 제안하세요. "
        "숨김 날짜 칼럼이 없으면 숨김 고객을 연도나 월로 나누지 마세요. "
        "사용자가 숨김 고객 수 자체를 질문하면 hidden_customer에서 집계하세요. "
        "customer는 숨김 고객이 제외된 뷰이므로 숨김 수 집계에 customer를 조인하면 안 됩니다. "
        "결과 칼럼 별칭과 explanation은 한국어로 작성하세요. 숫자 결과를 예측하지 마세요. "
        "고객 수는 customer, 주문일은 orders.order_date, 주문 금액은 orders.total_amount를 사용하세요. "
        "customer와 orders는 숨김 고객을 제외하는 읽기 뷰이며 orders는 취소 주문도 제외합니다. "
        "COUNT, SUM, GROUP BY 등 집계는 SQL에서 하세요. 매출 합계는 COALESCE(SUM(...), 0)를 쓰세요. "
        "기간은 한국 날짜를 기준으로 시작 이상, 다음 기간 시작 미만으로 비교하세요. "
        "'신규 고객수'는 해당 기간에 등록된 고객 수입니다. 등록일 칼럼이 실제로 있으면 사용하세요. "
        "등록일이 없다면 주문일이나 grade를 등록일로 대체하지 말고 "
        '{"sql": null, "explanation": "고객 등록일 칼럼이 실제 DB에 없어 기간별 신규 고객 수를 계산할 수 없습니다.", '
        '"suggestion": "등록일 조건을 제외한 전체 고객 수"}를 반환하세요. '
        "개별 데이터 조회는 최대 200행으로 제한하세요. "
        f"오늘의 한국 날짜는 {today}입니다. 다음 JSON은 실제 DB에서 조회한 전체 스키마입니다.\n"
        + json.dumps(schema, ensure_ascii=False)
    )
    with OpenAI(api_key=api_key.strip(), timeout=45, max_retries=0) as client:
        response = client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": question}],
            response_format={"type": "json_object"},
            reasoning_effort="minimal",
        )
    return parse_model_json(response.choices[0].message.content)


def normalize_select(sql):
    if not isinstance(sql, str):
        raise ValueError("SQL은 문자열이어야 합니다.")
    tokens = [" " if token.startswith(("--", "/*")) else token
              for token in SQL_TOKENS.findall(sql)]
    while tokens and (tokens[0].isspace() or tokens[0] == ";"):
        tokens.pop(0)
    while tokens and (tokens[-1].isspace() or tokens[-1] == ";"):
        tokens.pop()
    normalized = "".join(tokens)
    if not re.match(r"(?:select|with)\b", normalized, re.IGNORECASE):
        raise ValueError("SELECT 조회문만 실행할 수 있습니다.")
    statements = [item for item in parse(normalized, read="sqlite") if item is not None]
    if len(statements) != 1 or not isinstance(statements[0], exp.Query):
        raise ValueError("단일 SELECT 조회문만 실행할 수 있습니다.")
    expression = statements[0]
    changed = False
    # main 접두사를 사용한 정상 SELECT도 제외 조건이 적용되는 임시 뷰로 연결한다.
    for source in expression.find_all(exp.Table):
        if source.name.lower() in ("customer", "orders") and source.db.lower() == "main":
            source.set("db", exp.to_identifier("temp"))
            changed = True
    for source in expression.find_all(exp.Column):
        if source.table.lower() in ("customer", "orders") and source.db.lower() == "main":
            source.set("db", exp.to_identifier("temp"))
            changed = True
    if changed:
        normalized = expression.sql(dialect="sqlite")
    return normalized


def select_authorizer(action, first, second, database, source):
    if (action == sqlite3.SQLITE_READ
            and (first or "").casefold() == "employees"
            and (second or "").casefold() == "password_hash"):
        return sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_READ and database == "main" and second:
        # 스키마를 명시해 숨김·취소 제외 뷰를 우회하는 조회도 차단한다.
        if first in ("customer", "orders") and source not in ("customer", "orders"):
            return sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_FUNCTION and (second or "").lower() in (
        "load_extension", "writefile", "readfile",
    ):
        return sqlite3.SQLITE_DENY
    allowed = (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE)
    return sqlite3.SQLITE_OK if action in allowed else sqlite3.SQLITE_DENY


def validate_sql_schema(connection, sql):
    """실제 스키마로 이름을 해석한다. 원래 SELECT의 데이터 조회는 실행하지 않는다.

    SQLite의 컴파일러로 검증하여 별칭·CTE·상관 서브쿼리와 실제 칼럼을 구분한다.
    EXPLAIN 결과 형식에 의존하지 않고 컴파일 성공 여부만 확인한다.
    """
    # SQLite가 없는 "칼럼명"을 문자열 리터럴로 받아들이는 호환 모드를 해제한다.
    connection.setconfig(sqlite3.SQLITE_DBCONFIG_DQS_DML, False)
    try:
        connection.execute("EXPLAIN " + sql).close()
    except sqlite3.OperationalError as error:
        if str(error).startswith(("no such table:", "no such column:", "ambiguous column name:")):
            raise SchemaValidationError(f"{type(error).__name__}: {error}") from error
        raise


def unavailable_answer(question, schema, explanation, suggestion=""):
    """사용 가능한 실제 개념을 확인하는 질문으로 응답한다. 대안 SQL은 자동 실행하지 않는다."""
    columns = {item["name"]: {entry["name"] for entry in item["columns"]} for item in schema}
    if any(word in question for word in ("탈퇴", "해지")) and "customer_id" in columns.get("hidden_customer", set()):
        suggestion = "전체 기간의 숨김(hidden) 고객 수"
    if not suggestion and "id" in columns.get("customer", set()):
        suggestion = "등록일 조건을 제외한 전체 고객 수"
    if not suggestion:
        suggestion = "현재 DB에 기록된 항목의 조회"
    if "지원하지 않" in explanation:
        explanation = "질문의 개념을 직접 기록한 테이블·칼럼이 확인되지 않습니다."
    return f"이 데이터로는 답할 수 없습니다. {explanation} 혹시 {suggestion.strip().rstrip('?.')}를 물으신 건가요?"


def execute_select(connection, sql):
    normalized = normalize_select(sql)
    connection.execute("PRAGMA query_only = ON")
    deadline = monotonic() + QUERY_TIMEOUT
    connection.set_progress_handler(lambda: int(monotonic() >= deadline), 1000)
    connection.set_authorizer(select_authorizer)
    try:
        validate_sql_schema(connection, normalized)
        cursor = connection.execute(normalized)
        rows = cursor.fetchmany(MAX_ROWS + 1)
        columns = [item[0] for item in cursor.description]
        return {"columns": columns, "rows": rows[:MAX_ROWS], "truncated": len(rows) > MAX_ROWS,
                "executed_sql": normalized}
    finally:
        connection.set_authorizer(None)
        connection.set_progress_handler(None, 0)


def ask_database(db_path, question):
    result = {"question": question, "sql": "", "explanation": "", "answer": "", "error": None,
              "columns": [], "rows": [], "truncated": False}
    try:
        question = question.strip()
        if not question or len(question) > 2000:
            raise ValueError("질문을 1자 이상 2,000자 이하로 입력해 주세요.")
        with closing(connect(db_path, readonly=True)) as connection:
            schema = actual_schema(connection)
            generated = generate_sql(question, schema)
            result["sql"] = generated.get("sql") or ""
            explanation = generated.get("explanation")
            if not isinstance(explanation, str):
                raise ValueError("모델 JSON의 explanation 항목은 한국어 문자열이어야 합니다.")
            result["explanation"] = explanation
            if generated.get("sql") is None:
                suggestion = generated.get("suggestion", "")
                if not isinstance(suggestion, str):
                    raise ValueError("모델 JSON의 suggestion 항목은 한국어 문자열이어야 합니다.")
                result["answer"] = unavailable_answer(question, schema, explanation, suggestion)
                result["explanation"] = ""
                return result
            prepare_visible_views(connection, schema)
            result.update(execute_select(connection, result["sql"]))
        rows, columns = result["rows"], result["columns"]
        if len(rows) == 1 and len(columns) == 1:
            value = rows[0][0]
            formatted = "값 없음" if value is None else str(value)
            if isinstance(value, (int, float)):
                formatted = format(value, ",")
            result["answer"] = f"조회 결과는 {formatted}입니다."
        else:
            result["answer"] = f"조회 결과 {len(rows):,}개 행을 표시합니다."
    except SchemaValidationError as error:
        result["error"] = f"{type(error).__name__}: {error}"
        logger.exception("실제 스키마 사전 검증 예외: %s, 생성 SQL: %s", result["error"], result["sql"])
        result["sql"] = ""
        result["explanation"] = ""
        result["answer"] = unavailable_answer(
            question, schema, "생성된 SQL의 테이블·칼럼이 실제 스키마와 일치하지 않습니다.",
        )
    except Exception as error:
        result["error"] = f"{type(error).__name__}: {error}"
        logger.exception("자연어 질문 처리 예외: %s", result["error"])
    return result
