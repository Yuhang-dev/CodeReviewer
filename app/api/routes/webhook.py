import hmac
import hashlib
import logging
from fastapi import APIRouter, Request, HTTPException, BackgroundTasks
from app.core.config import settings
from app.services.github import post_pr_comment, fetch_pr_head_commit, post_pr_review, fetch_pr_files_data, fetch_pr_diff, post_pr_review_reply
from app.services.rag import trigger_review_pipeline
import json

router = APIRouter()
logger = logging.getLogger(__name__)

async def verify_signature(request: Request) -> bool:
    """Verifies the GitHub webhook signature using HMAC."""
    if not settings.GITHUB_WEBHOOK_SECRET:
        logger.warning("GITHUB_WEBHOOK_SECRET is not set, skipping signature verification!")
        return True

    signature_header = request.headers.get("x-hub-signature-256")
    if not signature_header:
        raise HTTPException(status_code=400, detail="Missing X-Hub-Signature-256 header")

    body = await request.body()
    hash_object = hmac.new(
        settings.GITHUB_WEBHOOK_SECRET.encode("utf-8"), 
        msg=body, 
        digestmod=hashlib.sha256
    )
    expected_signature = "sha256=" + hash_object.hexdigest()
    
    if not hmac.compare_digest(expected_signature, signature_header):
        raise HTTPException(status_code=401, detail="Invalid signature")

    return True

