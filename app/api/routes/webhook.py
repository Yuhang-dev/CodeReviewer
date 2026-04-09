import hmac
import hashlib
import logging
from fastapi import APIRouter, Request, HTTPException, BackgroundTasks
from app.core.config import settings
from app.services.github import fetch_pr_diff, post_pr_comment
from app.services.rag import trigger_review_pipeline

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
            logger.info(f"Processing PR event for {repo_full_name}#{pr_number}, action: {action}")
            
            # 3. Process the Diff and Run Graph Pipeline in background
            async def background_job():
                try:
                    # Fetch real diff patch
                    diff_text = await fetch_pr_diff(repo_full_name, pr_number)
                    
                    # trigger_review_pipeline is synchronous
                    result_state = trigger_review_pipeline([diff_text])
                    review_comment = result_state.get("review_result", "No review generated.")
                    
                    # Post review comment back to GitHub
                    await post_pr_comment(repo_full_name, pr_number, review_comment)
                except Exception as e:
                    logger.error(f"Error executing review pipeline: {e}")

            background_tasks.add_task(background_job)
            return {"status": "accepted", "message": "Code review pipeline triggered"}
            
    elif event_type == "issue_comment":
        action = payload.get("action")
        if action == "created":
            sender = payload.get("sender", {}).get("login", "")
            if "bot" in sender.lower() or sender == "github-actions[bot]":
                return {"status": "ignored", "message": "Ignored bot comment."}

            # Only process comments on PRs
            if "pull_request" not in payload.get("issue", {}):
                return {"status": "ignored", "message": "Not a pull request comment."}

            comment_body = payload.get("comment", {}).get("body", "")
            # Only trigger if the user mentions "@ai" or "@bot"
            if "@ai" in comment_body.lower() or "@bot" in comment_body.lower():
                repo_full_name = payload["repository"]["full_name"]
                pr_number = payload["issue"]["number"]
                logger.info(f"Processing chat comment for {repo_full_name}#{pr_number}")

                async def background_chat_job():
                    try:
                        from app.services.rag import trigger_chat_pipeline
                        # Fetch diff context
                        diff_text = await fetch_pr_diff(repo_full_name, pr_number)
                        # TODO: Fetch chat history from GitHub
                        
                        # run chat pipeline
                        chat_reply = trigger_chat_pipeline(diff_text, comment_body)
                        await post_pr_comment(repo_full_name, pr_number, chat_reply)
                    except Exception as e:
                        logger.error(f"Error executing chat pipeline: {e}")
                
                background_tasks.add_task(background_chat_job)
                return {"status": "accepted", "message": "Chat pipeline triggered"}

    return {"status": "ignored", "message": "Event type or action not handled"}
