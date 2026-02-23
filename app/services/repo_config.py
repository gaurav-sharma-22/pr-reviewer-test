import logging
from typing import Optional
from app.services.audit_logger import get_db

logger = logging.getLogger(__name__)

DEFAULT_CONFIG = {
    "max_issues": 10,
    "agents": ["security", "code_quality", "performance", "tests"],
    "min_severity": "low",
    "ignore_patterns": [],
}


async def get_repo_config(repo: str) -> dict:
    """Load repo config from MongoDB, falling back to defaults."""
    db = get_db()
    doc = await db["repo_configs"].find_one({"repo": repo})
    if doc:
        config = {**DEFAULT_CONFIG, **{k: v for k, v in doc.items() if k != "_id"}}
        logger.info(f"[repo_config] Loaded config for {repo}: agents={config['agents']} max_issues={config['max_issues']}")
    else:
        config = {**DEFAULT_CONFIG, "repo": repo}
        logger.info(f"[repo_config] No config found for {repo} — using defaults")
    return config


async def upsert_repo_config(repo: str, updates: dict) -> dict:
    """Create or update repo config in MongoDB."""
    db = get_db()
    allowed_keys = {"max_issues", "agents", "min_severity", "ignore_patterns"}
    clean = {k: v for k, v in updates.items() if k in allowed_keys}
    await db["repo_configs"].update_one(
        {"repo": repo},
        {"$set": {"repo": repo, **clean}},
        upsert=True,
    )
    logger.info(f"[repo_config] Upserted config for {repo}: {clean}")
    return await get_repo_config(repo)


async def delete_repo_config(repo: str) -> bool:
    """Delete repo config, reverting to defaults."""
    db = get_db()
    result = await db["repo_configs"].delete_one({"repo": repo})
    deleted = result.deleted_count > 0
    logger.info(f"[repo_config] Deleted config for {repo}: {deleted}")
    return deleted