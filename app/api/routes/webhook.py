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
    Receives push/PR events from GitHub, validates the signature, 
    and triggers the Agentic RAG pipeline in the background.
    """
    # 1. Verify Request Signature
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
            logger.info(f"Processing PR event for {repo_full_name}#{pr_number}, action: {action}")
            
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
            
            # 3. Process the Diff and Run Graph Pipeline in background
            async def background_job():
                try:
                    # Fetch head commit ID early
                    commit_id = await fetch_pr_head_commit(repo_full_name, pr_number)
                    
                    # Fetch structured files data per file
                    pr_files_data = await fetch_pr_files_data(repo_full_name, pr_number, commit_id)
                    
                    # trigger_review_pipeline is synchronous
                    result_data = trigger_review_pipeline(
                        pr_files_data, 
                        tier=tier,
                        review_context=review_context,
                        review_focus=review_focus
                    )
                    
                    review_comments = result_data.get("comments", [])
                    global_warning = result_data.get("global_warning", "")
                    
                    if global_warning:
                        await post_pr_comment(repo_full_name, pr_number, f"⚠️ **Global Impact Warning ( {tier} )** ⚠️\n\n{global_warning}")
                    
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
                            await post_pr_review(repo_full_name, pr_number, commit_id, review_comments)
                            inline_success = True
                        except Exception as e:
                            logger.warning(f"Failed to post inline review (fallback to general): {e}")
                            fallback_md = "⚠️ **无法精确定位代码行，改用全局评论：**\n\n"
                            for c in review_comments:
                                fallback_md += f"- **{c.get('file', 'Unknown File')}** (Line {c.get('line', '?')}): {c.get('comment', '')}\n"
                            await post_pr_comment(repo_full_name, pr_number, fallback_md)
                            inline_success = True  # We handled the fallback
                            
                    if inline_success:
                        return
                        
                    # If empty
                    if not review_comments:
                        await post_pr_comment(repo_full_name, pr_number, f"AI Code Review completed ({tier}). LGTM! 👍")
                except Exception as e:
                    logger.error(f"Error executing review pipeline: {e}")

            background_tasks.add_task(background_job)
            return {"status": "accepted", "message": "Code review pipeline triggered"}
            
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
                
            is_refiner_trigger = "[误报]" in comment_body or "[misjudge]" in comment_body.lower()
            is_chat_trigger = "@ai" in comment_body.lower() or "@bot" in comment_body.lower()
            
            # The id of the comment we are replying to
            comment_id = payload.get("comment", {}).get("id")

            if is_refiner_trigger:
                logger.info(f"Processing Refiner feedback for {repo_full_name}#{pr_number}")
                
                async def background_refiner_job():
                    try:
                        from app.services.rag import trigger_refiner_pipeline
                        # Fetch diff context
                        diff_text = await fetch_pr_diff(repo_full_name, pr_number)
                        
                        # run refiner pipeline
                        reply = trigger_refiner_pipeline(diff_text, comment_body)
                        if event_type == "pull_request_review_comment" and comment_id:
                            await post_pr_review_reply(repo_full_name, pr_number, comment_id, reply)
                        else:
                            await post_pr_comment(repo_full_name, pr_number, reply)
                    except Exception as e:
                        logger.error(f"Error executing refiner pipeline: {e}")
                
                background_tasks.add_task(background_refiner_job)
                return {"status": "accepted", "message": "Refiner pipeline triggered"}
                
            elif is_chat_trigger:
                logger.info(f"Processing chat comment for {repo_full_name}#{pr_number}")

                async def background_chat_job():
                    try:
                        from app.services.rag import trigger_chat_pipeline
                        diff_text = await fetch_pr_diff(repo_full_name, pr_number)
                        chat_reply = trigger_chat_pipeline(diff_text, comment_body)
                        if event_type == "pull_request_review_comment" and comment_id:
                            await post_pr_review_reply(repo_full_name, pr_number, comment_id, chat_reply)
                        else:
                            await post_pr_comment(repo_full_name, pr_number, chat_reply)
                    except Exception as e:
                        logger.error(f"Error executing chat pipeline: {e}")
                
                background_tasks.add_task(background_chat_job)
                return {"status": "accepted", "message": "Chat pipeline triggered"}

    return {"status": "ignored", "message": "Event type or action not handled"}
