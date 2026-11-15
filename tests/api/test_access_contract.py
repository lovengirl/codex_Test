"""정상·미인증·외부 출처 요청과 페이지 경계를 명시적으로 고정한다."""

import pytest
from sqlalchemy import text

from authentication import COOKIE_NAME, create_token


@pytest.fixture
def staff_client(client):
    with client.app.state.engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO employees VALUES (1, 'API직원', 'api@example.com', '서울', 'test-hash')"
        ))
        connection.execute(text(
            "INSERT INTO customer VALUES (:id, :name, :email, '서울', '일반')"
        ), [dict(id=i, name=f'경계고객{i}', email=f'boundary{i}@example.com') for i in range(1, 22)])
    client.cookies.set(COOKIE_NAME, create_token(1, client.app.state.jwt_secret))
    return client


@pytest.mark.parametrize('path', ['/customers', '/dashboard', '/dashboard/export/csv'])
def test_보호경로_미인증401(client, path):
    response = client.get(path, headers={'Accept': 'application/json'})
    assert response.status_code == 401
    assert response.json() == {'detail': '로그인이 필요합니다.'}


@pytest.mark.parametrize('path', ['/customers', '/dashboard', '/dashboard/export/csv'])
def test_보호경로_인증200(staff_client, path):
    assert staff_client.get(path).status_code == 200


@pytest.mark.parametrize('path', ['/customers', '/auth/logout', '/dashboard/ask'])
def test_외부출처_변경요청403과세션유지(staff_client, path):
    response = staff_client.post(path, headers={'Origin': 'https://external.example'})
    assert response.status_code == 403
    assert response.json() == {'detail': '요청 출처를 확인해 주세요.'}
    assert staff_client.get('/customers').status_code == 200
    with staff_client.app.state.engine.connect() as connection:
        assert connection.scalar(text('SELECT COUNT(*) FROM customer')) == 21


@pytest.mark.parametrize('page,status,ids', [
    (0, 422, None), (1, 200, list(range(1, 21))), (2, 200, [21]), (3, 404, None),
])
def test_첫페이지_마지막페이지_범위밖(staff_client, page, status, ids):
    response = staff_client.get('/customers', params={'page': page})
    assert response.status_code == status
    if ids is not None:
        assert [row['id'] for row in response.context['items']] == ids
        assert response.context['total'] == 21
        assert response.context['total_pages'] == 2
