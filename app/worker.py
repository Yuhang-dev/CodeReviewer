import asyncio
import logging
from celery import Celery
from app.core.config import settings

logger = logging.getLogger(__name__)

celery_app = Celery(
    "code_reviewer_tasks",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
)

# Celery task configuration
celery_app.conf.update(
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',
    timezone='UTC',
    enable_utc=True,
)

def run_async(coro):
    """Helper to run async functions synchronously in Celery"""
    return asyncio.run(coro)

@celery_app.task(name="tasks.review_pipeline_job")
def review_pipeline_job(repo_full_name: str, pr_number: int, commit_id: str, tier: str = "Tier-B", review_context: str = "", review_focus: str = "", base_branch: str = "master"):
    """
    【Celery 异步消费者核心】：执行真正耗时的 AI Review 流程
    
    工作流程：
    1. 通过 GitHub API 获取完整的 PR 变更代码 (Diff)。
    2. 触发 RAG 和双 Agent Pipeline (Reviewer + Critic) 进行深度代码审查。
    3. 容灾设计：尝试提交精确的行级评论 (Inline Review)。如果由于 API 路径问题或权限
       问题失败，自动 fallback（降级）为 PR 级别的全局评论，确保信息不丢失。
    """
    logger.info(f"Starting Celery review job for {repo_full_name}#{pr_number} with tier {tier}")
    from app.services.github import fetch_pr_files_data, post_pr_review, post_pr_comment, shallow_clone_repo
    from app.services.rag import trigger_review_pipeline
    
    try:
        files_data = run_async(fetch_pr_files_data(repo_full_name, pr_number, commit_id))
        if not files_data:
            logger.info("No files modified or failed to fetch files.")
            return

        # Clone the repo for Global Impact AST analysis if Tier requires it
        repo_path = ""
        if tier in ["TIER-S", "TIER-A", "Tier-S", "Tier-A"]:
            repo_path = shallow_clone_repo(repo_full_name, branch=base_branch)
            if not repo_path:
                logger.warning(f"Shallow clone failed for branch '{base_branch}'. AST analysis will be skipped.")
            
        result_data = trigger_review_pipeline(
            files_data,
            tier=tier,
            review_context=review_context,
            review_focus=review_focus,
            repo_path=repo_path
        )
        
        review_comments = result_data.get("comments", [])
        global_warning = result_data.get("global_warning", "")
        agent_trace_md = result_data.get("agent_trace_markdown", "")
        plan = result_data.get("plan", {})
        
        final_tier = plan.get("final_tier", tier)
        
        # 1. Post Inline Comments
        inline_success = False
        if review_comments:
            # Fix path issues (remove a/ or b/ prefixes)
            for c in review_comments:
                p = c.get("file", c.get("path", ""))
                if p.startswith("a/") or p.startswith("b/"):
                    c["path"] = p[2:]
                elif "file" in c:
                    c["path"] = c["file"]
            try:
                run_async(post_pr_review(repo_full_name, pr_number, commit_id, review_comments))
                inline_success = True
            except Exception as review_err:
                logger.error(f"Failed to post inline review, falling back to comment: {review_err}")
                fallback_md = f"### AI Review Report ({final_tier})\n\n⚠️ **无法精确定位代码行，改用全局评论：**\n\n"
                for c in review_comments:
                    fallback_md += f"- **{c.get('path', 'Unknown file')}** (Line {c.get('line', '?')}): {c.get('body', c.get('comment', ''))}\n"
                run_async(post_pr_comment(repo_full_name, pr_number, fallback_md))
                inline_success = True
                
        # 2. Build PR-Level Summary
        summary_md = ""
        if global_warning:
            summary_md += f"⚠️ **Global Impact Warning ( {final_tier} )** ⚠️\n\n{global_warning}\n\n---\n\n"
            
        if not review_comments:
            summary_md += f"AI Code Review completed ({final_tier}). LGTM! 👍\n\n"
        else:
            summary_md += f"AI Code Review completed ({final_tier}). See inline comments for details.\n\n"
            
        if agent_trace_md:
            summary_md += agent_trace_md
            
        # 3. Post PR-Level Summary
        if summary_md:
            run_async(post_pr_comment(repo_full_name, pr_number, summary_md))
            
    except Exception as e:
        logger.error(f"Error executing review pipeline in Celery: {e}")

@celery_app.task(name="tasks.refiner_pipeline_job")
def refiner_pipeline_job(repo_full_name: str, pr_number: int, comment_body: str, event_type: str, comment_id: int):
    logger.info(f"Starting Celery refiner job for {repo_full_name}#{pr_number}")
    from app.services.github import fetch_pr_diff, post_pr_comment, post_pr_review_reply
    from app.services.rag import trigger_refiner_pipeline
    
    try:
        diff_text = run_async(fetch_pr_diff(repo_full_name, pr_number))
        reply = trigger_refiner_pipeline(diff_text, comment_body)
        
        if event_type == "pull_request_review_comment" and comment_id:
            run_async(post_pr_review_reply(repo_full_name, pr_number, comment_id, reply))
        else:
            run_async(post_pr_comment(repo_full_name, pr_number, reply))
    except Exception as e:
        logger.error(f"Error executing refiner pipeline in Celery: {e}")

@celery_app.task(name="tasks.chat_pipeline_job")
def chat_pipeline_job(repo_full_name: str, pr_number: int, comment_body: str, event_type: str, comment_id: int):
    logger.info(f"Starting Celery chat job for {repo_full_name}#{pr_number}")
    from app.services.github import fetch_pr_diff, post_pr_comment, post_pr_review_reply
    from app.services.rag import trigger_chat_pipeline
    
    try:
        diff_text = run_async(fetch_pr_diff(repo_full_name, pr_number))
        chat_reply = trigger_chat_pipeline(diff_text, comment_body)
        
        if event_type == "pull_request_review_comment" and comment_id:
            run_async(post_pr_review_reply(repo_full_name, pr_number, comment_id, chat_reply))
        else:
            run_async(post_pr_comment(repo_full_name, pr_number, chat_reply))
    except Exception as e:
        logger.error(f"Error executing chat pipeline in Celery: {e}")
