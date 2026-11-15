"""HTTP 없이 함수 입력과 반환값을 검증한다."""


import pytest


import ai_insight


ACTION = "총매출 {{total_sales}}을 참고해 고객의 주문 내역과 후속 상담 대상을 점검하세요."

FACTS = {"customer_count": 100, "order_count": 200, "total_sales": 100000, "average_order": 500}


def test_주간변화와집중도는_이미주어진값만인용():
    facts = {**FACTS, "weekly_change_percent": -12.5, "top_customer_share_percent": 42}
    sentences = ai_insight.insight_sentences(ACTION, facts)
    assert sentences == [
        "전주 대비 매출 변화율은 -12.5%입니다.",
        "상위 고객의 매출 비중은 42%입니다.",
        "총매출 100,000원을 참고해 고객의 주문 내역과 후속 상담 대상을 점검하세요.",
    ]


@pytest.mark.parametrize("action", [
    "매출을 20% 늘리세요.", "총매출 100000원을 참고하세요.",
    "매출을 두 배로 늘리세요.", "이십 퍼센트 성장을 목표로 하세요.",
    "두 달 안에 고객에게 연락하세요.", "Ⅲ배 성장을 검토하세요.",
    "{{missing_value}}을 확인하세요.", "{{weekly_change_percent}}를 확인하세요.",
    "총매출 {{total_sales}}%를 기준으로 점검하세요.",
    "총매출 {{total_sales}}입니다. 고객에게 연락하세요.",
    "{{total_sales}}을 확인하세요", "{total_sales}을 확인하세요.",
    None, "", "Review {{total_sales}}.",
])
def test_숫자생성과_없는입력참조_문장오류는_카드표시전차단(action):
    with pytest.raises(ValueError):
        ai_insight.insight_sentences(action, FACTS)


@pytest.mark.parametrize("metrics", [
    {"total_sales": float("nan")}, {"total_sales": float("inf")}, {"total_sales": True},
    {"total_sales": -1}, {"top_customer_share_percent": 101}, {"total_sales": "100"},
])
def test_비정상집계는_스냅샷생성을생략(metrics):
    assert ai_insight.make_snapshot(metrics, 1, "a-secret-key-of-at-least-32-characters") is None

