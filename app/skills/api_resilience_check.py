import logging
from langchain_core.tools import tool
from langchain_core.messages import HumanMessage
from app.core.llm import init_llm

logger = logging.getLogger(__name__)

@tool
def api_resilience_check(code_diff: str, full_content: str) -> str:
    """
    检查代码变更中调用外部 API 时，是否存在容错性差、无超时、死锁、无重试等网络弹性漏洞。
    """
    logger.info("[Skill] api_resilience_check triggered (LLM-backed).")
    
    # Fast path: If the code doesn't make network calls, skip LLM call
    lower_diff = code_diff.lower()
    keywords = ['requests.', 'httpx', 'aiohttp', 'urllib', '.get(', '.post(', 'http']
    if not any(k in lower_diff for k in keywords):
        return ""
        
    llm = init_llm()
    prompt = (
        f"你是一名高级服务端稳定性架构专家。请专注审核以下代码变更中的**外部网络调用容灾与弹性 (API Resilience)** 漏洞。\n"
        f"你需要检查：\n"
        f"1. 发起网络请求（如 requests, httpx 等）是否缺少明确的 timeout 参数会导致线程枯竭。\n"
        f"2. 是否存在缺乏 try...except 网络异常保护机制，导致上层级联崩溃。\n"
        f"3. 缺少基础的 HTTP 状态码校验（如直接调用 .json() 前不检查 status_code）。\n\n"
        f"如果网络请求处理得非常严密，请只返回严格的空字符串。\n"
        f"如果发现漏洞，请精简地指明出错的原因及行号所在（格式：'第X行网络调用隐患：你的解释'）。\n\n"
        f"【完整参考文件】:\n{full_content}\n\n"
        f"【针对性变更 Diff】:\n{code_diff}\n"
    )
    
    try:
        response = llm.invoke([HumanMessage(content=prompt)])
        result = response.content.strip()
        if not result or result.lower() in ["无", "none", "ok", "没有网络调用隐患", "未发现问题"]:
            return ""
        return result
    except Exception as e:
        logger.error(f"api_resilience_check LLM call failed: {e}")
        return ""
