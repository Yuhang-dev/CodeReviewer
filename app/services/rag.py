import logging
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langgraph.graph import StateGraph, END
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams
from typing import TypedDict, Annotated
from app.core.config import settings

logger = logging.getLogger(__name__)

COLLECTION_NAME = "code_guidelines"

class AgentState(TypedDict):
    diff_text: str
    review_result: str

def init_qdrant() -> QdrantClient:
    """Initialize connection to Qdrant vector database."""
    client = QdrantClient(url=settings.QDRANT_URL, api_key=settings.QDRANT_API_KEY)
    
    # Ensure collection exists
    if not client.collection_exists(collection_name=COLLECTION_NAME):
        logger.info(f"Creating Qdrant collection: {COLLECTION_NAME}")
        client.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config=VectorParams(size=512, distance=Distance.COSINE),
        )
    return client

# Singleton clients
qdrant_client = init_qdrant()
# Fast, local, private embeddings
embeddings_model = HuggingFaceEmbeddings(model_name="BAAI/bge-small-zh-v1.5")


def init_llm() -> ChatOpenAI:
    return ChatOpenAI(
        api_key=settings.DEEPSEEK_API_KEY, 
        base_url=settings.DEEPSEEK_BASE_URL,
        model="deepseek-chat"
    )


def ingest_knowledge(content: str, metadata: dict = None) -> int:
    """
    Chunks the input markdown text, generates embeddings, and saves to Qdrant.
    """
    logger.info("Ingesting knowledge text...")
    
    # Text splitting
    text_splitter = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=50)
    chunks = text_splitter.split_text(content)
    
    if not chunks:
        return 0

    # Embed and upsert
    vectors = embeddings_model.embed_documents(chunks)
    
    payloads = [{"content": chunk, **(metadata or {})} for chunk in chunks]
    ids = list(range(len(chunks))) # In real app, use UUIDs
    
    # For MVP we just use sequential IDs. Existing IDs will be overwritten or we can use UUIDs:
    import uuid
    ids = [str(uuid.uuid4()) for _ in chunks]

    qdrant_client.upsert(
        collection_name=COLLECTION_NAME,
        points=[
            {"id": id_, "vector": vector, "payload": payload}
            for id_, vector, payload in zip(ids, vectors, payloads)
        ],
    )
    
    logger.info(f"Ingested {len(chunks)} chunks successfully.")
    return len(chunks)


def retrieve_guidelines(query_text: str) -> str:
    """
    Search Qdrant for relevant coding standards.
    """
    try:
        query_vector = embeddings_model.embed_query(query_text)
        search_result = qdrant_client.query_points(
            collection_name=COLLECTION_NAME,
            query=query_vector,
            limit=3
        ).points
        # Extract text from matched payloads
        guidelines = [hit.payload.get("content", "") for hit in search_result]
        return "\n\n".join(guidelines)
    except Exception as e:
        logger.error(f"Vector search failed: {e}")
        return ""


def review_code_step(state: AgentState):
    """LangGraph node: Reviews code taking constraints/RAG context into account."""
    logger.info("Executing review_code_step. Retrieving knowledge...")
    
    # 1. Retrieve Knowledge
    retrieved_context = retrieve_guidelines(state['diff_text'])
    
    context_str = ""
    if retrieved_context.strip():
        context_str = f"【企业代码规范参考】:\n{retrieved_context}\n\n请务必检查上述代码是否可能违反了上述规范要求。\n\n"
    
    logger.info("Invoking LLM...")
    llm = init_llm()
    try:
        prompt = (
            f"请使用**中文**审查以下代码变更，重点针对潜在的 Bug、代码安全性、可读性和性能问题提供专业的反馈和改进建议。\n"
            f"{context_str}"
            f"【代码 Diff 变更】:\n{state['diff_text']}\n\n"
            f"请保持排版清晰（必要时使用 Markdown）:"
        )
        response = llm.invoke([HumanMessage(content=prompt)])
        
        logger.info(f"Code Review completed by LLM.")
        return {"review_result": response.content}
    except Exception as e:
        logger.error(f"LLM API Error during code review: {e}")
        return {"review_result": f"LLM Connection Error: {str(e)}"}


def build_review_graph() -> StateGraph:
    workflow = StateGraph(AgentState)
    workflow.add_node("review_code", review_code_step)
    workflow.set_entry_point("review_code")
    workflow.add_edge("review_code", END)
    return workflow.compile()

graph = build_review_graph()

def trigger_review_pipeline(diff_list: list[str]):
    logger.info(f"Triggering Agentic RAG pipeline for {len(diff_list)} chunks...")
    combined_diff = "\n".join(diff_list)
    initial_state = {"diff_text": combined_diff, "review_result": ""}
    return graph.invoke(initial_state)
