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

# Load pluggable skills
from app.skills.type_hints_check import type_hints_check
from app.skills.print_statement_check import print_statement_check
from app.skills.hardcoded_secrets_check import hardcoded_secrets_check
from app.skills.diff_utils import annotate_diff_with_line_numbers
from app.skills.idempotency_check import idempotency_check
from app.skills.api_resilience_check import api_resilience_check

AVAILABLE_SKILLS = [
    type_hints_check, 
    print_statement_check, 
    hardcoded_secrets_check,
    idempotency_check,
    api_resilience_check
]

class AgentState(TypedDict):
    diff_text: str
    filename: str
    language: str
    full_files_context: str
    tier: str
    review_context: str
    review_focus: str
    review_result: str
    chat_query: str
    chat_response: str
    repo_path: str
    pr_filenames: list[str]

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


from app.core.llm import init_llm
from app.skills.ast_tools import find_python_references, read_code_snippet
def ingest_knowledge(
    content: str,
    metadata: dict = None,
    category: str = "general",
    path_regex: str = ".*",
    language: str = "python",
    severity: str = "warning"
) -> int:
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
    
    import uuid, datetime
    base_metadata = {
        "type": "guideline",
        "category": category,
        "path_regex": path_regex,
        "language": language,
        "severity": severity,
        "source": "manual_ingest",
        "created_at": datetime.datetime.utcnow().isoformat(),
        **(metadata or {}),
    }
    
    payloads = [{"content": chunk, **base_metadata} for chunk in chunks]
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


def _rewrite_diff_to_intent(diff_text: str) -> str:
    """
    【Query Rewriting / HyDE 变体】
    将代码 diff 翻译为自然语言的"风险意图描述"，使查询向量和文档向量处于同一语义空间。
    """
    try:
        snippet = diff_text[:800]
        llm = init_llm()
        prompt = (
            "你是一名代码安全架构师。请用 1-2 句简洁的中文描述下面这段代码变更的"
            "核心意图和潜在工程风险点。\n"
            "要求：\n"
            "1. 不要输出任何代码片段或代码符号\n"
            "2. 重点描述：这段变更在做什么，以及可能违反哪类编程规范\n"
            "3. 如果变更是删除代码，请说明删除了什么功能\n\n"
            f"【代码 Diff】:\n{snippet}"
        )
        response = llm.invoke([HumanMessage(content=prompt)])
        intent = response.content.strip()
        logger.info(f"Query rewritten: '{intent[:80]}...'")
        return intent
    except Exception as e:
        logger.warning(f"Query rewriting failed, falling back to raw diff: {e}")
        return diff_text

def retrieve_guidelines(query_text: str, filename: str = "", language: str = "python") -> tuple[str, list[str]]:
    """
    Search Qdrant for relevant coding standards and staging patches.
    Returns: (formatted guidelines string, list of disabled skill names)
    """
    try:
        human_readable_query = _rewrite_diff_to_intent(query_text)
        query_vector = embeddings_model.embed_query(human_readable_query)
        
        from qdrant_client.models import Filter, FieldCondition, MatchAny
        lang_filter = Filter(
            should=[
                FieldCondition(key="language", match=MatchAny(any=[language, "all"])),
            ]
        ) if language else None

        search_result = qdrant_client.query_points(
            collection_name=COLLECTION_NAME,
            query=query_vector,
            query_filter=lang_filter,
            limit=5
        ).points
        
        guidelines = []
        disabled_skills = []
        import re
        
        for hit in search_result:
            content = hit.payload.get("content", "")
            metadata = hit.payload.get("metadata", {})
            
            # Metadata-driven Action Routine
            if metadata.get("type") == "staging_patch":
                path_regex = metadata.get("path_regex", ".*")
                if filename and not re.search(path_regex, filename):
                    # Hard filtering: if the staging patch scope doesn't match the current file, skip it completely
                    logger.info(f"Filtered out staging patch due to path mismatch: {path_regex} for {filename}")
                    continue
                
                # If matched, apply the patch text
                guidelines.append(f"【自愈纠偏补丁】: {content}")
                
                # Suppress the skill via Route Action
                action = metadata.get("rule_action", {}).get("action")
                skill_name = metadata.get("rule_action", {}).get("skill_name")
                if action == "DISABLE_SKILL" and skill_name:
                    disabled_skills.append(skill_name)
                    logger.info(f"Metadata routing payload triggered: Disabling skill [{skill_name}] for {filename}")
            else:
                # Ordinary enterprise guidelines
                guidelines.append(content)
                
        return "\n\n".join(guidelines), disabled_skills
    except Exception as e:
        logger.error(f"Vector search failed: {e}")
        return "", []


