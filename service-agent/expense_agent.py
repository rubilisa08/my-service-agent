"""사내 회계 규정·경비 정산 가이드 챗봇 실습 코드.

외부 규정 검색 서버·ERP를 호출하지 않고, 호출되었다고 가정해 가상 규정 데이터로 결과를 돌려준다.
사용법: python expense_agent.py "질문" ["이어지는 사용자 답변" ...]
환경변수 FAIL_SEARCH=1 이면 규정 검색 도구가 TOOL_ERROR를 돌려준다(도구 오류 테스트용).
"""
import asyncio, json, os, sys
from datetime import datetime
from typing import Literal

CHECKED_AT = "2026-10-12T16:30:00+09:00"

# 가상 사내 회계 규정 (실제 규정이 아니라 실습용 예시)
POLICIES = [
    {"clause_title": "경비 규정 제3조(교통비)", "keywords": ["택시", "교통"],
     "condition": "업무 이동 택시비는 오후 10시 이후 또는 출장 중일 때 청구 가능, 1회 50,000원 한도, 영수증 필수",
     "effective_date": "2026-01-01", "source": "경비규정.pdf p.3"},
    {"clause_title": "경비 규정 제5조(야근 식대)", "keywords": ["야근"],
     "condition": "오후 9시 이후 근무 시 1인당 15,000원 한도",
     "effective_date": "2026-01-01", "source": "경비규정.pdf p.5"},
    {"clause_title": "경비 규정 제6조(회식비)", "keywords": ["회식"],
     "condition": "1인당 50,000원 한도, 팀장 사전 승인 필요",
     "effective_date": "2026-01-01", "source": "경비규정.pdf p.6"},
    {"clause_title": "경비 규정 제7조(출장 식대)", "keywords": ["출장", "식대"],
     "condition": "출장 중 1일 30,000원 한도, 같은 식사를 야근 식대와 중복 청구할 수 없음",
     "effective_date": "2026-01-01", "source": "경비규정.pdf p.7"},
    {"clause_title": "경비 규정 제8조(해외 출장 숙박)", "keywords": ["해외", "숙박"],
     "condition": "1박 200,000원 한도, 숙박 영수증과 출장 승인서 필요",
     "effective_date": "2026-01-01", "source": "경비규정.pdf p.8"},
    {"clause_title": "경비 규정 제10조(증빙)", "keywords": ["영수증", "증빙", "공급가액"],
     "condition": "적격 증빙(세금계산서, 카드 영수증) 필요, 공급가액이 없는 영수증은 보완 후 청구",
     "effective_date": "2026-01-01", "source": "경비규정.pdf p.10"},
    {"clause_title": "경비 규정 제11조(분할 청구 금지)", "keywords": ["나눠", "쪼개", "분할"],
     "condition": "한도를 피하려고 한 건의 지출을 여러 영수증으로 나눠 청구할 수 없음",
     "effective_date": "2026-01-01", "source": "경비규정.pdf p.11"},
]

# 항목별 한도: (금액, 기준) 기준이 "person"이면 1인당, "item"이면 건당
LIMITS = {"야근식대": (15000, "person"), "회식비": (50000, "person"), "접대비": (100000, "item"),
          "국내출장숙박": (100000, "item"), "해외출장숙박": (200000, "item"), "교통비": (50000, "item")}
LIMIT_CLAUSE = {"야근식대": "경비 규정 제5조(야근 식대)", "회식비": "경비 규정 제6조(회식비)",
                "접대비": "경비 규정 제9조(접대비)", "국내출장숙박": "경비 규정 제8조(국내 출장 숙박)",
                "해외출장숙박": "경비 규정 제8조(해외 출장 숙박)", "교통비": "경비 규정 제3조(교통비)"}
Category = Literal["야근식대", "회식비", "접대비", "국내출장숙박", "해외출장숙박", "교통비"]


def log(tag, text):
    print(f"[{tag}] {text}", flush=True)


