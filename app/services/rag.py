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

from pydantic import BaseModel, Field
from typing import Literal, Optional


class PlannedFile(BaseModel):
    path: str
    language: str
    risk_reasons: list[str]
    selected_checks: list[str]


class PlannerOutput(BaseModel):
    user_requested_tier: Optional[str]
    system_inferred_tier: str
    final_tier: str
    tier_resolution_reason: str
    selected_checks: list[str]
    files: list[PlannedFile]


class RetrievedGuideline(BaseModel):
    content: str
    category: str = "general"
    language: str = "all"
    severity: str = "warning"
    source: str = "qdrant"


class ReviewFinding(BaseModel):
    file: str
    line: int
    comment: str
    check: str
    evidence: Optional[str] = None
    severity: Literal["info", "warning", "error"]


class CriticDecision(BaseModel):
    kept: list[ReviewFinding]
    dropped: list[dict]  # dict containing finding details and a `reason` for dropping


class AgentTraceEvent(BaseModel):
    node: str
    summary: str
    data: dict = {}


class AgentState(TypedDict):
    # Inputs
    diff_text: str
    filename: str
    language: str
    full_files_context: str
    pr_filenames: list[str]
    user_requested_tier: Optional[str]
    repo_path: str

    # Legacy Inputs for backward compatibility with webhook currently
    tier: str
    review_context: str
    review_focus: str

    # MAS State
    plan: dict  # PlannerOutput dump
    retrieved_guidelines: list[dict]  # list of RetrievedGuideline dumps
    disabled_skills: list[str]
    raw_reviews: list[dict]  # list of ReviewFinding dumps
    final_reviews: list[dict]  # kept ReviewFinding dumps
    dropped_reviews: list[dict]
    final_comments: list[dict]  # Legacy format for GitHub webhook
    agent_trace: list[dict]  # list of AgentTraceEvent dumps
    trace_markdown: str

    # Chat State
    chat_query: str
    chat_response: str
    review_result: str  # legacy


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
        severity: str = "warning",
        rule_id: str = None
) -> list[str]:
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
    ids = [rule_id] if rule_id and len(chunks) == 1 else [str(uuid.uuid4()) for _ in chunks]

    qdrant_client.upsert(
        collection_name=COLLECTION_NAME,
        points=[
            {"id": id_, "vector": vector, "payload": payload}
            for id_, vector, payload in zip(ids, vectors, payloads)
        ],
    )

    logger.info(f"Ingested {len(chunks)} chunks successfully.")
    return ids


def approve_knowledge_rule(rule_id: str) -> bool:
    """Approve a staging rule by moving it to production status."""
    try:
        from qdrant_client.models import SetPayloadOperation
        qdrant_client.set_payload(
            collection_name=COLLECTION_NAME,
            payload={"status": "production"},
            points=[rule_id]
        )
        logger.info(f"Rule {rule_id} approved and moved to production.")
        return True
    except Exception as e:
        logger.error(f"Failed to approve rule {rule_id}: {e}")
        return False


def delete_knowledge_rule(rule_id: str) -> bool:
    """Delete a knowledge rule by ID."""
    try:
        qdrant_client.delete(
            collection_name=COLLECTION_NAME,
            points_selector=[rule_id]
        )
        logger.info(f"Rule {rule_id} deleted successfully.")
        return True
    except Exception as e:
        logger.error(f"Failed to delete rule {rule_id}: {e}")
        return False


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
                status = metadata.get("status", "production")

                # If matched, apply the patch text
                if status == "staging":
                    guidelines.append(f"【待审批规则 - 仅供参考】: {content}")
                else:
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


TIER_RANK = {
    "Tier-C": 0,
    "Tier-B": 1,
    "Tier-A": 2,
    "Tier-S": 3,
}


def higher_tier(tier1: Optional[str], tier2: str) -> str:
    if not tier1:
        return tier2

    # Normalize
    def normalize(t):
        parts = t.strip().upper().split('-')
        if len(parts) == 2 and parts[0] in ['TIER', 'TIER']:
            return f"Tier-{parts[-1]}"
        return t
        
    t1 = normalize(tier1)
    t2 = normalize(tier2)

    rank1 = TIER_RANK.get(t1, 1)  # default B
    rank2 = TIER_RANK.get(t2, 1)

    return t1 if rank1 >= rank2 else t2


