from typing import Optional
import logging

from fastapi import APIRouter, Request, Header, HTTPException
from app.core.security import verify_github_signature
from app.services.event_handler import handle_github_event

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/github")
async def github_webhook(
    request: Request,
    x_github_event: Optional[str] = Header(default=None, alias="X-GitHub-Event"),
    x_hub_signature_256: Optional[str] = Header(default=None, alias="X-Hub-Signature-256"),
):
    body = await request.body()

    logger.info(f"[webhook] Received event header: '{x_github_event}'")

    # Ping: GitHub's connectivity test — no signature, no body parsing needed
    if x_github_event == "ping":
        logger.info("[webhook] Ping received — responding pong")
        return {"status": "pong"}

    # All real events must have a valid signature
    try:
        verify_github_signature(body, x_hub_signature_256)
    except ValueError as e:
        logger.warning(f"[webhook] Signature verification failed: {e}")
        raise HTTPException(status_code=401, detail=str(e))

    # Parse JSON only after signature is verified
    try:
        payload = await request.json()
    except Exception as e:
        logger.error(f"[webhook] Failed to parse JSON body: {e}")
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    logger.info(f"[webhook] Signature verified. Dispatching event: '{x_github_event}'")

    await handle_github_event(event_type=x_github_event, payload=payload)

    return {"status": "ok"}