@router.post("/github")
async def github_webhook(request: Request, background_tasks: BackgroundTasks):
    """
    【核心路由：GitHub Webhook 流量入口】
    接收并处理来自 GitHub 的 push 和 PR 事件。
    
    架构设计亮点：
    1. 极致轻量：不在此函数内做任何耗时的网络或 LLM 推理计算。
    2. 安全性保障：通过 HMAC 算法校验请求签名，防止恶意伪造 Payload。
    3. 异步解耦：仅进行 Metadata 解析，随后将任务状态封装进 Celery 队列，
       立即返回 200 OK 给 GitHub，彻底避免 10s 超时截断问题。
    """
    # 1. Verify Request Signature (防止伪造攻击)
    await verify_signature(request)
    
    # 2. Parse Payload
    event_type = request.headers.get("x-github-event")
    payload = await request.json()
    
    logger.info(f"Received GitHub webhook event: {event_type}")

    # We typically only care about PR opened/synchronized events for code review
    if event_type == "pull_request":
        sender = payload.get("sender", {}).get("login", "")
        # Prevent infinite loops if the bot itself modifies the PR (e.g. "bot" suffix users)
        if "bot" in sender.lower() or sender == "github-actions[bot]":
            logger.info(f"Ignoring PR event triggered by a bot: {sender}")
            return {"status": "ignored", "message": "Ignored bot event."}

        action = payload.get("action")
        if action in ["opened", "synchronize", "reopened"]:
            repo_full_name = payload["repository"]["full_name"]
            pr_number = payload["pull_request"]["number"]
            pr_body = payload["pull_request"].get("body", "") or ""
            # Extract the actual base branch name from the PR payload (could be 'master', 'main', 'develop', etc.)
            base_branch = payload["pull_request"]["base"]["ref"]
            logger.info(f"Processing PR event for {repo_full_name}#{pr_number}, action: {action}, base_branch: {base_branch}")
            
            # Parse Tiered Code Review Metadata
            import re
            tier = "Tier-B"
            review_context = ""
            review_focus = ""
            
            match = re.search(r'>>>REVIEW_METADATA<<<(.*?)(?:>>>END<<<|$)', pr_body, re.DOTALL)
            if match:
                meta_text = match.group(1).strip()
                tier_match = re.search(r'Tier:\s*(Tier-[SABC])', meta_text, re.IGNORECASE)
                if tier_match:
                    tier = tier_match.group(1).upper()
                
                context_match = re.search(r'Context:\s*(.*?)(?=\n(?:Tier|Focus):|$)', meta_text, re.IGNORECASE | re.DOTALL)
                if context_match:
                    review_context = context_match.group(1).strip()
                    
                focus_match = re.search(r'Focus:\s*(.*?)(?=\n(?:Tier|Context):|$)', meta_text, re.IGNORECASE | re.DOTALL)
                if focus_match:
                    review_focus = focus_match.group(1).strip()
                    
            logger.info(f"Metadata parsed - Tier: {tier}, Focus: {review_focus}")
            
            # Fetch head commit ID early
            commit_id = await fetch_pr_head_commit(repo_full_name, pr_number)
            
            # 3. Process the Diff and Run Graph Pipeline in background
            from app.worker import review_pipeline_job
            review_pipeline_job.delay(
                repo_full_name, 
                pr_number, 
                commit_id, 
                tier=tier, 
                review_context=review_context, 
                review_focus=review_focus,
                base_branch=base_branch
            )
            
            return {"status": "accepted", "message": "Code review pipeline triggered via Celery"}
            
    elif event_type in ["issue_comment", "pull_request_review_comment"]:
        action = payload.get("action")
        if action == "created":
            sender = payload.get("sender", {}).get("login", "")
            if "bot" in sender.lower() or sender == "github-actions[bot]":
                return {"status": "ignored", "message": "Ignored bot comment."}

            if event_type == "issue_comment" and "pull_request" not in payload.get("issue", {}):
                return {"status": "ignored", "message": "Not a pull request comment."}

            comment_body = payload.get("comment", {}).get("body", "")
            repo_full_name = payload["repository"]["full_name"]
            
            # Extract PR number depending on event type
            if event_type == "issue_comment":
                pr_number = payload["issue"]["number"]
            else:
                pr_number = payload["pull_request"]["number"]
                
            import re
            is_approve_trigger = re.search(r"@(?:bot|ai)\s+approve-rule\s+([a-f0-9\-]+)", comment_body.lower())
            is_delete_trigger = re.search(r"@(?:bot|ai)\s+delete-rule\s+([a-f0-9\-]+)", comment_body.lower())
            is_refiner_trigger = "[误报]" in comment_body or "[misjudge]" in comment_body.lower()
            is_review_trigger = re.search(r"@(?:bot|ai)\s+review", comment_body.lower())
            is_chat_trigger = ("@ai" in comment_body.lower() or "@bot" in comment_body.lower()) and not (is_review_trigger or is_approve_trigger or is_delete_trigger)
            
            # The id of the comment we are replying to
            comment_id = payload.get("comment", {}).get("id")

            if is_approve_trigger:
                rule_id = is_approve_trigger.group(1)
                logger.info(f"Processing rule approval for {rule_id}")
                from app.services.rag import approve_knowledge_rule
                success = approve_knowledge_rule(rule_id)
                msg = f"✅ 规则 `{rule_id}` 已成功批准并移入生产环境生效。" if success else f"❌ 批准规则 `{rule_id}` 失败，可能该 ID 不存在或发生了内部错误。"
                if event_type == "pull_request_review_comment" and comment_id:
                    background_tasks.add_task(post_pr_review_reply, repo_full_name, pr_number, comment_id, msg)
                else:
                    background_tasks.add_task(post_pr_comment, repo_full_name, pr_number, msg)
                return {"status": "accepted", "message": "Rule approved."}

            if is_delete_trigger:
                rule_id = is_delete_trigger.group(1)
                logger.info(f"Processing rule deletion for {rule_id}")
                from app.services.rag import delete_knowledge_rule
                success = delete_knowledge_rule(rule_id)
                msg = f"✅ 规则 `{rule_id}` 已从知识库中彻底删除。" if success else f"❌ 删除规则 `{rule_id}` 失败，可能该 ID 不存在或发生了内部错误。"
                if event_type == "pull_request_review_comment" and comment_id:
                    background_tasks.add_task(post_pr_review_reply, repo_full_name, pr_number, comment_id, msg)
                else:
                    background_tasks.add_task(post_pr_comment, repo_full_name, pr_number, msg)
                return {"status": "accepted", "message": "Rule deleted."}

            if is_refiner_trigger:
                logger.info(f"Processing Refiner feedback for {repo_full_name}#{pr_number}")
                from app.worker import refiner_pipeline_job
                refiner_pipeline_job.delay(repo_full_name, pr_number, comment_body, event_type, comment_id)
                return {"status": "accepted", "message": "Refiner pipeline triggered via Celery"}
                
            elif is_review_trigger:
                logger.info(f"Processing manual review request for {repo_full_name}#{pr_number}")
                from app.worker import review_pipeline_job
                # Fetch head commit ID early
                commit_id = await fetch_pr_head_commit(repo_full_name, pr_number)
                # Parse tier and context from PR body if needed, or default
                pr_body = payload["pull_request"].get("body", "") or "" if "pull_request" in payload else ""
                tier = "Tier-B"
                review_context = ""
                review_focus = ""
                import re
                match = re.search(r'>>>REVIEW_METADATA<<<(.*?)(?:>>>END<<<|$)', pr_body, re.DOTALL)
                if match:
                    meta_text = match.group(1).strip()
                    tier_match = re.search(r'Tier:\s*(Tier-[SABC])', meta_text, re.IGNORECASE)
                    if tier_match:
                        tier = tier_match.group(1).upper()
                    
                    context_match = re.search(r'Context:\s*(.*?)(?=\n(?:Tier|Focus):|$)', meta_text, re.IGNORECASE | re.DOTALL)
                    if context_match:
                        review_context = context_match.group(1).strip()
                        
                    focus_match = re.search(r'Focus:\s*(.*?)(?=\n(?:Tier|Context):|$)', meta_text, re.IGNORECASE | re.DOTALL)
                    if focus_match:
                        review_focus = focus_match.group(1).strip()

                base_branch = payload["pull_request"]["base"]["ref"] if "pull_request" in payload else "master"
                
                review_pipeline_job.delay(
                    repo_full_name, 
                    pr_number, 
                    commit_id, 
                    tier=tier, 
                    review_context=review_context, 
                    review_focus=review_focus,
                    base_branch=base_branch
                )
                return {"status": "accepted", "message": "Manual review triggered"}
                
            elif is_chat_trigger:
                logger.info(f"Processing chat comment for {repo_full_name}#{pr_number}")
                from app.worker import chat_pipeline_job
                chat_pipeline_job.delay(repo_full_name, pr_number, comment_body, event_type, comment_id)
                return {"status": "accepted", "message": "Chat pipeline triggered via Celery"}

    return {"status": "ignored", "message": "Event type or action not handled"}
