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
    x_github_event: Optional[str] = Header(
        default=None,
        alias="X-GitHub-Event"
    ),
    x_hub_signature_256: Optional[str] = Header(
        default=None,
        alias="X-Hub-Signature-256"
    ),
):
    body = await request.body()
    payload = await request.json()

    logger.warning(f"EVENT HEADER VALUE = {x_github_event}")

    # ✅ 1. ALWAYS accept ping (NO signature)
    if x_github_event == "ping":
        logger.warning("PING RECEIVED — BYPASSING ALL AUTH")
        return {"status": "pong"}

    # ✅ 2. Verify signature ONLY for real events
    try:
        verify_github_signature(body, x_hub_signature_256)
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))

    # ✅ 3. Process event
    await handle_github_event(
        event_type=x_github_event,
        payload=payload
    )

    return {"status": "ok"}
