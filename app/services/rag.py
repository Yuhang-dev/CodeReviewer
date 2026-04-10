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
    full_files_context: str
    tier: str
    review_context: str
    review_focus: str
    review_result: str
    chat_query: str
    chat_response: str

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
    
    tier = state.get("tier", "Tier-B")
    review_context = state.get("review_context", "")
    review_focus = state.get("review_focus", "")
    
    if tier == "TIER-C":
        logger.info("Tier-C detected. Skipping LLM code review (auto LGTM).")
        return {"review_result": "[]"}
        
    logger.info(f"Invoking LLM for {tier}...")
    
    # Base requirements
    tier_requirements = ""
    if tier == "TIER-S":
        tier_requirements = "这是极核心/安全底层的代码，请【极其严苛】地审查并发状态、死锁、内存泄漏、防重放、越权和 SQL 注入等致命问题！不放过任何蛛丝马迹。"
    elif tier == "TIER-A":
        tier_requirements = "这是核心业务逻辑，请侧重检查异常边界条件、空指针、重试逻辑和幂等性是否有缺失。"
    else:
        tier_requirements = "请重点查验基础规范、Type Hints、命名和是否有明显错误即可。"
        
    user_focus_str = ""
    if review_context or review_focus:
        user_focus_str = f"【开发者说明】:\n背景: {review_context}\n焦点: {review_focus}\n\n请【务必】针对开发者的焦点(Focus)进行深度评估校验！\n\n"

    llm = init_llm()
    try:
        prompt = (
            f"请使用**中文**审查以下代码变更。你是一位极其干练的资深工程师，你的 Review 必须符合以下要求：\n"
            f"1. 极度精简，只指出问题。\n"
            f"2. {tier_requirements}\n"
            f"3. 如果没有问题发空数组 []。\n"
            f"4. 你的输出【必须】是严谨的 JSON 数组结构，不能包含多余的 Markdown 格式，例如：\n"
            f'   [{{\"file\": \"path/to/file.py\", \"line\": 15, \"comment\": \"你的具体批注\"}}]\n\n'
            f"5. 务必确保 JSON 格式合法（用双引号包裹键名）。\n\n"
            f"{context_str}"
            f"{user_focus_str}"
            f"【完整文件上下文 (仅供参考)】:\n{state.get('full_files_context', '')}\n\n"
            f"【代码 Diff 变更】:\n{state['diff_text']}\n\n"
            f"精简 JSON 审查意见:"
        )
        response = llm.invoke([HumanMessage(content=prompt)])
        raw_text = response.content.strip()
        
        # Simple extraction logic for markdown wrapped json
        import re
        json_match = re.search(r'\[\s*\{.*?\}\s*\]', raw_text, re.DOTALL)
        if json_match:
            raw_text = json_match.group(0)
        elif raw_text.startswith("```json"):
            raw_text = raw_text[7:].strip("`\n ")
            
        logger.info(f"Code Review completed by LLM.")
        return {"review_result": raw_text}
    except Exception as e:
        logger.error(f"LLM API Error during code review: {e}")
        return {"review_result": "[]"}


def build_review_graph() -> StateGraph:
    workflow = StateGraph(AgentState)
    workflow.add_node("review_code", review_code_step)
    workflow.set_entry_point("review_code")
    workflow.add_edge("review_code", END)
    return workflow.compile()

graph = build_review_graph()

def global_impact_step(state: AgentState):
    """LangGraph node: Assesses cross-file backward compatibility."""
    logger.info("Executing global_impact_step...")
    llm = init_llm()
    try:
        prompt = (
            f"你是一位全局架构师。请仅评估以下文件的修改是否会导致**全局接口破坏**或**向后不兼容**。\n"
            f"不需要指出具体行数代码错误，只需给出一个宏观警告。\n"
            f"如果影响不大，返回严格的空字符串。\n"
            f"如果有影响，请输出一段纯文本警告内容。\n\n"
            f"【代码 Diff 变更】:\n{state['diff_text']}"
        )
        response = llm.invoke([HumanMessage(content=prompt)])
        return {"review_result": response.content.strip()}
    except Exception as e:
        logger.error(f"Global impact error: {e}")
        return {"review_result": ""}

