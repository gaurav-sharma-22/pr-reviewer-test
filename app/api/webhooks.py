from typing import Optional
import logging

from fastapi import APIRouter, Request, Header, HTTPException
from app.core.security import verify_github_signature
from app.services.event_handler import handle_github_event

router = APIRouter()
logger = logging.getLogger(__name__)

# Events we care about — all others are acknowledged but not processed
HANDLED_EVENTS = {"pull_request", "issue_comment", "ping"}


@router.post("/github")
async def github_webhook(
    request: Request,
    x_github_event: Optional[str] = Header(default=None, alias="X-GitHub-Event"),
    x_hub_signature_256: Optional[str] = Header(default=None, alias="X-Hub-Signature-256"),
):
    body = await request.body()
    payload = await request.json()

    event = x_github_event or "unknown"
    logger.info(f"[webhook] Received event header: '{event}'")

    # Always accept ping without signature check
    if event == "ping":
        logger.info("[webhook] Ping received — responding pong")
        return {"status": "pong"}

    # Verify signature for all real events
    try:
        verify_github_signature(body, x_hub_signature_256)
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))

    logger.info(f"[webhook] Signature verified. Dispatching event: '{event}'")

    await handle_github_event(event_type=event, payload=payload)

    return {"status": "ok"}