async def main():
    from langchain_core.tools import tool
    from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
    from langchain_ollama import ChatOllama

    @tool
    def search_expense_policy(query: str) -> dict:
        """사내 회계 규정 문서에서 질문과 관련된 조항을 읽기 전용으로 검색합니다.
        경비 기준, 증빙 요건, 청구 절차처럼 규정 내용을 묻는 질문에 씁니다.
        특정 금액이 한도 안인지 판단할 때, 청구서 작성, 승인·이체 요청에는 쓰지 않습니다.
        query에는 사용자 질문을 바꾸지 말고 그대로 넣습니다.
        NO_RESULT가 오면 관련 없는 조항으로 답하지 말고 재무팀 이관을 제안합니다.
        TOOL_ERROR가 오면 한 번만 다시 호출하고, 또 실패하면 답을 보류합니다."""
        if os.environ.get("FAIL_SEARCH") == "1":
            return {"error": "TOOL_ERROR"}
        # 관련성 판단은 하네스가 한다: 키워드가 하나도 맞지 않으면 결과에서 뺀다
        hits = [p for p in POLICIES if any(k in query for k in p["keywords"])]
        if not hits:
            return {"error": "NO_RESULT", "checked_at": CHECKED_AT}
        return {"results": [{k: p[k] for k in ("clause_title", "condition", "effective_date", "source")}
                            for p in hits[:3]], "checked_at": CHECKED_AT}

    @tool
    def check_expense_limit(category: Category, amount: int, headcount: int = 1) -> dict:
        """경비 항목·금액·인원이 규정 한도 안인지 읽기 전용으로 계산합니다.
        사용자가 실제 금액을 말하며 한도 안인지 물을 때만 씁니다.
        금액이나 인원이 빠졌으면 호출하지 말고 사용자에게 물어봅니다. 값을 추측하지 않습니다.
        category가 목록에 없으면 비슷한 항목으로 바꾸지 말고 어떤 항목인지 물어봅니다."""
        if category not in LIMITS:
            return {"error": "INVALID_CATEGORY"}
        if amount < 1 or headcount < 1:
            return {"error": "MISSING_FIELD"}
        limit, basis = LIMITS[category]
        total = limit * headcount if basis == "person" else limit
        return {"within_limit": amount <= total, "limit_per_person": limit if basis == "person" else None,
                "total_limit": total, "over_amount": max(0, amount - total),
                "clause_title": LIMIT_CLAUSE[category], "checked_at": CHECKED_AT}

    @tool
    def draft_expense_claim(category: Category, amount: int, purpose: str,
                            receipt_attached: bool, user_confirmed: bool, headcount: int = 1) -> dict:
        """본인 명의 경비 청구서 초안을 임시 저장합니다. 제출·승인은 하지 않습니다.
        먼저 초안 내용(항목, 금액, 인원, 목적, 영수증 여부)을 사용자에게 보여주고,
        사용자가 확인했을 때만 user_confirmed=true로 호출합니다.
        최종 제출·승인, 다른 사람 명의, 영수증을 나누거나 꾸미는 요청에는 쓰지 않습니다.
        TOOL_ERROR가 오면 다시 호출하지 말고 재무팀 이관을 제안합니다."""
        if not user_confirmed:
            return {"error": "NOT_CONFIRMED"}
        limit, basis = LIMITS[category]
        total = limit * headcount if basis == "person" else limit
        reasons = []
        if amount > total:
            reasons.append(f"한도 초과 {amount - total}원")
        if not receipt_attached:
            reasons.append("영수증 없음")
        return {"claim_no": "EXP-2026-0142", "status": "HOLD" if reasons else "DRAFT",
                "hold_reason": ", ".join(reasons) or None,
                "next_step": "ERP에서 본인이 내용을 확인한 뒤 제출하면 재무팀 승인 단계로 넘어갑니다"}

    @tool
    def escalate_to_finance(reason_code: Literal["NO_POLICY", "EXCEPTION_APPROVAL", "HOLD_REVIEW",
                                                 "TOOL_FAILURE", "USER_REQUEST"], summary: str) -> dict:
        """챗봇이 처리할 수 없는 건을 재무팀 문의 큐에 넘깁니다.
        규정에 없는 항목, 한도 예외 승인, 보류 검토, 도구 오류 반복일 때 사용자가 이관에 동의한 뒤에만 씁니다.
        편법·우회 요청은 이관하지 않고 거절합니다. summary에는 개인정보를 넣지 않습니다."""
        return {"ticket_no": "FIN-2026-0087", "expected_reply": "영업일 1일 이내",
                "channel": "재무팀 슬랙 #finance-help"}

    tools = {t.name: t for t in (search_expense_policy, check_expense_limit, draft_expense_claim, escalate_to_finance)}
    system = SystemMessage(
        "너는 사내 경비 청구와 회계 규정을 안내하는 재무 어시스턴트다.\n"
        "1. 규정 내용은 search_expense_policy 결과에, 한도 판단은 check_expense_limit 결과에만 근거한다. "
        "도구 결과에 없는 금액·한도·조건은 말하지 않는다.\n"
        "2. 근거를 찾지 못했거나 규정에 없는 예외라면 확정하지 말고 '재무팀 담당자 확인 필요'라고 답한 뒤, "
        "사용자가 원하면 escalate_to_finance로 넘긴다.\n"
        "3. 청구서는 초안만 만든다. 초안 내용을 사용자에게 보여주고 확인을 받은 뒤에만 draft_expense_claim을 호출한다.\n"
        "4. 최종 제출·승인·이체, 다른 직원 정보 조회, 증빙을 나누거나 꾸미는 요청은 거절하고 정상 절차를 안내한다.\n"
        "5. 답변 끝에는 근거로 쓴 규정 조항명과 확인 시각을 적는다.")
    llm = ChatOllama(model="qwen3.5:2b", temperature=0, reasoning=False, num_predict=512)
    llm = llm.bind_tools(list(tools.values()))

    turns = sys.argv[1:] or ["팀원 4명이서 야근 식대로 총 6만 원 썼는데 한도 안이야?"]
    messages = [system]
    for user_text in turns:
        log("USER", user_text)
        messages.append(HumanMessage(user_text))
        for _ in range(6):  # 한 사용자 턴에서 도구 호출은 최대 6번
            r = await llm.ainvoke(messages)
            messages.append(r)
            if not r.tool_calls:
                log("ANSWER", r.content.strip())
                break
            for tc in r.tool_calls:
                log("CALL", f"{tc['name']} {json.dumps(tc['args'], ensure_ascii=False)}")
                try:
                    result = tools[tc["name"]].invoke(tc["args"])
                except Exception as e:  # 스키마 위반 등은 하네스가 막고 오류로 돌려준다
                    result = {"error": "SCHEMA_REJECTED", "detail": type(e).__name__}
                log("RESULT", json.dumps(result, ensure_ascii=False))
                messages.append(ToolMessage(json.dumps(result, ensure_ascii=False), tool_call_id=tc["id"]))
        else:
            log("ANSWER", "(도구 호출 6회 한도에 도달해 중단)")

asyncio.run(main())