def review_code_step(state: AgentState):
    """LangGraph node: Reviews code taking constraints/RAG context into account."""
    logger.info("Executing review_code_step. Retrieving knowledge...")
    
    # 1. Retrieve Knowledge
    filename = state.get("filename", "")
    language = state.get("language", "python")
    retrieved_context, disabled_skills = retrieve_guidelines(state['diff_text'], filename, language)
    
    context_str = ""
    if retrieved_context.strip():
        context_str = f"【企业代码规范参考】:\n{retrieved_context}\n\n请务必检查上述代码是否可能违反了上述规范要求。\n\n"
    
    tier = state.get("tier", "Tier-B")
    review_context = state.get("review_context", "")
    review_focus = state.get("review_focus", "")
    diff_text = state['diff_text']
    
    if tier == "TIER-C":
        logger.info("Tier-C detected. Skipping LLM code review (auto LGTM).")
        return {"review_result": "[]"}
        
    logger.info(f"Invoking LLM for {tier}...")
    
    # --- Run applicable Skills pre-LLM ---
    skill_findings_str = ""
    skill_results = []

    # print_statement_check: Tier-B and above (all tiers)
    if "print_statement_check" not in disabled_skills:
        try:
            result = print_statement_check.invoke({"code_diff": diff_text})
            if result:
                logger.info("[Skill] print_statement_check found issues.")
                skill_results.append(result)
        except Exception as e:
            logger.warning(f"Skill print_statement_check failed: {e}")

    # hardcoded_secrets_check: all tiers
    if "hardcoded_secrets_check" not in disabled_skills:
        try:
            result = hardcoded_secrets_check.invoke({"code_diff": diff_text})
            if result:
                logger.info("[Skill] hardcoded_secrets_check found issues.")
                skill_results.append(result)
        except Exception as e:
            logger.warning(f"Skill hardcoded_secrets_check failed: {e}")
    
    # type_hints_check: Tier-B only
    if tier in ["Tier-B", "TIER-B", ""] and "type_hints_check" not in disabled_skills:
        try:
            result = type_hints_check.invoke({"code_diff": diff_text})
            if result:
                logger.info("[Skill] type_hints_check found issues.")
                skill_results.append(result)
        except Exception as e:
            logger.warning(f"Skill type_hints_check failed: {e}")

    if skill_results:
        skill_findings_str = f"【静态 Skill 检查结果】:\n" + "\n".join(skill_results) + "\n\n"
        
    # --- Run LLM-backed Semantic Skills (Tier-A/S only) ---
    semantic_skill_results = []
    if tier in ["Tier-A", "TIER-A", "Tier-S", "TIER-S"]:
        # idempotency_check
        if "idempotency_check" not in disabled_skills:
            try:
                result = idempotency_check.invoke({
                    "code_diff": diff_text, 
                    "full_content": state.get('full_files_context', '')
                })
                if result:
                    logger.info("[Skill] idempotency_check found issues.")
                    semantic_skill_results.append(result)
            except Exception as e:
                logger.warning(f"Skill idempotency_check failed: {e}")
            
        # api_resilience_check
        if "api_resilience_check" not in disabled_skills:
            try:
                result = api_resilience_check.invoke({
                    "code_diff": diff_text, 
                    "full_content": state.get('full_files_context', '')
                })
                if result:
                    logger.info("[Skill] api_resilience_check found issues.")
                    semantic_skill_results.append(result)
            except Exception as e:
                logger.warning(f"Skill api_resilience_check failed: {e}")
            
    if semantic_skill_results:
        skill_findings_str += f"【Agentic Skill 深度分析报告】:\n" + "\n".join(semantic_skill_results) + "\n\n"
    
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
            f"5. 务必确保 JSON 格式合法（用双引号包裹键名）。\n"
            f"6. 【关键】Diff 中每行以 L+数字 开头（如 L15），这是新文件中的真实行号。你输出的 line 字段必须使用该数字，不要自己推算行号！\n\n"
            f"{context_str}"
            f"{skill_findings_str}"
            f"{user_focus_str}"
            f"【正在审查的文件】: {state.get('filename', '未知文件')}\n\n"
            f"【完整文件上下文 (仅供参考)】:\n{state.get('full_files_context', '')}\n\n"
            f"【代码 Diff 变更 (L开头的数字是真实行号，直接用于 line 字段)】:\n{annotate_diff_with_line_numbers(state['diff_text'])}\n\n"
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
    """LangGraph node: Assesses cross-file backward compatibility using AST Tool Calling."""
    logger.info("Executing global_impact_step with AST Tool Calling...")
    
    if not state.get("repo_path"):
        logger.info("No repo_path provided. Skipping AST Global Impact check.")
        return {"review_result": ""}
        
    llm = init_llm()
    # Bind AST tools for the LLM
    from langchain_core.messages import HumanMessage, ToolMessage
    llm_with_tools = llm.bind_tools([find_python_references, read_code_snippet])
    
    pr_filenames_str = ", ".join(state.get('pr_filenames', []))
    prompt = f"""你是一位具备全栈代码库视野的全局架构师。
你的任务是评估本次 PR 修改是否引发了跨文件的**破坏性变更（Breaking Changes）**。
破坏性变更特指：修改了核心函数的参数签名、移除了函数、或者变更了返回值类型，这会导致其他未被修改的文件在调用时抛出异常。

【核心审查执行逻辑 (必须遵守)】：
1. 分析下方代码 Diff。如果**不涉及破坏性更改**（只是改了内部逻辑、新增文件等），请立刻停止，并严格输出空字符串。
2. 如果存在破坏性更改，请明确提取被修改的核心函数名称。
3. 主动调用工具 `find_python_references`，传入仓库路径和函数名，查找所有调用方。
4. 【重要】：如果检索到的调用方文件已经存在于本次 PR 包含的文件列表中（[{pr_filenames_str}]），说明开发者已经同步修改了调用方代码。此时无需报错！
5. 如果调用方文件【不在】上述 PR 文件列表中，请使用 `read_code_snippet` 读取调用上下文，确认是否真的会引发崩溃。
6. 如果确认引发崩溃，请输出一段严厉的警告，明确指出未修改的文件及其行号。

【仓库环境参数】
- repo_path: {state.get('repo_path')}

【代码 Diff 变更】:
{state['diff_text']}
"""

    messages = [HumanMessage(content=prompt)]
    tool_trace = []  # Collect AST execution steps for display in PR comment
    
    try:
        for _ in range(5): # Limit to 5 LLM interactions
            response = llm_with_tools.invoke(messages)
            messages.append(response)
            
            if not response.tool_calls:
                break # LLM decided to reply normally
                
            for tool_call in response.tool_calls:
                tool_name = tool_call["name"]
                tool_args = tool_call["args"]
                
                try:
                    if tool_name == "find_python_references":
                        # Ensure repo_path is explicitly set to prevent LLM hallucinating paths
                        tool_args["repo_path"] = state.get("repo_path")
                        tool_result = find_python_references.invoke(tool_args)
                        tool_trace.append(
                            f"🔍 **AST 扫描** `find_python_references(function_name='{tool_args.get('function_name', '')}')`\n"
                            f"```\n{str(tool_result)}\n```"
                        )
                    elif tool_name == "read_code_snippet":
                        tool_args["repo_path"] = state.get("repo_path")
                        tool_result = read_code_snippet.invoke(tool_args)
                        tool_trace.append(
                            f"📄 **读取代码** `read_code_snippet(file='{tool_args.get('relative_file_path', '')}', "
                            f"lines={tool_args.get('start_line', '?')}-{tool_args.get('end_line', '?')})`\n"
                            f"```python\n{str(tool_result)}\n```"
                        )
                    else:
                        tool_result = f"Error: Tool {tool_name} not found."
                        tool_trace.append(f"❓ 未知工具: `{tool_name}`")
                except Exception as e:
                    tool_result = f"Tool execution error: {e}"
                    tool_trace.append(f"❌ 工具执行失败: `{tool_name}` → {e}")
                    
                logger.info(f"[AST Agent] Executed {tool_name} -> {str(tool_result)[:100]}...")
                messages.append(ToolMessage(content=str(tool_result), tool_call_id=tool_call["id"]))

        final_content = response.content.strip()
        # Clean up empty thoughts if LLM outputs only spaces
        if not final_content:
            return {"review_result": ""}
        
        # Append the AST execution trace as a collapsible section
        if tool_trace:
            trace_section = (
                "\n\n---\n"
                "<details>\n"
                "<summary>🤖 AST Agent 推理过程（点击展开）</summary>\n\n"
                + "\n\n".join(tool_trace) +
                "\n\n</details>"
            )
            final_content = final_content + trace_section
            
        return {"review_result": final_content}
        
    except Exception as e:
        logger.error(f"Global impact tool execution error: {e}")
        return {"review_result": ""}

