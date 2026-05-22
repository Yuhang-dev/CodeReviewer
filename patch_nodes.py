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
    t1 = tier1.strip().upper()
    t2 = tier2.strip().upper()
    
    # Handle casing (expecting like Tier-C)
    t1 = f"Tier-{t1[-1]}" if t1.startswith("TIER-") else tier1
    t2 = f"Tier-{t2[-1]}" if t2.startswith("TIER-") else tier2
    
    rank1 = TIER_RANK.get(t1, 1) # default B
    rank2 = TIER_RANK.get(t2, 1)
    
    return t1 if rank1 >= rank2 else t2

def planner_step(state: AgentState):
    """LangGraph node: Determines review strategy and tier resolution."""
    logger.info("Executing planner_step...")
    diff_text = state.get('diff_text', '')
    user_tier = state.get('user_requested_tier') or state.get('tier')
    pr_filenames = state.get('pr_filenames', [])
    
    llm = init_llm().with_structured_output(PlannerOutput)
    
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
"""
    try:
        plan: PlannerOutput = llm.invoke([HumanMessage(content=prompt)])
        plan_dict = plan.dict()
        
        trace_event = {
            "node": "planner_step",
            "summary": f"Resolved tier to {plan.final_tier} (User: {user_tier}, System: {plan.system_inferred_tier})",
            "data": {"selected_checks": plan.selected_checks, "reason": plan.tier_resolution_reason}
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
        return {"plan": fallback, "agent_trace": state.get("agent_trace", []) + [{"node": "planner_step", "summary": "Planner failed, used fallback", "data": {}}]}

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
        "summary": f"Retrieved guidelines. Disabled skills: {disabled_skills}",
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
            "agent_trace": state.get("agent_trace", []) + [{"node": "reviewer_step", "summary": "Skipped due to Tier-C", "data": {}}]
        }
        
    # Same skill logic as before, run print_statement_check, hardcoded_secrets_check, etc.
    # ... I will copy the skill execution logic ...
    
    llm = init_llm().with_structured_output(ReviewerOutput)
    
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
        f"要求：\n1. {tier_requirements}\n2. Diff 中每行以 L+数字 开头（如 L15），你必须使用该真实行号！\n\n"
        f"{context_str}\n"
        f"【完整文件上下文】:\n{state.get('full_files_context', '')}\n\n"
        f"【代码 Diff 变更】:\n{annotate_diff_with_line_numbers(diff_text)}\n"
    )
    
    try:
        response: ReviewerOutput = llm.invoke([HumanMessage(content=prompt)])
        raw_reviews = [f.dict() for f in response.findings]
        
        trace_event = {
            "node": "reviewer_step",
            "summary": f"Generated {len(raw_reviews)} candidate findings",
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
        llm = init_llm().with_structured_output(CriticDecision)
        prompt = f"""You are a Critic Agent. Verify the following candidate code review findings.
Drop a finding if:
- It is purely a subjective style preference without evidence.
- The evidence provided does not strongly support the comment.
- It contradicts best practices.

Candidate findings:
{llm_candidates}

Output which to keep and which to drop (with reasons).
"""
        try:
            decision: CriticDecision = llm.invoke([HumanMessage(content=prompt)])
            final_reviews = [f.dict() for f in decision.kept]
            for d in decision.dropped:
                dropped.append(d)
        except Exception as e:
            logger.error(f"Critic LLM failed: {e}")
            # Fallback: keep them all if critic fails
            final_reviews = llm_candidates
            
    trace_event = {
        "node": "critic_step",
        "summary": f"Critic verified findings. Kept: {len(final_reviews)}, Dropped: {len(dropped)}",
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
    final_comments = final_reviews # already list of dicts: file, line, comment
    
    # 2. Format Trace Markdown
    trace_md = "<details>\n<summary>🤖 Agent Trace</summary>\n\n"
    for event in agent_trace:
        trace_md += f"- **{event.get('node')}**: {event.get('summary')}\n"
    trace_md += "\n</details>"
    
    return {
        "final_comments": final_comments,
        "trace_markdown": trace_md
    }
