"""DB 접근 없이 서명된 집계값만으로 한국어 Insight를 생성한다."""

from datetime import datetime, timedelta, timezone
import json
import logging
import math
import os
import re

from dotenv import dotenv_values
import jwt
from openai import AsyncOpenAI

from authentication import ALGORITHM
from natural_language import ENV_PATH, MODEL


logger = logging.getLogger(__name__)
FACT_UNITS = {
    "customer_count": "명", "order_count": "건", "total_sales": "원", "average_order": "원",
    "weekly_change_percent": "%", "top_customer_share_percent": "%",
}
REFERENCE = re.compile(r"\{\{([a-z_]+)\}\}")
SPELLED_NUMBER = re.compile(
    r"(?:한|두|세|네|다섯|여섯|일곱|여덟|아홉|열|십|백|천|만|억|조|일|이|삼|사|오|육|칠|팔|구)"
    r"\s*(?:배|퍼센트|프로|명|건|원|주일|개월|년|달|일|주|번)|절반|반감|배증"
)


def validated_facts(metrics):
    if not isinstance(metrics, dict):
        raise ValueError("Insight 입력은 집계값 객체여야 합니다.")
    facts = {}
    for name in FACT_UNITS:
        value = metrics.get(name)
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError("Insight 집계값은 유한한 숫자여야 합니다.")
        if name != "weekly_change_percent" and value < 0:
            raise ValueError("Insight 집계값 범위를 확인해 주세요.")
        if name == "top_customer_share_percent" and value > 100:
            raise ValueError("상위 고객 비중은 백분율 범위여야 합니다.")
        facts[name] = value
    return facts


def make_snapshot(metrics, user_id, key):
    """이미 조회한 값을 사용자에게 묶어 전달한다. 생성 오류는 화면에 전파하지 않는다."""
    try:
        facts = validated_facts(metrics)
        if not facts:
            return None
        now = datetime.now(timezone.utc)
        return jwt.encode({
            "purpose": "dashboard_insight", "user_id": str(user_id), "facts": facts,
            "iat": now, "exp": now + timedelta(minutes=5),
        }, key, algorithm=ALGORITHM)
    except Exception:
        logger.exception("Insight 집계 스냅샷 생성 예외")
        return None


def read_snapshot(snapshot, access_token, key):
    """기존 JWT와 집계 서명만 확인한다. 인증을 위한 DB 재조회도 수행하지 않는다."""
    session = jwt.decode(access_token or "", key, algorithms=[ALGORITHM],
                         options={"require": ["sub", "iat", "exp"]})
    payload = jwt.decode(snapshot, key, algorithms=[ALGORITHM],
                         options={"require": ["purpose", "user_id", "facts", "iat", "exp"]})
    if payload["purpose"] != "dashboard_insight" or payload["user_id"] != session["sub"]:
        raise ValueError("Insight 집계 스냅샷의 사용자 또는 용도가 일치하지 않습니다.")
    return validated_facts(payload["facts"])


def format_fact(name, facts):
    return f"{facts[name]:,}{FACT_UNITS[name]}"


def validated_action(action, facts):
    """모델은 숫자 대신 참조를 출력하고, 서버가 입력 원값과 단위를 삽입한다."""
    if not isinstance(action, str) or not 1 <= len(action) <= 240 or not re.search("[가-힣]", action):
        raise ValueError("Insight 행동 제안은 한국어 문장이어야 합니다.")
    action = action.strip()
    literal = REFERENCE.sub("", action)
    if any(character.isnumeric() for character in literal) or SPELLED_NUMBER.search(literal):
        raise ValueError("Insight에 입력값 참조 이외의 수치가 포함되어 있습니다.")
    if "{" in literal or "}" in literal or "\n" in literal or not action.endswith("."):
        raise ValueError("Insight 참조 또는 문장 형식을 확인해 주세요.")
    if re.search(r"[.!?]", action[:-1]):
        raise ValueError("행동 제안은 한 문장이어야 합니다.")
    references = REFERENCE.findall(action)
    if not references or any(name not in facts for name in references):
        raise ValueError("Insight는 제공된 집계값만 참조해야 합니다.")
    if re.search(r"\}\}\s*(?:원|명|건|%|퍼센트)", action):
        raise ValueError("Insight의 수치 단위는 서버에서 지정합니다.")
    return REFERENCE.sub(lambda match: format_fact(match.group(1), facts), action)


def insight_sentences(action, facts):
    if "weekly_change_percent" in facts:
        change = f"전주 대비 매출 변화율은 {format_fact('weekly_change_percent', facts)}입니다."
    else:
        change = "전주 대비 변화는 주간 비교 집계값이 없어 확인할 수 없습니다."
    if "top_customer_share_percent" in facts:
        concentration = f"상위 고객의 매출 비중은 {format_fact('top_customer_share_percent', facts)}입니다."
    else:
        concentration = "상위 고객 비중은 고객별 매출 집계값이 없어 확인할 수 없습니다."
    return [change, concentration, validated_action(action, facts)]


async def generate_insight(facts):
    """기존 집계만 모델에 전달한다. 오류·환각 검출 시 카드용 응답을 생략한다."""
    try:
        facts = validated_facts(facts)
        api_key = os.getenv("OPENAI_API_KEY") or dotenv_values(ENV_PATH, encoding="utf-8-sig").get("OPENAI_API_KEY")
        if not api_key or not facts:
            return None
        system = (
            "당신은 매출 대시보드의 한국어 행동 제안을 작성합니다. JSON 객체만 출력하세요. "
            '형식은 {"action": "총매출 {{total_sales}}을 기준으로 고객 주문 현황을 점검하세요."}입니다. '
            "action은 마침표로 끝나는 짧은 한국어 제안 한 문장이어야 합니다. "
            "주어진 집계 이외의 정보는 추정하거나 수치로 만들지 마세요. 증가·감소·고객 집중도를 단정하지 마세요. "
            "전주 대비 변화와 상위 고객 비중은 서버가 실제 입력 유무에 맞춰 따로 표시합니다. "
            "숫자, 한글로 풀어 쓴 숫자, 목표치, 예측치, 배수는 직접 쓰지 마세요. "
            "수치를 인용하려면 반드시 제공된 키의 {{키}} 참조를 사용하세요. 참조 뒤에 단위를 쓰지 마세요. "
            "서버가 실제 입력값과 단위를 삽입합니다. 제공된 키 중 하나 이상을 참조하세요. "
            "새 DB 조회나 SQL을 생성하지 마세요. 코드펜스와 마크다운을 쓰지 마세요."
        )
        async with AsyncOpenAI(api_key=api_key.strip(), timeout=8, max_retries=0) as client:
            response = await client.chat.completions.create(
                model=MODEL,
                messages=[{"role": "system", "content": system},
                          {"role": "user", "content": json.dumps(facts, ensure_ascii=False)}],
                response_format={"type": "json_object"}, reasoning_effort="minimal", max_completion_tokens=1024,
            )
        content = json.loads(response.choices[0].message.content)
        return insight_sentences(content["action"], facts)
    except Exception:
        logger.exception("Insight 생성 또는 수치 검증 예외, 카드 생략")
        return None
