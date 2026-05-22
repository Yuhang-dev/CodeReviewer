import logging
from pydantic import BaseModel
from typing import Literal
from fastapi import APIRouter, HTTPException
from app.services.rag import ingest_knowledge

router = APIRouter()
logger = logging.getLogger(__name__)

class GuidelineIngestRequest(BaseModel):
    content: str
    category: str = "general"
    path_regex: str = ".*"
    language: str = "all"
    severity: Literal["info", "warning", "error"] = "warning"
    rule_id: str = None
    status: Literal["staging", "production", "disabled"] = "production"
    rule_action: dict = {}
    metadata: dict = {}

@router.post("/ingest")
async def ingest_guideline(payload: GuidelineIngestRequest):
    """
    Receives raw Markdown/Text guidelines and ingests them into the Qdrant Knowledge Base.
    """
    if not payload.content.strip():
        raise HTTPException(status_code=400, detail="Content cannot be empty.")
        
    try:
        # In a real app this could be queued in background
        logger.info("Ingesting new knowledge...")
        ingested_ids = ingest_knowledge(
            content=payload.content,
            metadata=payload.metadata,
            category=payload.category,
            path_regex=payload.path_regex,
            language=payload.language,
            severity=payload.severity,
            rule_id=payload.rule_id,
            status=payload.status,
            rule_action=payload.rule_action
        )
        return {
            "status": "success", 
            "message": f"Successfully ingested {len(ingested_ids)} document chunks into Qdrant.",
            "ids": ingested_ids
        }
    except Exception as e:
        logger.error(f"Knowledge ingestion failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))