def planner_step(state: AgentState):
    """LangGraph node: Determines review strategy and tier resolution."""
    logger.info("Executing planner_step...")
    diff_text = state.get('diff_text', '')
    user_tier = state.get('user_requested_tier') or state.get('tier')
    pr_filenames = state.get('pr_filenames', [])

    llm = init_llm().bind(response_format={'type': 'json_object'})

    prompt = f"""You are a Planner Agent for a Code Review System.
Analyze the following PR metadata and diff to determine the review strategy.

【User Requested Tier】: {user_tier if user_tier else 'None'}
【PR Files】: {pr_filenames}

【Diff Summary】:
{diff_text[:3000]}

1. Evaluate the `system_inferred_tier` (Tier-C: simple styling, Tier-B: normal logic, Tier-A: core logic, Tier-S: payment/auth/resilience/high risk).
2. Calculate `final_tier` which must be the higher of user_requested_tier and system_inferred_tier.
3. Provide `tier_resolution_reason`.
4. Decide `selected_checks` (e.g. 'security', 'type_hints', 'idempotency', 'global_impact', 'style').
5. Provide a plan for this specific file in the `files` list.

You MUST return ONLY a valid JSON object matching the following structure:
""" + """{
  "user_requested_tier": "string | null",
  "system_inferred_tier": "string",
  "final_tier": "string",
  "tier_resolution_reason": "string",
  "selected_checks": ["string"],
  "files": [{"path": "string", "language": "string", "risk_reasons": ["string"], "selected_checks": ["string"]}]
}
"""
    try:
        response = llm.invoke([HumanMessage(content=prompt)])
        import json
        raw_text = response.content.strip()
        if raw_text.startswith("```json"): raw_text = raw_text[7:-3].strip()
        elif raw_text.startswith("```"): raw_text = raw_text[3:-3].strip()
        plan_dict = json.loads(raw_text)
        plan = PlannerOutput(**plan_dict)
        plan_dict = plan.dict()

        trace_event = {
            "node": "planner_step",
            "summary": f"已将代码风险评级定为 {plan.final_tier} (用户请求: {user_tier}, 系统推断: {plan.system_inferred_tier})",
            "data": plan_dict
        }

        return {
            "plan": plan_dict,
            "agent_trace": state.get("agent_trace", []) + [trace_event]
        }
    except Exception as e:
        logger.error(f"Planner failed: {e}")
        # Fallback plan
        fallback = {
            "user_requested_tier": user_tier,
            "system_inferred_tier": "Tier-B",
            "final_tier": user_tier or "Tier-B",
            "tier_resolution_reason": "Fallback due to planner error",
            "selected_checks": ["general"],
            "files": []
        }
        return {"plan": fallback, "agent_trace": state.get("agent_trace", []) + [
            {"node": "planner_step", "summary": "Planner failed, used fallback", "data": {}}]}


def retrieve_step(state: AgentState):
    """LangGraph node: Retrieves guidelines and disables skills based on metadata."""
    logger.info("Executing retrieve_step...")
    # 1. Retrieve Knowledge
    filename = state.get("filename", "")
    language = state.get("language", "python")
    diff_text = state.get("diff_text", "")

    # We use the existing retrieve_guidelines but adapt it to return structured data if needed.
    # For now, we wrap it to match the new state requirements.
    retrieved_context_str, disabled_skills = retrieve_guidelines(diff_text, filename, language)

    # Mocking structure since existing retrieve_guidelines returns a string
    guidelines = []
    if retrieved_context_str.strip():
        guidelines.append({
            "content": retrieved_context_str,
            "category": "general",
            "language": language,
            "severity": "warning",
            "source": "qdrant"
        })

    trace_event = {
        "node": "retrieve_step",
        "summary": f"检索到企业规范。已禁用技能: {disabled_skills}",
        "data": {"disabled_skills": disabled_skills}
    }

    return {
        "retrieved_guidelines": guidelines,
        "disabled_skills": disabled_skills,
        "agent_trace": state.get("agent_trace", []) + [trace_event]
    }


class ReviewerOutput(BaseModel):
    findings: list[ReviewFinding]


