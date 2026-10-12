import asyncio, json, sys
from typing import Any

QUESTION = sys.argv[1] if len(sys.argv) > 1 else "부산 3번 창고의 A-17 재고를 알려 줘"

async def main():
    from langchain_core.tools import tool
    from langchain_core.messages import ToolMessage
    from langchain_ollama import ChatOllama

    @tool
    def get_inventory(sku: str, warehouse_id: str) -> dict[str, Any]:
        """승인된 창고에서 특정 상품의 현재 가용 재고를 읽기 전용으로 조회합니다.
        sku에는 상품 SKU를 넣습니다.
        warehouse_id에는 사용자가 말한 창고 이름을 바꾸거나 비슷한 코드로 고치지 말고 그대로 넣습니다.
        허용 여부는 도구가 판단합니다. 허용 창고는 SEOUL-01, BUSAN-02뿐입니다.
        WAREHOUSE_NOT_ALLOWED가 반환되면 수량을 말하지 말고, 다시 호출하지 말고,
        SEOUL-01과 BUSAN-02 중 어느 창고인지 사용자에게 물어봅니다."""
        if warehouse_id not in ("SEOUL-01", "BUSAN-02"):
            return {"error": "WAREHOUSE_NOT_ALLOWED"}
        return {"sku": sku, "warehouse_id": warehouse_id,
                "available_quantity": 7, "checked_at": "2026-08-27T01:55:00+09:00"}

    llm = ChatOllama(model="qwen3.5:2b", temperature=0, reasoning=False)
    llm_with_tools = llm.bind_tools([get_inventory])

    print("[0] 사용자 질문:", QUESTION)
    r1 = await llm_with_tools.ainvoke(QUESTION)
    print("[1] 모델의 요청:", [(tc["name"], tc["args"]) for tc in r1.tool_calls])

    result = get_inventory.invoke(r1.tool_calls[0]["args"])
    print("[2] 하네스의 실행 결과:", json.dumps(result, ensure_ascii=False))

    r2 = await llm_with_tools.ainvoke(
        [r1, ToolMessage(content=json.dumps(result, ensure_ascii=False),
                         tool_call_id=r1.tool_calls[0]["id"])])
    print("[3] 결과를 읽은 최종 답:", r2.content[:120])

asyncio.run(main())
