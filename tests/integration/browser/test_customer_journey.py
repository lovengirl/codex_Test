"""느린 브라우저 테스트는 가장 중요한 사용자 여정 하나만 유지한다."""

import re

from playwright.sync_api import expect


def test_로그인_고객등록_검색_로그아웃(browser_page):
    page = browser_page
    page.goto('/auth/register')
    page.get_by_label('이름', exact=True).fill('UI검증직원')
    page.get_by_label('이메일', exact=True).fill('ui-staff@example.com')
    page.get_by_label('주소', exact=True).fill('서울')
    page.get_by_label('비밀번호', exact=True).fill('Ui-test-123!')
    page.get_by_role('button', name='회원가입', exact=True).click()
    expect(page.get_by_role('status')).to_contain_text('회원가입이 완료되었습니다')

    page.get_by_label('이메일', exact=True).fill('ui-staff@example.com')
    page.get_by_label('비밀번호', exact=True).fill('Ui-test-123!')
    page.get_by_role('button', name='로그인', exact=True).click()
    expect(page).to_have_url(re.compile(r'/customers$'))
    expect(page.locator('header')).to_contain_text('UI검증직원')

    page.get_by_role('link', name='고객 등록', exact=True).click()
    page.get_by_label('이름', exact=True).fill('브라우저검증고객')
    page.get_by_label('이메일', exact=True).fill('ui-customer@example.com')
    page.get_by_label('주소', exact=True).fill('부산 해운대구')
    page.get_by_role('combobox').select_option('VIP')
    page.get_by_role('button', name='등록', exact=True).click()
    expect(page.locator('main')).to_contain_text('브라우저검증고객')
    expect(page.locator('main')).to_contain_text('ui-customer@example.com')

    page.get_by_role('link', name='고객 목록', exact=True).first.click()
    page.get_by_label('이름 또는 이메일 검색').fill('없는고객')
    page.get_by_role('button', name='검색', exact=True).click()
    expect(page.locator('main')).to_contain_text('검색 결과가 없습니다.')
    page.get_by_label('이름 또는 이메일 검색').fill('브라우저검증고객')
    page.get_by_role('button', name='검색', exact=True).click()
    expect(page.locator('tbody tr')).to_have_count(1)
    expect(page.locator('tbody tr')).to_contain_text('ui-customer@example.com')

    page.get_by_role('button', name='로그아웃', exact=True).click()
    expect(page.get_by_role('heading', name='로그인', exact=True)).to_be_visible()
    page.goto('/customers')
    expect(page.get_by_role('heading', name='로그인', exact=True)).to_be_visible()
    expect(page.locator('main')).not_to_contain_text('브라우저검증고객')
    print('[통과] 실제 Chromium: 로그인 → 고객 등록 → 검색 → 로그아웃 → 재접근 차단')
