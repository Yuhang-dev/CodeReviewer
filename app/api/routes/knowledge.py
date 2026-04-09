import logging
from pydantic import BaseModel
from fastapi import APIRouter, HTTPException
from app.services.rag import ingest_knowledge

router = APIRouter()
logger = logging.getLogger(__name__)

class KnowledgePayload(BaseModel):
    content: str
    metadata: dict = {}

@router.post("/ingest")
async def ingest_guideline(payload: KnowledgePayload):
    """
    Receives raw Markdown/Text guidelines and ingests them into the Qdrant Knowledge Base.
    """
    if not payload.content.strip():
        raise HTTPException(status_code=400, detail="Content cannot be empty.")
        
    try:
        # In a real app this could be queued in background
        logger.info("Ingesting new knowledge...")
        ingested_count = ingest_knowledge(payload.content, payload.metadata)
        return {
            "status": "success", 
            "message": f"Successfully ingested {ingested_count} document chunks into Qdrant."
        }
    except Exception as e:
        logger.error(f"Knowledge ingestion failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))