def reviewer_step(state: AgentState):
    """LangGraph node: Generates candidate review findings."""
    logger.info("Executing reviewer_step...")
    plan = state.get("plan", {})
    tier = plan.get("final_tier", "Tier-B")
    disabled_skills = state.get("disabled_skills", [])
    diff_text = state.get('diff_text', '')

    if tier.upper() == "TIER-C":
        logger.info("Tier-C detected. Skipping LLM code review.")
        return {
            "raw_reviews": [],
            "agent_trace": state.get("agent_trace", []) + [
                {"node": "reviewer_step", "summary": "Skipped due to Tier-C", "data": {}}]
        }

    # Same skill logic as before, run print_statement_check, hardcoded_secrets_check, etc.
    # ... I will copy the skill execution logic ...

    llm = init_llm().bind(response_format={'type': 'json_object'})

    tier_requirements = ""
    if tier.upper() == "TIER-S":
        tier_requirements = "这是极核心/安全底层的代码，请【极其严苛】地审查并发状态、死锁、内存泄漏、防重放、越权和 SQL 注入等致命问题！"
    elif tier.upper() == "TIER-A":
        tier_requirements = "这是核心业务逻辑，请侧重检查异常边界条件、空指针、重试逻辑和幂等性是否有缺失。"
    else:
        tier_requirements = "请重点查验基础规范、Type Hints、命名和是否有明显错误即可。"

    context_str = ""
    for g in state.get("retrieved_guidelines", []):
        context_str += f"- {g['content']}\n"
    if context_str:
        context_str = f"【企业代码规范参考】:\n{context_str}\n\n请务必检查是否违反规范。\n"

    prompt = (
        f"你是一位资深工程师。请使用中文审查代码变更。\n"
        f"当前审查的文件名是：{state.get('filename')}\n"
        f"要求：\n1. {tier_requirements}\n2. Diff 中每行以 L+数字 开头（如 L15），你必须使用该真实行号！\n3. 在 findings 的 file 字段中，必须严格填写 {state.get('filename')}！\n\n"
        f"{context_str}\n"
        f"【完整文件上下文】:\n{state.get('full_files_context', '')}\n\n"
        f"【代码 Diff 变更】:\n{annotate_diff_with_line_numbers(diff_text)}\n\n"
        "You MUST return ONLY a valid JSON object matching the following structure:\n"
        "{\n"
        "  \"findings\": [\n"
        "    {\n"
        "      \"file\": \"string\",\n"
        "      \"line\": 123,\n"
        "      \"comment\": \"string\",\n"
        "      \"check\": \"string\",\n"
        "      \"evidence\": \"string | null\",\n"
        "      \"severity\": \"info | warning | error\"\n"
        "    }\n"
        "  ]\n"
        "}"
    )

    try:
        response = llm.invoke([HumanMessage(content=prompt)])
        import json
        raw_text = response.content.strip()
        if raw_text.startswith("```json"): raw_text = raw_text[7:-3].strip()
        elif raw_text.startswith("```"): raw_text = raw_text[3:-3].strip()
        resp_dict = json.loads(raw_text)
        resp_obj = ReviewerOutput(**resp_dict)
        raw_reviews = [f.dict() for f in resp_obj.findings]

        trace_event = {
            "node": "reviewer_step",
            "summary": f"生成了 {len(raw_reviews)} 条初步审查意见",
            "data": {"count": len(raw_reviews)}
        }

        return {
            "raw_reviews": raw_reviews,
            "agent_trace": state.get("agent_trace", []) + [trace_event]
        }
    except Exception as e:
        logger.error(f"Reviewer LLM failed: {e}")
        return {"raw_reviews": []}


