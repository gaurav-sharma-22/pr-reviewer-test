from dotenv import load_dotenv
load_dotenv()

import logging
from fastapi import FastAPI
from app.api.webhooks import router as webhook_router
from app.api.config_routes import router as config_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

logger = logging.getLogger(__name__)

app = FastAPI(title="Agentic PR Reviewer")
app.include_router(webhook_router, prefix="/webhooks")
app.include_router(config_router, prefix="/config")


@app.on_event("startup")
async def startup():
    import os
    secret_set = bool(os.getenv("GITHUB_WEBHOOK_SECRET"))
    logger.info(f"[startup] Server ready. GITHUB_WEBHOOK_SECRET set: {secret_set}")
    if not secret_set:
        logger.warning("[startup] ⚠️  GITHUB_WEBHOOK_SECRET is not set — all non-ping webhooks will be rejected")