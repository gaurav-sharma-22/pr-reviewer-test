import os
import logging
from datetime import datetime, timezone
from typing import Optional
import motor.motor_asyncio

logger = logging.getLogger(__name__)

_client: Optional[motor.motor_asyncio.AsyncIOMotorClient] = None
_db = None


def get_db():
    global _client, _db
    if _db is None:
        uri = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
        db_name = os.getenv("MONGODB_DB", "pr_reviewer")
        _client = motor.motor_asyncio.AsyncIOMotorClient(uri)
        _db = _client[db_name]
        logger.info(f"[audit_logger] Connected to MongoDB: {db_name}")
    return _db


async def log_review_run(
    repo: str,
    pr_number: int,
    pr_title: str,
    action: str,
    diff_size: int,
    chunks_count: int,
    issues_found: int,
    risk: str,
    status: str,          # "success" | "failed"
    started_at: datetime,
    ended_at: datetime,
    error: Optional[str] = None,
) -> str:
    """Store a review run record in MongoDB. Returns the inserted document id."""
    db = get_db()

    duration_ms = int((ended_at - started_at).total_seconds() * 1000)

    doc = {
        "repo": repo,
        "pr_number": pr_number,
        "pr_title": pr_title,
        "action": action,
        "diff_size_chars": diff_size,
        "chunks_count": chunks_count,
        "issues_found": issues_found,
        "risk": risk,
        "status": status,
        "duration_ms": duration_ms,
        "started_at": started_at,
        "ended_at": ended_at,
        "error": error,
    }

    result = await db["review_runs"].insert_one(doc)
    run_id = str(result.inserted_id)
    logger.info(
        f"[audit_logger] Logged review run {run_id} | "
        f"repo={repo} pr=#{pr_number} status={status} "
        f"issues={issues_found} risk={risk} duration={duration_ms}ms"
    )
    return run_id