def critic_step(state: AgentState):
    """LangGraph node: Hybrid critic (Deterministic + LLM) to filter findings."""
    logger.info("Executing critic_step...")
    raw_reviews = state.get("raw_reviews", [])
    pr_filenames = state.get("pr_filenames", [])
    diff_text = state.get("diff_text", "")

    if not raw_reviews:
        return {"final_reviews": [], "dropped_reviews": []}

    # 1. Deterministic Checks
    # Simple line number check (rough diff parsing)
    import re
    valid_lines = set()
    for line in annotate_diff_with_line_numbers(diff_text).split('\n'):
        if line.startswith("L") and ":" in line:
            try:
                line_num = int(line.split(":")[0][1:])
                valid_lines.add(line_num)
            except:
                pass

    llm_candidates = []
    dropped = []

    for review in raw_reviews:
        file_path = review.get("file", "")
        line_num = review.get("line", 0)

        # Check 1: File existence
        if pr_filenames and not any(file_path.endswith(pr) for pr in pr_filenames):
            dropped.append({"finding": review, "reason": f"File {file_path} not in PR."})
            continue

        # Check 2: Line existence
        if valid_lines and line_num not in valid_lines:
            dropped.append({"finding": review, "reason": f"Line {line_num} is not a valid modified line in diff."})
            continue

        llm_candidates.append(review)

    # 2. LLM Verification
    final_reviews = []
    if llm_candidates:
        llm = init_llm().bind(response_format={'type': 'json_object'})
        prompt = f"""你是一个 Critic Agent (代码审查校验员)。请仔细验证以下生成的代码审查意见。
你需要根据以下规则丢弃（Drop）不合理的意见：
- 纯粹是主观的代码风格偏好，且没有提供有力的证据。
- 提供的证据与评论内容不符或不足以支撑该评论。
- 评论内容本身违背了最佳实践。

候选意见列表：
{llm_candidates}

请输出哪些意见应该保留（kept），哪些应该丢弃（dropped），并给出丢弃的原因。
You MUST return ONLY a valid JSON object matching the following structure:
""" + """{
  "kept": [
    {
      "file": "string",
      "line": 123,
      "comment": "string",
      "check": "string",
      "evidence": "string | null",
      "severity": "info | warning | error"
    }
  ],
  "dropped": [
    {
      "finding": {},
      "reason": "中文说明被丢弃的原因"
    }
  ]
}
"""
        try:
            response = llm.invoke([HumanMessage(content=prompt)])
            import json
            raw_text = response.content.strip()
            if raw_text.startswith("```json"): raw_text = raw_text[7:-3].strip()
            elif raw_text.startswith("```"): raw_text = raw_text[3:-3].strip()
            resp_dict = json.loads(raw_text)
            decision = CriticDecision(**resp_dict)
            final_reviews = [f.dict() for f in decision.kept]
            for d in decision.dropped:
                dropped.append(d)
        except Exception as e:
            logger.error(f"Critic LLM failed: {e}")
            # Fallback: keep them all if critic fails
            final_reviews = llm_candidates

    trace_event = {
        "node": "critic_step",
        "summary": f"Critic 校验完毕。保留了: {len(final_reviews)} 条，拦截了: {len(dropped)} 条",
        "data": {"dropped": [d.get("reason") for d in dropped]}
    }

    return {
        "final_reviews": final_reviews,
        "dropped_reviews": dropped,
        "agent_trace": state.get("agent_trace", []) + [trace_event]
    }


def finalize_review_step(state: AgentState):
    """LangGraph node: Formats trace and final comments."""
    logger.info("Executing finalize_review_step...")
    final_reviews = state.get("final_reviews", [])
    agent_trace = state.get("agent_trace", [])

    # 1. Format legacy comments
    final_comments = final_reviews  # already list of dicts: file, line, comment

    # 2. Format Trace Markdown
    trace_md = "<details>\n<summary>🤖 Agent Trace</summary>\n\n"
    for event in agent_trace:
        trace_md += f"- **{event.get('node')}**: {event.get('summary')}\n"
        if event.get("node") == "critic_step" and "dropped" in event.get("data", {}):
            for drop_reason in event["data"]["dropped"]:
                if drop_reason:
                    trace_md += f"  - 🚫 Dropped: {drop_reason}\n"
    trace_md += "\n</details>"

    return {
        "final_comments": final_comments,
        "trace_markdown": trace_md
    }


def build_review_graph() -> StateGraph:
    workflow = StateGraph(AgentState)
    workflow.add_node("planner", planner_step)
    workflow.add_node("retrieve", retrieve_step)
    workflow.add_node("reviewer", reviewer_step)
    workflow.add_node("critic", critic_step)
    workflow.add_node("finalize", finalize_review_step)

    workflow.set_entry_point("planner")
    workflow.add_edge("planner", "retrieve")
    workflow.add_edge("retrieve", "reviewer")
    workflow.add_edge("reviewer", "critic")
    workflow.add_edge("critic", "finalize")
    workflow.add_edge("finalize", END)
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
1. 分析下方代码 Diff。如果**不涉及破坏性更改**（只是改了内部逻辑、新增文件等），请立刻停止，并输出 "is_breaking": false。
2. 如果存在破坏性更改，请明确提取被修改的核心函数名称。
3. 主动调用工具 `find_python_references`，传入仓库路径和函数名，查找所有调用方。
4. 【重要】：如果检索到的调用方文件已经存在于本次 PR 包含的文件列表中（[{pr_filenames_str}]），说明开发者已经同步修改了调用方代码。此时无需报错！
5. 如果调用方文件【不在】上述 PR 文件列表中，请使用 `read_code_snippet` 读取调用上下文，确认是否真的会引发崩溃。
6. 如果确认引发崩溃，请在 warning_msg 中输出一段严厉的警告，明确指出未修改的文件及其行号。

