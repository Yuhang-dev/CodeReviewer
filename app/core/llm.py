from langchain_openai import ChatOpenAI
from app.core.config import settings

def init_llm() -> ChatOpenAI:
    """
    Initializes and returns the main ChatOpenAI client.
    Can be imported by both the RAG service and individual Skills 
    to prevent circular dependencies.
    """
    import httpx
    http_client = httpx.Client(verify=False)
    return ChatOpenAI(
        api_key=settings.DEEPSEEK_API_KEY, 
        base_url=settings.DEEPSEEK_BASE_URL,
        model="deepseek-chat",
        http_client=http_client
    )
