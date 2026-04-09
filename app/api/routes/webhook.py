import hmac
import hashlib
import logging
from fastapi import APIRouter, Request, HTTPException, BackgroundTasks
from app.core.config import settings
from app.services.github import parse_pr_diff
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
        action = payload.get("action")
        if action in ["opened", "synchronize", "reopened"]:
            logger.info(f"Processing PR event for action: {action}")
        # 3. Process the Diff and Run Graph Pipeline in background
        def background_job():
            try:
                diff_texts = parse_pr_diff(payload)
                trigger_review_pipeline(diff_texts)
            except Exception as e:
                logger.error(f"Error executing review pipeline: {e}")

        background_tasks.add_task(background_job)
        return {"status": "accepted", "message": "Code review pipeline triggered"}
            
    return {"status": "ignored", "message": "Event type or action not handled"}