【仓库环境参数】
- repo_path: {state.get('repo_path')}

【代码 Diff 变更】:
{state['diff_text']}

You MUST return ONLY a valid JSON object matching the following structure:
{{
  "is_breaking": true/false,
  "warning_msg": "中文警告信息，如果没有破坏性变更，则为空字符串"
}}
"""

    messages = [HumanMessage(content=prompt)]
    tool_trace = []  # Collect AST execution steps for display in PR comment

    try:
        for _ in range(5):  # Limit to 5 LLM interactions
            response = llm_with_tools.invoke(messages)
            messages.append(response)

            if not response.tool_calls:
                break  # LLM decided to reply normally

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

        # Log the full AST trace for debugging, but don't expose it in the PR comment
        if tool_trace:
            logger.info("[AST Agent] Full reasoning trace:\n" + "\n".join(tool_trace))

        return {"review_result": final_content}

    except Exception as e:
        logger.error(f"Global impact tool execution error: {e}")
        return {"review_result": ""}


global_graph = StateGraph(AgentState)
global_graph.add_node("global_impact", global_impact_step)
global_graph.set_entry_point("global_impact")
global_graph.add_edge("global_impact", END)
global_impact_graph = global_graph.compile()


def trigger_review_pipeline(pr_files_data: list[dict], tier: str = "Tier-B", review_context: str = "",
                            review_focus: str = "", repo_path: str = "") -> dict:
    """
    Executes Local Review Agents natively concurrently per-file.
    Optionally executes Global Impact Agent if tier mandates it.
    Returns: {"comments": list[dict], "global_warning": str, "agent_trace": list, "agent_trace_markdown": str, "plan": dict}
    """
    logger.info(f"Triggering Multi-Agent Map-Reduce pipeline for {len(pr_files_data)} files...")
    if not pr_files_data:
        return {"comments": [], "global_warning": "", "agent_trace": [], "agent_trace_markdown": "", "plan": {}}

    initial_states = []
    combined_diffs = ""
    pr_filenames = [fd["filename"] for fd in pr_files_data]

    for file_data in pr_files_data:
        combined_diffs += f"\nFile: {file_data['filename']}\n{file_data['patch']}\n"
        state = {
            "diff_text": file_data["patch"],
            "filename": file_data["filename"],
            "language": file_data.get("language", "python"),
            "full_files_context": file_data["full_content"],
            "tier": tier,
            "user_requested_tier": tier,
            "review_context": review_context,
            "review_focus": review_focus,
            "repo_path": repo_path,
            "pr_filenames": pr_filenames,
            "agent_trace": []
        }
        initial_states.append(state)

    # 1. Parallel execution for Local File Reviewers
    results = graph.batch(initial_states)

    all_reviews = []
    all_traces = []
    all_trace_mds = []
    final_plan = {}

    for result in results:
        final_comments = result.get("final_comments", [])
        if final_comments:
            all_reviews.extend(final_comments)

        agent_trace = result.get("agent_trace", [])
        if agent_trace:
            all_traces.extend(agent_trace)

        trace_md = result.get("trace_markdown", "")
        if trace_md:
            all_trace_mds.append(f"### File: {result.get('filename')}\n{trace_md}")

        # Just grab the plan from the first file as the general plan (since they run concurrently but use same metadata roughly)
        if not final_plan and result.get("plan"):
            final_plan = result.get("plan")

    # 2. Sequential/Parallel Global Impact Analyzer (if tier allows)
    global_warning = ""
    # We use final_plan's tier if available
    resolved_tier = final_plan.get("final_tier", tier)
    if resolved_tier in ["TIER-S", "TIER-A", "Tier-S", "Tier-A"]:
        gl_state = {
            "diff_text": combined_diffs,
            "filename": "",
            "language": "all",
            "full_files_context": "",
            "tier": resolved_tier,
            "user_requested_tier": tier,
            "repo_path": repo_path,
            "pr_filenames": pr_filenames,
            "plan": {},
            "retrieved_guidelines": [],
            "disabled_skills": [],
            "raw_reviews": [],
            "final_reviews": [],
            "dropped_reviews": [],
            "final_comments": [],
            "agent_trace": [],
            "trace_markdown": "",
            "chat_query": "",
            "chat_response": "",
            "review_result": "",
            "review_context": review_context,
            "review_focus": review_focus
        }
        res = global_impact_graph.invoke(gl_state)
        global_warning = res.get("review_result", "")

    combined_trace_md = "\n\n".join(all_trace_mds)

    return {
        "comments": all_reviews,
        "global_warning": global_warning,
        "agent_trace": all_traces,
        "agent_trace_markdown": combined_trace_md,
        "plan": final_plan
    }


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
        f"     否则为空 dict {{}}\n"
        f"  4. confidence (float): 0.0到1.0的综合置信度，评估提炼的规则是否准确、具体、合理且不与常规工程规范矛盾。\n"
        f"  5. risk (str): 如果接受此规则影响整个仓库的潜在风险，选填 'low', 'medium', 'high'\n"
        f"  6. reject_reason (str): 如果置信度低于 0.6 或风险为 high，说明拒绝原因。\n\n"
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

                confidence = parsed_json.get("confidence", 0.0)
                risk = parsed_json.get("risk", "high")

                if confidence < 0.6 or risk == "high":
                    reason = parsed_json.get("reject_reason", "规则置信度过低或风险过高，已被自评拦截。")
                    logger.info(f"Refiner rejected rule. Confidence: {confidence}, Risk: {risk}. Reason: {reason}")
                    return f"❌ **提炼的规则未通过 AI 自评拦截**\n\n拦截原因: {reason}\n置信度: {confidence}"

                status = "production"
                if confidence < 0.85 or risk == "medium":
                    status = "staging"

                metadata = {
                    "source": "Self-Reflection Loop",
                    "type": "staging_patch",
                    "status": status,
                    "path_regex": parsed_json.get("rule_action", {}).get("path_regex", ".*"),
                    "category": parsed_json.get("rule_action", {}).get("category", "general"),
                    "language": parsed_json.get("rule_action", {}).get("language", "python"),
                    "severity": parsed_json.get("rule_action", {}).get("severity", "warning"),
                    "rule_action": parsed_json.get("rule_action", {}),
                    "created_at": datetime.datetime.utcnow().isoformat(),
                }

                import uuid
                rule_id = str(uuid.uuid4())
                ingest_knowledge(content_to_save, metadata=metadata, rule_id=rule_id)

                rule_name = parsed_json.get("rule_action", {}).get("skill_name", "UNKNOWN")

                if status == "staging":
                    return (
                        f"📋 **该规则已进入审查暂存区（staging）**\n\n"
                        f"> 提炼的认知: *{content_to_save}*\n\n"
                        f"Rule ID: `{rule_id}` (置信度: {confidence}, 风险评估: {risk})\n\n"
                        f"暂存规则需管理员确认后生效。如需批准，请在此评论回复：`@bot approve-rule {rule_id}`\n"
                        f"如需删除，请评论：`@bot delete-rule {rule_id}`"
                    )
                else:
                    return (
                        f"✅ **收到误报反馈，自愈机制已启动！**\n\n"
                        f"已将您的上下文提炼为架构认知补丁并写入知识库（自动通过预筛）。\n"
                        f"下次命中相同路径的 PR 时，`{rule_name}` 的检查将被软化或拦截。\n\n"
                        f"> 提炼的认知: *{content_to_save}*\n\n"
                        f"Rule ID: `{rule_id}` (置信度: {confidence})\n"
                        f"如需撤销该规则，请评论：`@bot delete-rule {rule_id}`"
                    )
            except Exception as e:
                logger.error(f"Failed to parse Refiner json: {e}")
                return "❌ 无法解析反馈认知，自愈闭环失败。"
        return "❌ 无法生成规范资产。"
    except Exception as e:
        logger.error(f"LLM API Error during Refiner: {e}")
        return f"❌ 内部反思特工执行错误: {str(e)}"