global_graph = StateGraph(AgentState)
global_graph.add_node("global_impact", global_impact_step)
global_graph.set_entry_point("global_impact")
global_graph.add_edge("global_impact", END)
global_impact_graph = global_graph.compile()

def trigger_review_pipeline(pr_files_data: list[dict], tier: str = "Tier-B", review_context: str = "", review_focus: str = "", repo_path: str = "") -> dict:
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
    pr_filenames = [fd["filename"] for fd in pr_files_data]
    
    for file_data in pr_files_data:
        combined_diffs += f"\nFile: {file_data['filename']}\n{file_data['patch']}\n"
        state = {
            # Pass raw patch WITHOUT "File:" prefix so Skills compute correct line numbers
            "diff_text": file_data["patch"],
            "filename": file_data["filename"],
            "language": file_data.get("language", "python"),
            "full_files_context": file_data["full_content"],
            "tier": tier,
            "review_context": review_context,
            "review_focus": review_focus,
            "review_result": "", 
            "chat_query": "", 
            "chat_response": "",
            "repo_path": repo_path,
            "pr_filenames": pr_filenames
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
    if tier in ["TIER-S", "TIER-A", "Tier-S", "Tier-A"]:
        gl_state = {
            "diff_text": combined_diffs,
            "filename": "",
            "language": "all",
            "full_files_context": "",
            "tier": tier,
            "review_context": review_context,
            "review_focus": review_focus,
            "review_result": "", 
            "chat_query": "", 
            "chat_response": "",
            "repo_path": repo_path,
            "pr_filenames": pr_filenames
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

def trigger_chat_pipeline(diff_text: str, chat_query: str) -> str:
    state = {
        "diff_text": diff_text,
        "filename": "",
        "full_files_context": "",
        "tier": "",
        "review_context": "",
        "review_focus": "",
        "review_result": "",
        "chat_query": chat_query,
        "chat_response": ""
    }
    result = chat_graph.invoke(state)
    return result.get("chat_response", "无法生成回答。")

def trigger_refiner_pipeline(diff_text: str, user_comment: str) -> str:
    """
    【Refiner Agent】从用户反馈中提炼知识并注入 Qdrant。

    修复后的两阶段流程：
    Phase 1 - 意图分类（Intent Classification）：
        先判断用户评论是"误报驳回（False Positive Rejection）"还是"普通对话/追问"。
        只有明确的误报驳回才进入规范提炼阶段，避免普通对话污染知识库。

    Phase 2 - 条件提炼（Conditional Extraction）：
        仅当 is_false_positive=true 时，提炼 insight_text 和 rule_action 并存入 Qdrant。
        否则直接返回普通对话回复，不写入任何知识。
    """
    logger.info("Triggering Refiner Agent...")
    llm = init_llm()
    prompt = (
        f"你是一名架构规范总结师（Refiner Agent）。\n"
        f"请先判断下面这条开发者评论的意图类型：\n\n"
        f"【意图类型说明】：\n"
        f"  A. 误报驳回（False Positive）：开发者明确否定了 AI 的审查意见，"
        f"并解释为什么这段代码在当前上下文中是合理的。\n"
        f"  B. 普通对话/追问（General Chat）：开发者在提问、讨论、或要求调整建议，"
        f"并没有否定 AI 的审查意见本身。\n\n"
        f"【输出要求】：纯 JSON，不含 Markdown。必须包含：\n"
        f"  1. is_false_positive (bool): true 表示误报驳回，false 表示普通对话\n"
        f"  2. insight_text (str): 仅 is_false_positive=true 时填写业务认知更新，"
        f"否则为空字符串。严禁写出粗暴的屏蔽规则！\n"
        f"  3. rule_action (dict): 仅 is_false_positive=true 时填写，包含:\n"
        f"     action, skill_name, path_regex, category, language, severity\n"
        f"     否则为空 dict {{}}\n\n"
        f"【原始 Diff】:\n{diff_text}\n\n"
        f"【开发者评论】:\n{user_comment}\n\n"
        f"请输出 JSON:"
    )
    
    try:
        response = llm.invoke([HumanMessage(content=prompt)])
        raw_text = response.content.strip()
        import re, json, datetime
        json_match = re.search(r'\{.*\}', raw_text, re.DOTALL)
        if json_match:
            try:
                parsed_json = json.loads(json_match.group(0))

                # Phase 1: 意图分类结果 — 不是误报驳回，直接返回普通回复
                if not parsed_json.get("is_false_positive", False):
                    logger.info("Refiner classified as general chat. Skipping knowledge injection.")
                    return (
                        f"感谢您的反馈！根据您的评论，这不属于需要更新规范的误报场景。\n"
                        f"如果您想进一步讨论代码细节，欢迎继续提问。"
                    )

                # Phase 2: 确认是误报驳回，提炼并注入 Qdrant
                content_to_save = parsed_json.get("insight_text", "")
                if not content_to_save:
                    return "❌ 无法提炼有效的规范认知，请尝试更详细地描述误报原因。"

                metadata = {
                    "source": "Self-Reflection Loop",
                    "type": "staging_patch",
                    "path_regex": parsed_json.get("rule_action", {}).get("path_regex", ".*"),
                    "category": parsed_json.get("rule_action", {}).get("category", "general"),
                    "language": parsed_json.get("rule_action", {}).get("language", "python"),
                    "severity": parsed_json.get("rule_action", {}).get("severity", "warning"),
                    "rule_action": parsed_json.get("rule_action", {}),
                    "created_at": datetime.datetime.utcnow().isoformat(),
                }
                
                # 直接调用同文件函数，无需循环 import
                ingest_knowledge(content_to_save, metadata=metadata)
                
                rule_name = parsed_json.get("rule_action", {}).get("skill_name", "UNKNOWN")
                return (
                    f"✅ **收到误报反馈，自愈机制已启动！**\n\n"
                    f"已将您的上下文提炼为架构认知补丁并写入知识库。\n"
                    f"下次命中相同路径的 PR 时，`{rule_name}` 的检查将被软化或拦截。\n\n"
                    f"> 提炼的认知: *{content_to_save}*"
                )
            except Exception as e:
                logger.error(f"Failed to parse Refiner json: {e}")
                return "❌ 无法解析反馈认知，自愈闭环失败。"
        return "❌ 无法生成规范资产。"
    except Exception as e:
        logger.error(f"LLM API Error during Refiner: {e}")
        return f"❌ 内部反思特工执行错误: {str(e)}"
