import logging
from langchain_core.tools import tool
from langchain_core.messages import HumanMessage
from app.core.llm import init_llm

logger = logging.getLogger(__name__)

@tool
def idempotency_check(code_diff: str, full_content: str) -> str:
    """
    检查代码变更中是否存在破坏幂等性的业务逻辑漏洞。
    特别针对涉及回调处理、数据库增扣、支付状态流转的场景。
    """
    logger.info("[Skill] idempotency_check triggered (LLM-backed).")
    
    # Fast path: If the code doesn't look like it mutates state or handles callbacks, skip LLM call
    lower_diff = code_diff.lower()
    keywords = ['update', 'insert', 'callback', 'balance', 'amount', 'status', 'pay', 'refund', '+=', '-=']
    if not any(k in lower_diff for k in keywords):
        return ""
        
    llm = init_llm()
    prompt = (
        f"你是一名高级安全与业务架构专家。请专注审核以下代码变更中的**幂等性 (Idempotency) 与并发防重放**漏洞。\n"
        f"你需要检查：\n"
        f"1. 回调接口/状态流转是否缺少前置状态判定（如支付成功后未拦阻重复回调）。\n"
        f"2. 增减余额或库存时，是否缺少防重放 ID (Idempotency Key) 或唯一约束判定。\n\n"
        f"如果完全没有幂等性漏洞，请只返回严格的空字符串。\n"
        f"如果发现漏洞，请精简地指明出错的原因及行号所在（格式：'第X行存在幂等性缺失：你的解释'）。\n\n"
        f"【完整参考文件】:\n{full_content}\n\n"
        f"【针对性变更 Diff】:\n{code_diff}\n"
    )
    
    try:
        response = llm.invoke([HumanMessage(content=prompt)])
        result = response.content.strip()
        if not result or result.lower() in ["无", "none", "ok", "没有发现漏洞", "没有发现幂等性漏洞"]:
            return ""
        return result
    except Exception as e:
        logger.error(f"idempotency_check LLM call failed: {e}")
        return ""
