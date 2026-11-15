"""실제 고객 입력·검색 정규화·SQL 검증 함수의 경계와 조합을 검증한다."""

from contextlib import closing
import sqlite3

import pytest

from database import address_region, compact_name
from natural_language import normalize_select
from routers.customers import customer_input


@pytest.mark.parametrize('grade', ['VIP', '일반', '신규'])
@pytest.mark.parametrize('name', ['홍길동', '  홍 길동  ', 'Élodie'])
@pytest.mark.parametrize('address', ['서울 강남구', '  부산 해운대구  '])
@pytest.mark.parametrize('email,expected', [
    ('user@example.com', 'user@example.com'),
    ('  USER@EXAMPLE.COM  ', 'user@example.com'),
    ('한글@example.com', '한글@example.com'),
])
def test_고객입력_유효조합과공백정리(grade, name, address, email, expected):
    values, error = customer_input(name, email, address, grade)
    assert error is None
    assert values == dict(name=name.strip(), email=expected, address=address.strip(), grade=grade)


@pytest.mark.parametrize('field', ['name', 'address'])
@pytest.mark.parametrize('blank', ['', ' ', '\t\n'])
@pytest.mark.parametrize('grade', ['VIP', '일반', '신규'])
def test_필수입력_빈문자와공백거부(field, blank, grade):
    data = dict(name='고객', address='서울', email='user@example.com', grade=grade)
    data[field] = blank
    _, error = customer_input(**data)
    assert error == '이름과 주소를 입력해 주세요.'


@pytest.mark.parametrize('email', ['', 'abc', '@example.com', 'a@', 'a b@example.com', 'a@@example.com', 'a@example..com'])
@pytest.mark.parametrize('grade', ['VIP', '일반', '신규'])
def test_이메일형식_거부(email, grade):
    _, error = customer_input('고객', email, '서울', grade)
    assert error == '올바른 이메일을 입력해 주세요.'


@pytest.mark.parametrize('grade', ['', 'vip', '일반고객', '탈퇴', '<script>', '0'])
def test_스키마에서허용하지않는등급_거부(grade):
    _, error = customer_input('고객', 'user@example.com', '서울', grade)
    assert error == '등급은 VIP, 일반, 신규 중에서 선택해 주세요.'


@pytest.mark.parametrize('value,expected', [(' 홍 길 동 ', '홍길동'), (' A\tB\nC ', 'abc'), ('Straße', 'strasse'), ('', '')])
def test_이름검색_유니코드공백정규화(value, expected):
    assert compact_name(value) == expected


@pytest.mark.parametrize('value,expected', [(' 서울 강남구 ', '서울'), ('부산', '부산'), ('\t\n', ''), ('', '')])
def test_주소지역_첫항목과빈값(value, expected):
    assert address_region(value) == expected


@pytest.mark.parametrize('sql,expected', [
    ('select 1', [(1,)]),
    ("SeLeCt 'DELETE; -- /* DROP */'", [('DELETE; -- /* DROP */',)]),
    ('SELECT 1 AS "UPDATE"', [(1,)]),
    ('WITH n AS (SELECT 2 AS x) SELECT x FROM n', [(2,)]),
    ("SELECT '고객 등록일' AS 결과", [('고객 등록일',)]),
    ('SELECT 1 UNION ALL SELECT 2', [(1,), (2,)]),
])
@pytest.mark.parametrize('prefix', ['', '  \n', '; ; ', '-- 설명\n', '/* INSERT DROP */\n'])
@pytest.mark.parametrize('suffix', ['', ';', '; -- 끝\n', ' /* UPDATE */ ; ; '])
def test_정상SELECT_표현조합을오탐없이허용(sql, expected, prefix, suffix):
    normalized = normalize_select(prefix + sql + suffix)
    with closing(sqlite3.connect(':memory:')) as connection:
        assert connection.execute(normalized).fetchall() == expected


@pytest.mark.parametrize('sql', [
    'INSERT INTO customer VALUES (1)', 'UPDATE customer SET name=1',
    'DELETE FROM customer', 'DROP TABLE customer', 'ALTER TABLE customer ADD x',
    'PRAGMA user_version=1', "ATTACH DATABASE 'x' AS x", 'CREATE TABLE x(a)',
    'SELECT 1; DELETE FROM customer',
])
@pytest.mark.parametrize('prefix', ['', ' ; /* 설명 */ ', '-- 설명\n'])
@pytest.mark.parametrize('lowercase', [False, True])
def test_변경과복수문장_정규화후차단(sql, prefix, lowercase):
    with pytest.raises(ValueError):
        normalize_select(prefix + (sql.lower() if lowercase else sql) + ';')
