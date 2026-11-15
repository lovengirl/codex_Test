"""외부 의존성 없이 인증과 모델 응답의 경계를 검증한다."""

from datetime import datetime, timedelta, timezone
import sqlite3
from types import SimpleNamespace

import jwt
import pytest
from starlette.requests import Request

import ai_insight
from authentication import ALGORITHM, COOKIE_NAME, get_optional_user
from customer_queries import customer_version
from natural_language import normalize_select, parse_model_json, select_authorizer, unavailable_answer


@pytest.mark.parametrize('subject', ['', '１', '-1', '1.0', '1 2', '1' * 20, str(2**63)])
def test_JWT_잘못된직원식별자는_DB조회전에차단(subject):
    key = 'unit-secret-' * 4
    now = datetime.now(timezone.utc)
    token = jwt.encode(dict(sub=subject, iat=now, exp=now + timedelta(minutes=5)), key, algorithm=ALGORITHM)
    request = Request({
        'type': 'http', 'headers': [(b'cookie', f'{COOKIE_NAME}={token}'.encode('ascii'))],
        'app': SimpleNamespace(state=SimpleNamespace(jwt_secret=key)),
    })

    class NoDatabase:
        def execute(self, *args, **kwargs):
            pytest.fail('잘못된 JWT로 DB를 조회했습니다.')

    assert get_optional_user(request, NoDatabase()) is None


@pytest.mark.parametrize('content', [None, 0, [], '', '설명만 있는 응답', '{"explanation":"SQL 없음"}'])
def test_환각_불완전모델JSON은_원문로그와예외(caplog, content):
    with pytest.raises(ValueError):
        parse_model_json(content)
    assert '모델 원문 응답:' in caplog.text


@pytest.mark.parametrize('sql', [None, 1, [], {}])
def test_SQL_문자열외입력거부(sql):
    with pytest.raises(ValueError, match='문자열'):
        normalize_select(sql)


@pytest.mark.parametrize('facts', [None, [], 1, '매출'])
def test_Insight_객체외집계값거부(facts):
    with pytest.raises(ValueError, match='객체'):
        ai_insight.validated_facts(facts)


def test_Insight_선택지표없음과_미등록필드는_수치로생성하지않음():
    assert ai_insight.validated_facts({'total_sales': None, 'invented': 100}) == {}
    assert ai_insight.make_snapshot({}, 1, 'unit-secret-' * 4) is None


@pytest.mark.parametrize('field', ['name', 'email', 'address', 'grade'])
def test_고객버전_변경필드감지와키순서독립(field):
    original = dict(id=1, name='고객', email='a@example.com', address='서울', grade='신규')
    assert customer_version(original) == customer_version(dict(reversed(list(original.items()))))
    assert customer_version(original) != customer_version(original | {field: '변경값'})


@pytest.mark.parametrize('table', ['customer', 'orders'])
def test_스키마를명시한_원본조회우회차단(table):
    assert select_authorizer(sqlite3.SQLITE_READ, table, 'id', 'main', None) == sqlite3.SQLITE_DENY
    assert select_authorizer(sqlite3.SQLITE_READ, table, 'id', 'main', table) == sqlite3.SQLITE_OK


def test_환각_빈스키마에서는_고객과숨김개념도상상하지않음():
    answer = unavailable_answer('탈퇴 고객수', [], '관련 기록이 없습니다.')
    assert '현재 DB에 기록된 항목의 조회' in answer
    assert '숨김' not in answer