global_graph = StateGraph(AgentState)
global_graph.add_node("global_impact", global_impact_step)
global_graph.set_entry_point("global_impact")
global_graph.add_edge("global_impact", END)
global_impact_graph = global_graph.compile()

def trigger_review_pipeline(pr_files_data: list[dict], tier: str = "Tier-B", review_context: str = "", review_focus: str = "") -> dict:
    """
    Executes Local Review Agents natively concurrently per-file.
    Optionally executes Global Impact Agent if tier mandates it.
    Returns: {"comments": list[dict], "global_warning": str}
    """
    logger.info(f"Triggering Multi-Agent Map-Reduce pipeline for {len(pr_files_data)} files...")
    if not pr_files_data:
        return {"comments": [], "global_warning": ""}
        
    initial_states = []
    combined_diffs = ""
    for file_data in pr_files_data:
        combined_diffs += f"\nFile: {file_data['filename']}\n{file_data['patch']}\n"
        state = {
            "diff_text": file_data["patch"], 
            "full_files_context": file_data["full_content"],
            "tier": tier,
            "review_context": review_context,
            "review_focus": review_focus,
            "review_result": "", 
            "chat_query": "", 
            "chat_response": ""
        }
        initial_states.append(state)
        
    # 1. Parallel execution for Local File Reviewers
    results = graph.batch(initial_states)
    
    all_reviews = []
    import json
    for result in results:
        res_str = result.get("review_result", "[]")
        if res_str:
            try:
                parsed = json.loads(res_str)
                if isinstance(parsed, list):
                    all_reviews.extend(parsed)
            except json.JSONDecodeError:
                pass

    # 2. Sequential/Parallel Global Impact Analyzer (if tier allows)
    global_warning = ""
    if tier in ["TIER-S", "TIER-A"]:
        gl_state = {
            "diff_text": combined_diffs,
            "full_files_context": "",
            "tier": tier,
            "review_context": review_context,
            "review_focus": review_focus,
            "review_result": "", 
            "chat_query": "", 
            "chat_response": ""
        }
        res = global_impact_graph.invoke(gl_state)
        global_warning = res.get("review_result", "")
        
    return {"comments": all_reviews, "global_warning": global_warning}

def chat_step(state: AgentState):
    """LangGraph node: Answers developer questions about the code/review."""
    logger.info("Executing chat_step...")
    llm = init_llm()
    try:
        prompt = (
            f"你是一个资深 AI Code Reviewer，正在与开发者就 PR 进行对话。\n"
            f"【PR Diff 背景】:\n{state.get('diff_text', '暂无代码')}\n\n"
            f"【开发者的问题】:\n{state.get('chat_query')}\n\n"
            f"请简洁专业地回答（请精简，直接切入正题，尽量提供代码示例）。"
        )
        response = llm.invoke([HumanMessage(content=prompt)])
        return {"chat_response": response.content}
    except Exception as e:
        logger.error(f"LLM API Error during chat: {e}")
        return {"chat_response": f"LLM Connection Error: {str(e)}"}

def build_chat_graph() -> StateGraph:
    workflow = StateGraph(AgentState)
    workflow.add_node("chat_node", chat_step)
    workflow.set_entry_point("chat_node")
    workflow.add_edge("chat_node", END)
    return workflow.compile()

chat_graph = build_chat_graph()

def nosenese():
    return "nosenese"

# def trigger_chat_pipeline(diff_text: str, chat_query: str) -> str:
#     logger.info(f"Triggering Agentic Chat pipeline...")
#     initial_state = {
#         "diff_text": diff_text,
#         "chat_query": chat_query,
#         "review_result": "",
#         "chat_response": ""
#     }
#     result = chat_graph.invoke(initial_state)
#     return result.get("chat_response", "Sorry, I couldn't process that.")
