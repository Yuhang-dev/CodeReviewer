import sys
import codecs

try:
    rag_path = '/app/app/services/rag.py'
    
    with codecs.open(rag_path, 'r', 'utf-8') as f:
        content = f.read()

    start_str = 'def trigger_review_pipeline'
    end_str = 'return {"comments": all_reviews, "global_warning": global_warning}'
    
    start_idx = content.find(start_str)
    end_idx = content.find(end_str)
    
    if start_idx == -1 or end_idx == -1:
        print('Could not find start or end block.')
        sys.exit(1)
        
    end_idx += len(end_str)
    
    replacement = '''def trigger_review_pipeline(pr_files_data: list[dict], tier: str = "Tier-B", review_context: str = "", review_focus: str = "", repo_path: str = "") -> dict:
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
        combined_diffs += f"\\nFile: {file_data['filename']}\\n{file_data['patch']}\\n"
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
            all_trace_mds.append(f"### File: {result.get('filename')}\\n{trace_md}")
            
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
            "repo_path": repo_path,
            "pr_filenames": pr_filenames,
            "agent_trace": []
        }
        res = global_impact_graph.invoke(gl_state)
        global_warning = res.get("review_result", "")
        
    combined_trace_md = "\\n\\n".join(all_trace_mds)
        
    return {
        "comments": all_reviews, 
        "global_warning": global_warning,
        "agent_trace": all_traces,
        "agent_trace_markdown": combined_trace_md,
        "plan": final_plan
    }'''
    
    new_content = content[:start_idx] + replacement + content[end_idx:]
    
    with codecs.open(rag_path, 'w', 'utf-8') as f:
        f.write(new_content)
        
    print('Successfully replaced trigger_review_pipeline in rag.py')
except Exception as e:
    print(e)
