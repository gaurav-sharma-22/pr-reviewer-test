import logging
from fastapi import FastAPI
from app.api.webhooks import router as webhook_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

app = FastAPI(title="Agentic PR Reviewer")
app.include_router(webhook_router, prefix="/webhooks")