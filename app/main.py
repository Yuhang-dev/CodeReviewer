from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import logging

from app.api.routes import webhook
from app.core.config import settings

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def create_app() -> FastAPI:
    app = FastAPI(title=settings.PROJECT_NAME)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Include routes
    from app.api.routes import webhook, knowledge
    app.include_router(webhook.router, prefix="/webhook", tags=["webhooks"])
    app.include_router(knowledge.router, prefix="/knowledge", tags=["knowledge"])
    @app.get("/health")
    async def health_check():
        return {"status": "healthy"}

    return app

app = create_app()
