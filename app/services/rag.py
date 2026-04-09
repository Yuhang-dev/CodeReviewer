import logging
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage
from langgraph.graph import StateGraph, END
from qdrant_client import QdrantClient
from typing import TypedDict, Annotated
from app.core.config import settings

logger = logging.getLogger(__name__)

# State definition for LangGraph
class AgentState(TypedDict):
    diff_text: str
    review_result: str

def init_qdrant() -> QdrantClient:
    """Initialize connection to Qdrant vector database."""
    logger.info("Connecting to Qdrant...")
    client = QdrantClient(url=settings.QDRANT_URL, api_key=settings.QDRANT_API_KEY)
    return client

def init_llm() -> ChatOpenAI:
    """Initialize the DeepSeek LLM via Langchain's OpenAI wrapper."""
    return ChatOpenAI(
        api_key=settings.DEEPSEEK_API_KEY, 
        base_url=settings.DEEPSEEK_BASE_URL,
        model="deepseek-chat" # or deepseek-coder
    )

# --- LangGraph Nodes (Placeholder) ---

def review_code_step(state: AgentState):
    """Placeholder node for reviewing code via LLM."""
    logger.info("Executing review_code_step in LangGraph...")
    # llm = init_llm()
    # response = llm.invoke([HumanMessage(content=f"Review this diff:\\n{state['diff_text']}")])
    # return {"review_result": response.content}
    return {"review_result": "Simulated AI Code Review Feedback..."}

def build_review_graph() -> StateGraph:
    """Constructs the Agentic RAG review workflow."""
    workflow = StateGraph(AgentState)
    
    workflow.add_node("review_code", review_code_step)
    workflow.set_entry_point("review_code")
    workflow.add_edge("review_code", END)
    
    return workflow.compile()

graph = build_review_graph()

def trigger_review_pipeline(diff_list: list[str]):
    """Entry point to run the langgraph pipeline."""
    logger.info(f"Triggering Agentic RAG pipeline for {len(diff_list)} diff chunks...")
    
    combined_diff = "\n".join(diff_list)
    initial_state = {"diff_text": combined_diff, "review_result": ""}
    
    result_state = graph.invoke(initial_state)
    logger.info(f"Graph execution complete. Result: {result_state.get('review_result')}")
    return result_state
