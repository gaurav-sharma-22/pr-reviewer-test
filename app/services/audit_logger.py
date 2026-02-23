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


async def get_last_reviewed_sha(repo: str, pr_number: int) -> Optional[str]:
    """Return the commit SHA of the last successful review for this PR, or None."""
    db = get_db()
    doc = await db["review_runs"].find_one(
        {"repo": repo, "pr_number": pr_number, "status": "success"},
        sort=[("ended_at", -1)],
    )
    if doc and doc.get("commit_sha"):
        sha = doc["commit_sha"]
        logger.info(f"[audit_logger] Last reviewed SHA for {repo}#{pr_number}: {sha[:8]}")
        return sha
    return None


async def log_review_run(
    repo: str,
    pr_number: int,
    pr_title: str,
    action: str,
    diff_size: int,
    chunks_count: int,
    issues_found: int,
    risk: str,
    status: str,
    started_at: datetime,
    ended_at: datetime,
    commit_sha: Optional[str] = None,
    review_mode: str = "full",
    error: Optional[str] = None,
) -> str:
    db = get_db()
    duration_ms = int((ended_at - started_at).total_seconds() * 1000)
    doc = {
        "repo": repo, "pr_number": pr_number, "pr_title": pr_title,
        "action": action, "diff_size_chars": diff_size, "chunks_count": chunks_count,
        "issues_found": issues_found, "risk": risk, "status": status,
        "duration_ms": duration_ms, "started_at": started_at,
        "ended_at": ended_at, "commit_sha": commit_sha,
        "review_mode": review_mode, "error": error,
    }
    result = await db["review_runs"].insert_one(doc)
    run_id = str(result.inserted_id)
    logger.info(
        f"[audit_logger] Logged review run {run_id} | "
        f"repo={repo} pr=#{pr_number} mode={review_mode} "
        f"status={status} issues={issues_found} risk={risk} duration={duration_ms}ms"
    )
    return run_id