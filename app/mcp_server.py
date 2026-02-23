from dotenv import load_dotenv
load_dotenv()

# Required .env variables:
# AZURE_OPENAI_API_KEY, AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_DEPLOYMENT, AZURE_OPENAI_API_VERSION
# GITHUB_APP_ID, GITHUB_INSTALLATION_ID, GITHUB_PRIVATE_KEY_PATH
# MONGODB_URI, MONGODB_DB

import logging
from typing import Optional
from fastmcp import FastMCP

from app.services.github_client import (
    get_installation_token,
    get_pr_info,
    get_pr_diff,
    get_commit_diff,
    upsert_pr_comment,
    get_bot_inline_comments,
)
from app.services.review_agent import review_diff, format_review_comment
from app.services.audit_logger import get_db, log_review_run, get_last_reviewed_sha
from app.services.repo_config import get_repo_config, upsert_repo_config, delete_repo_config
from app.services.event_handler import _clear_old_inline_comments, _post_inline_comments
# Note: _run_review is intentionally NOT imported.
# It returns None and calls log_review_run internally — using it would
# double-log every MCP run and we'd lose the run_id needed in tool responses.
from datetime import datetime, timezone

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)

mcp = FastMCP("PR Reviewer")


# ── Constants ───────────────────────────────────────────────────────────────────────────────

# Valid agent names - must match planner.py and agent prompt filenames
VALID_AGENTS = {"security", "code_quality", "performance", "tests"}

# Maps focus keywords to agent lists.
# Mirrors SLASH_AGENT_MAP in event_handler.py exactly.
FOCUS_AGENT_MAP: dict = {
    "security":    ["security"],
    "perf":        ["performance"],
    "performance": ["performance"],
    "quality":     ["code_quality"],
    "tests":       ["tests"],
    "test":        ["tests"],
}

VALID_SEVERITIES = {"low", "medium", "high"}


# ── Structured response helpers ──────────────────────────────────────────────

def ok(data: dict | list) -> dict:
    return {"success": True, "data": data, "error": None}


def err(message: str) -> dict:
    return {"success": False, "data": None, "error": message}


def _validate_repo(repo: str) -> str | None:
    """Return an error string if repo is not in owner/repo format, else None."""
    if repo.startswith("http") or "/" not in repo or repo.count("/") != 1:
        return (
            f"Invalid repo format: '{repo}'. "
            "Expected 'owner/repo' e.g. 'gaurav-sharma-22/pr-reviewer-test', "
            "not a full GitHub URL."
        )
    return None


# ─────────────────────────────────────────────────────────────────────────────
# TOOL 1: review_pr
#
# Full review — fetches the complete PR diff, runs all (or selected) agents,
# posts a summary comment + inline comments, and logs the run to audit.
# Mirrors handle_pull_request_event for "opened" / "reopened".
# ─────────────────────────────────────────────────────────────────────────────
@mcp.tool()
async def review_pr(
    repo: str,
    pr_number: int,
    agents: Optional[list[str]] = None,
    max_issues: Optional[int] = None,
    min_severity: Optional[str] = None,
) -> dict:
    """
    Trigger a full AI review on a GitHub PR.

    Fetches the full PR diff, runs the configured agents (security,
    code_quality, performance, tests), posts a summary comment on the PR,
    posts inline comments on high/medium issues, and logs the run.

    Args:
        repo:         Full repo name e.g. 'owner/repo'
        pr_number:    PR number to review
        agents:       Optional agent override e.g. ['security', 'code_quality'].
                      Valid values: security, code_quality, performance, tests.
                      Defaults to repo config (all 4 agents).
        max_issues:   Optional cap on issues returned. Defaults to repo config.
        min_severity: Optional minimum severity for inline comments.
                      Valid values: 'low', 'medium', 'high'. Defaults to repo config.

    Returns:
        run_id, risk level, summary, issues list, checklist, inline_comments_posted.
    """
    logger.info(f"[mcp:review_pr] repo={repo} pr=#{pr_number} agents={agents} max_issues={max_issues}")
    try:
        if (e := _validate_repo(repo)): return err(e)
        if agents:
            invalid = set(agents) - VALID_AGENTS
            if invalid:
                return err(f"Invalid agents: {invalid}. Valid values: {VALID_AGENTS}")

        if min_severity and min_severity not in VALID_SEVERITIES:
            return err("min_severity must be 'low', 'medium', or 'high'")

        token = await get_installation_token()
        pr_info = await get_pr_info(repo, pr_number, token)
        pr_title = pr_info.get("title", "untitled")
        head_sha = pr_info.get("head", {}).get("sha")

        config = await get_repo_config(repo)
        if agents:
            config = {**config, "agents": agents}
        if max_issues is not None:
            config = {**config, "max_issues": max_issues}
        if min_severity:
            config = {**config, "min_severity": min_severity}

        diff = await get_pr_diff(repo, pr_number, token)
        if not diff.strip():
            return ok({"summary": "Empty diff — nothing to review.", "risk": "low", "issues": [], "checklist": []})

        started_at = datetime.now(timezone.utc)
        review = await review_diff(diff, pr_title, config=config)
        issues = review.get("issues", [])

        await _clear_old_inline_comments(repo, pr_number, token)
        inline_count = await _post_inline_comments(
            repo, pr_number, head_sha, issues, token,
            min_severity=config.get("min_severity", "low"),
        )

        comment = format_review_comment(review, pr_number, "full")
        await upsert_pr_comment(repo, pr_number, comment, token)

        ended_at = datetime.now(timezone.utc)
        run_id = await log_review_run(
            repo=repo, pr_number=pr_number, pr_title=pr_title, action="mcp_tool",
            diff_size=len(diff), chunks_count=review.get("_chunks_count", 0),
            issues_found=len(issues), risk=review.get("risk", "unknown"),
            status="success", started_at=started_at, ended_at=ended_at,
            commit_sha=head_sha, review_mode="full",
        )

        logger.info(f"[mcp:review_pr] Done — run_id={run_id} issues={len(issues)} risk={review.get('risk')} inline={inline_count}")
        return ok({
            "run_id": run_id,
            "pr": f"{repo}#{pr_number}",
            "risk": review.get("risk"),
            "summary": review.get("summary"),
            "issues_found": len(issues),
            "inline_comments_posted": inline_count,
            "issues": issues,
            "checklist": review.get("checklist", []),
        })

    except Exception as e:
        logger.error(f"[mcp:review_pr] Failed: {e}", exc_info=True)
        return err(str(e))


# ─────────────────────────────────────────────────────────────────────────────
# TOOL 2: review_pr_incremental
#
# Reviews only the diff between two commits. Mirrors what
# handle_pull_request_event does on "synchronize" when a prior review SHA
# exists: get_commit_diff(base_sha, head_sha) instead of get_pr_diff.
# ─────────────────────────────────────────────────────────────────────────────
@mcp.tool()
async def review_pr_incremental(
    repo: str,
    pr_number: int,
    base_sha: str,
    head_sha: str,
    agents: Optional[list[str]] = None,
    max_issues: Optional[int] = None,
) -> dict:
    """
    Run an incremental review between two commit SHAs on a PR.

    Only the diff between base_sha and head_sha is reviewed. Use
    get_last_reviewed_sha to find the right base_sha for a PR.

    Args:
        repo:       Full repo name e.g. 'owner/repo'
        pr_number:  PR number (needed for posting the comment)
        base_sha:   Starting commit SHA (e.g. last reviewed SHA)
        head_sha:   Ending commit SHA (e.g. latest push)
        agents:     Optional agent override. Valid: security, code_quality, performance, tests.
        max_issues: Optional cap on issues returned.

    Returns:
        run_id, risk level, summary, issues list, checklist, inline_comments_posted.
    """
    logger.info(f"[mcp:review_pr_incremental] repo={repo} pr=#{pr_number} {base_sha[:8]}...{head_sha[:8]}")
    try:
        if (e := _validate_repo(repo)): return err(e)
        if agents:
            invalid = set(agents) - VALID_AGENTS
            if invalid:
                return err(f"Invalid agents: {invalid}. Valid values: {VALID_AGENTS}")

        token = await get_installation_token()
        pr_info = await get_pr_info(repo, pr_number, token)
        pr_title = pr_info.get("title", "untitled")

        config = await get_repo_config(repo)
        if agents:
            config = {**config, "agents": agents}
        if max_issues is not None:
            config = {**config, "max_issues": max_issues}

        diff = await get_commit_diff(repo, base_sha, head_sha, token)
        if not diff.strip():
            return ok({"summary": "Empty incremental diff — nothing to review.", "risk": "low", "issues": [], "checklist": []})

        started_at = datetime.now(timezone.utc)
        review = await review_diff(diff, pr_title, config=config)
        issues = review.get("issues", [])

        await _clear_old_inline_comments(repo, pr_number, token)
        inline_count = await _post_inline_comments(
            repo, pr_number, head_sha, issues, token,
            min_severity=config.get("min_severity", "low"),
        )

        comment = format_review_comment(review, pr_number, "incremental")
        await upsert_pr_comment(repo, pr_number, comment, token)

        ended_at = datetime.now(timezone.utc)
        run_id = await log_review_run(
            repo=repo, pr_number=pr_number, pr_title=pr_title, action="mcp_tool_incremental",
            diff_size=len(diff), chunks_count=review.get("_chunks_count", 0),
            issues_found=len(issues), risk=review.get("risk", "unknown"),
            status="success", started_at=started_at, ended_at=ended_at,
            commit_sha=head_sha, review_mode="incremental",
        )

        logger.info(f"[mcp:review_pr_incremental] Done — run_id={run_id} issues={len(issues)} inline={inline_count}")
        return ok({
            "run_id": run_id,
            "pr": f"{repo}#{pr_number}",
            "base_sha": base_sha,
            "head_sha": head_sha,
            "risk": review.get("risk"),
            "summary": review.get("summary"),
            "issues_found": len(issues),
            "inline_comments_posted": inline_count,
            "issues": issues,
            "checklist": review.get("checklist", []),
        })

    except Exception as e:
        logger.error(f"[mcp:review_pr_incremental] Failed: {e}", exc_info=True)
        return err(str(e))


# ─────────────────────────────────────────────────────────────────────────────
# TOOL 3: review_pr_targeted
#
# Single-focus review — mirrors the /agent review <mode> slash command from
# event_handler. SLASH_AGENT_MAP is reproduced faithfully here.
# ─────────────────────────────────────────────────────────────────────────────
@mcp.tool()
async def review_pr_targeted(
    repo: str,
    pr_number: int,
    focus: str,
    max_issues: Optional[int] = None,
) -> dict:
    """
    Run a targeted single-focus review on a PR (mirrors /agent review <mode>).

    Runs only the relevant agent for the chosen focus area on the full PR diff.

    Args:
        repo:       Full repo name e.g. 'owner/repo'
        pr_number:  PR number to review
        focus:      Focus area. Valid values:
                      'security'  → security agent
                      'perf'      → performance agent
                      'quality'   → code_quality agent
                      'tests'     → tests agent
        max_issues: Optional cap on issues returned.

    Returns:
        run_id, agents_used, risk level, summary, issues list, checklist.
    """
    logger.info(f"[mcp:review_pr_targeted] repo={repo} pr=#{pr_number} focus={focus}")
    try:
        if (e := _validate_repo(repo)): return err(e)
        agents = FOCUS_AGENT_MAP.get(focus.lower())
        if agents is None:
            return err(f"Invalid focus '{focus}'. Valid values: {list(FOCUS_AGENT_MAP.keys())}")

        token = await get_installation_token()
        pr_info = await get_pr_info(repo, pr_number, token)
        pr_title = pr_info.get("title", "untitled")
        head_sha = pr_info.get("head", {}).get("sha")

        config = await get_repo_config(repo)
        config = {**config, "agents": agents}
        if max_issues is not None:
            config = {**config, "max_issues": max_issues}

        diff = await get_pr_diff(repo, pr_number, token)
        if not diff.strip():
            return ok({"summary": "Empty diff — nothing to review.", "risk": "low", "issues": [], "checklist": []})

        started_at = datetime.now(timezone.utc)
        review = await review_diff(diff, pr_title, config=config)
        issues = review.get("issues", [])

        await _clear_old_inline_comments(repo, pr_number, token)
        inline_count = await _post_inline_comments(
            repo, pr_number, head_sha, issues, token,
            min_severity=config.get("min_severity", "low"),
        )

        comment = format_review_comment(review, pr_number, f"slash:{focus}")
        await upsert_pr_comment(repo, pr_number, comment, token)

        ended_at = datetime.now(timezone.utc)
        run_id = await log_review_run(
            repo=repo, pr_number=pr_number, pr_title=pr_title, action="mcp_tool_targeted",
            diff_size=len(diff), chunks_count=review.get("_chunks_count", 0),
            issues_found=len(issues), risk=review.get("risk", "unknown"),
            status="success", started_at=started_at, ended_at=ended_at,
            commit_sha=head_sha, review_mode=f"slash:{focus}",
        )

        logger.info(f"[mcp:review_pr_targeted] Done — focus={focus} run_id={run_id} issues={len(issues)}")
        return ok({
            "run_id": run_id,
            "pr": f"{repo}#{pr_number}",
            "focus": focus,
            "agents_used": agents,
            "risk": review.get("risk"),
            "summary": review.get("summary"),
            "issues_found": len(issues),
            "inline_comments_posted": inline_count,
            "issues": issues,
            "checklist": review.get("checklist", []),
        })

    except Exception as e:
        logger.error(f"[mcp:review_pr_targeted] Failed: {e}", exc_info=True)
        return err(str(e))


# ─────────────────────────────────────────────────────────────────────────────
# TOOL 4: get_pr_metadata
#
# Wraps github_client.get_pr_info and returns only the fields an MCP consumer
# needs — not the raw 300-field GitHub API blob. Includes stats fields
# (changed_files, additions, deletions) that the old tool was missing.
# ─────────────────────────────────────────────────────────────────────────────
@mcp.tool()
async def get_pr_metadata(repo: str, pr_number: int) -> dict:
    """
    Fetch metadata for a GitHub PR.

    Args:
        repo:      Full repo name e.g. 'owner/repo'
        pr_number: PR number

    Returns:
        title, state, draft, author, head/base SHAs, branches, labels,
        mergeable, commit count, changed_files, additions, deletions, URLs.
    """
    logger.info(f"[mcp:get_pr_metadata] repo={repo} pr=#{pr_number}")
    try:
        if (e := _validate_repo(repo)): return err(e)
        token = await get_installation_token()
        info = await get_pr_info(repo, pr_number, token)
        return ok({
            "number": info.get("number"),
            "title": info.get("title"),
            "state": info.get("state"),
            "draft": info.get("draft", False),
            "author": info.get("user", {}).get("login"),
            "head_sha": info.get("head", {}).get("sha"),
            "head_branch": info.get("head", {}).get("ref"),
            "base_sha": info.get("base", {}).get("sha"),
            "base_branch": info.get("base", {}).get("ref"),
            "labels": [lbl.get("name") for lbl in info.get("labels", [])],
            "mergeable": info.get("mergeable"),
            "commits": info.get("commits"),
            "changed_files": info.get("changed_files"),
            "additions": info.get("additions"),
            "deletions": info.get("deletions"),
            "created_at": info.get("created_at"),
            "updated_at": info.get("updated_at"),
            "html_url": info.get("html_url"),
        })
    except Exception as e:
        logger.error(f"[mcp:get_pr_metadata] Failed: {e}", exc_info=True)
        return err(str(e))


# ─────────────────────────────────────────────────────────────────────────────
# TOOL 5: get_pr_diff_tool
#
# Returns the full PR unified diff. Separate from review_pr so consumers can
# inspect the diff before deciding to trigger a review.
# ─────────────────────────────────────────────────────────────────────────────
@mcp.tool()
async def get_pr_diff_tool(repo: str, pr_number: int) -> dict:
    """
    Fetch the raw unified diff for a PR.

    Args:
        repo:      Full repo name e.g. 'owner/repo'
        pr_number: PR number

    Returns:
        Raw diff text and character count.
    """
    logger.info(f"[mcp:get_pr_diff] repo={repo} pr=#{pr_number}")
    try:
        if (e := _validate_repo(repo)): return err(e)
        token = await get_installation_token()
        diff = await get_pr_diff(repo, pr_number, token)
        return ok({
            "repo": repo,
            "pr_number": pr_number,
            "diff_size_chars": len(diff),
            "diff": diff,
        })
    except Exception as e:
        logger.error(f"[mcp:get_pr_diff] Failed: {e}", exc_info=True)
        return err(str(e))


# ─────────────────────────────────────────────────────────────────────────────
# TOOL 6: get_commit_diff_tool
#
# Returns the diff between two arbitrary SHAs via github_client.get_commit_diff.
# Useful for inspecting what changed before triggering an incremental review.
# ─────────────────────────────────────────────────────────────────────────────
@mcp.tool()
async def get_commit_diff_tool(repo: str, base_sha: str, head_sha: str) -> dict:
    """
    Fetch the diff between two commits in a repo.

    Args:
        repo:     Full repo name e.g. 'owner/repo'
        base_sha: Base commit SHA
        head_sha: Head commit SHA

    Returns:
        Raw diff text and character count.
    """
    logger.info(f"[mcp:get_commit_diff] repo={repo} {base_sha[:8]}...{head_sha[:8]}")
    try:
        if (e := _validate_repo(repo)): return err(e)
        token = await get_installation_token()
        diff = await get_commit_diff(repo, base_sha, head_sha, token)
        return ok({
            "repo": repo,
            "base_sha": base_sha,
            "head_sha": head_sha,
            "diff_size_chars": len(diff),
            "diff": diff,
        })
    except Exception as e:
        logger.error(f"[mcp:get_commit_diff] Failed: {e}", exc_info=True)
        return err(str(e))


# ─────────────────────────────────────────────────────────────────────────────
# TOOL 7: get_last_reviewed_sha_tool
#
# Wraps audit_logger.get_last_reviewed_sha — the same function event_handler
# calls on "synchronize" to decide full vs incremental review.
# ─────────────────────────────────────────────────────────────────────────────
@mcp.tool()
async def get_last_reviewed_sha_tool(repo: str, pr_number: int) -> dict:
    """
    Get the commit SHA of the last successful review for a PR.

    Use this to find the right base_sha before calling review_pr_incremental.

    Args:
        repo:      Full repo name e.g. 'owner/repo'
        pr_number: PR number

    Returns:
        last_reviewed_sha (null if no prior review) and has_prior_review flag.
    """
    logger.info(f"[mcp:get_last_reviewed_sha] repo={repo} pr=#{pr_number}")
    try:
        if (e := _validate_repo(repo)): return err(e)
        sha = await get_last_reviewed_sha(repo, pr_number)
        return ok({
            "repo": repo,
            "pr_number": pr_number,
            "last_reviewed_sha": sha,
            "has_prior_review": sha is not None,
        })
    except Exception as e:
        logger.error(f"[mcp:get_last_reviewed_sha] Failed: {e}", exc_info=True)
        return err(str(e))


# ─────────────────────────────────────────────────────────────────────────────
# TOOL 8: get_review_run
#
# Fetches a specific or most-recent review run from the audit log.
# Accepts optional run_id (ObjectId) to fetch a specific run.
# ─────────────────────────────────────────────────────────────────────────────
@mcp.tool()
async def get_review_run(repo: str, pr_number: int, run_id: Optional[str] = None) -> dict:
    """
    Fetch a review run record from the audit log.

    Args:
        repo:      Full repo name e.g. 'owner/repo'
        pr_number: PR number
        run_id:    Optional specific run ID (ObjectId string). If omitted,
                   returns the most recent run for this PR.

    Returns:
        Full review run record: risk, issues_found, duration_ms, status, error, etc.
    """
    logger.info(f"[mcp:get_review_run] repo={repo} pr=#{pr_number} run_id={run_id}")
    try:
        if (e := _validate_repo(repo)): return err(e)
        db = get_db()

        if run_id:
            from bson import ObjectId
            doc = await db["review_runs"].find_one({"_id": ObjectId(run_id)})
        else:
            doc = await db["review_runs"].find_one(
                {"repo": repo, "pr_number": pr_number},
                sort=[("ended_at", -1)],
            )

        if not doc:
            msg = f"Run {run_id} not found" if run_id else f"No review runs found for {repo}#{pr_number}"
            return ok({"found": False, "message": msg})

        return ok({
            "found": True,
            "run_id": str(doc["_id"]),
            "repo": doc.get("repo"),
            "pr_number": doc.get("pr_number"),
            "pr_title": doc.get("pr_title"),
            "action": doc.get("action"),
            "review_mode": doc.get("review_mode"),
            "status": doc.get("status"),
            "risk": doc.get("risk"),
            "issues_found": doc.get("issues_found"),
            "chunks_count": doc.get("chunks_count"),
            "diff_size_chars": doc.get("diff_size_chars"),
            "commit_sha": doc.get("commit_sha"),
            "duration_ms": doc.get("duration_ms"),
            "started_at": str(doc.get("started_at")),
            "ended_at": str(doc.get("ended_at")),
            "error": doc.get("error"),
        })

    except Exception as e:
        logger.error(f"[mcp:get_review_run] Failed: {e}", exc_info=True)
        return err(str(e))


# ─────────────────────────────────────────────────────────────────────────────
# TOOL 9: list_review_runs
#
# Lists audit log entries for a repo. Adds status and review_mode filters
# that the old tool was missing.
# ─────────────────────────────────────────────────────────────────────────────
@mcp.tool()
async def list_review_runs(
    repo: str,
    limit: int = 10,
    status: Optional[str] = None,
    review_mode: Optional[str] = None,
) -> dict:
    """
    List recent review runs for a repo from the audit log, newest first.

    Args:
        repo:        Full repo name e.g. 'owner/repo'
        limit:       Max runs to return (default 10, max 50)
        status:      Optional filter — 'success' or 'failed'
        review_mode: Optional filter — 'full', 'incremental', 'slash:security', etc.

    Returns:
        List of review run summaries.
    """
    logger.info(f"[mcp:list_review_runs] repo={repo} limit={limit} status={status} mode={review_mode}")
    try:
        if (e := _validate_repo(repo)): return err(e)
        if status and status not in {"success", "failed"}:
            return err("status must be 'success' or 'failed'")

        limit = min(limit, 50)
        db = get_db()

        query: dict = {"repo": repo}
        if status:
            query["status"] = status
        if review_mode:
            query["review_mode"] = review_mode

        cursor = db["review_runs"].find(query, sort=[("ended_at", -1)], limit=limit)
        runs = []
        async for doc in cursor:
            runs.append({
                "run_id": str(doc["_id"]),
                "pr_number": doc.get("pr_number"),
                "pr_title": doc.get("pr_title"),
                "action": doc.get("action"),
                "review_mode": doc.get("review_mode"),
                "status": doc.get("status"),
                "risk": doc.get("risk"),
                "issues_found": doc.get("issues_found"),
                "commit_sha": doc.get("commit_sha"),
                "duration_ms": doc.get("duration_ms"),
                "ended_at": str(doc.get("ended_at")),
            })

        logger.info(f"[mcp:list_review_runs] Returned {len(runs)} runs for {repo}")
        return ok({"repo": repo, "count": len(runs), "runs": runs})

    except Exception as e:
        logger.error(f"[mcp:list_review_runs] Failed: {e}", exc_info=True)
        return err(str(e))


# ─────────────────────────────────────────────────────────────────────────────
# TOOL 10: clear_inline_comments
#
# Deletes all bot-posted inline review comments from a PR.
# Delegates to event_handler._clear_old_inline_comments — same function
# called before every review run to avoid stale comments.
# ─────────────────────────────────────────────────────────────────────────────
@mcp.tool()
async def clear_inline_comments(repo: str, pr_number: int) -> dict:
    """
    Delete all bot-posted inline review comments from a PR.

    Useful for cleaning up before re-running a targeted review, or if
    inline comments are outdated after a rebase.

    Args:
        repo:      Full repo name e.g. 'owner/repo'
        pr_number: PR number

    Returns:
        Number of inline comments deleted.
    """
    logger.info(f"[mcp:clear_inline_comments] repo={repo} pr=#{pr_number}")
    try:
        if (e := _validate_repo(repo)): return err(e)
        token = await get_installation_token()
        existing = await get_bot_inline_comments(repo, pr_number, token)
        count_before = len(existing)
        await _clear_old_inline_comments(repo, pr_number, token)
        return ok({
            "repo": repo,
            "pr_number": pr_number,
            "deleted_count": count_before,
        })
    except Exception as e:
        logger.error(f"[mcp:clear_inline_comments] Failed: {e}", exc_info=True)
        return err(str(e))


# ─────────────────────────────────────────────────────────────────────────────
# TOOL 11: get_repo_config_tool
# ─────────────────────────────────────────────────────────────────────────────
@mcp.tool()
async def get_repo_config_tool(repo: str) -> dict:
    """
    Get the review configuration for a repo.

    Returns the stored config merged with system defaults. If no custom config
    exists, returns defaults: all 4 agents, max 10 issues, min_severity 'low'.

    Args:
        repo: Full repo name e.g. 'owner/repo'

    Returns:
        max_issues, agents, min_severity, ignore_patterns.
    """
    logger.info(f"[mcp:get_repo_config] repo={repo}")
    try:
        if (e := _validate_repo(repo)): return err(e)
        config = await get_repo_config(repo)
        return ok(config)
    except Exception as e:
        logger.error(f"[mcp:get_repo_config] Failed: {e}", exc_info=True)
        return err(str(e))


# ─────────────────────────────────────────────────────────────────────────────
# TOOL 12: set_repo_config_tool
#
# Validation matches repo_config.upsert_repo_config allowed keys exactly.
# ─────────────────────────────────────────────────────────────────────────────
@mcp.tool()
async def set_repo_config_tool(
    repo: str,
    max_issues: Optional[int] = None,
    agents: Optional[list[str]] = None,
    min_severity: Optional[str] = None,
    ignore_patterns: Optional[list[str]] = None,
) -> dict:
    """
    Update the review configuration for a repo (partial update — only provided fields change).

    Args:
        repo:            Full repo name e.g. 'owner/repo'
        max_issues:      Max issues to report per run (must be >= 1)
        agents:          Agents to enable. Valid: security, code_quality, performance, tests.
        min_severity:    Minimum severity to report. Valid: 'low', 'medium', 'high'.
        ignore_patterns: File glob patterns to skip e.g. ['migrations/', '*.test.js']

    Returns:
        Full updated config after save.
    """
    logger.info(f"[mcp:set_repo_config] repo={repo} agents={agents} max_issues={max_issues} min_severity={min_severity}")
    try:
        if (e := _validate_repo(repo)): return err(e)
        updates: dict = {}

        if max_issues is not None:
            if max_issues < 1:
                return err("max_issues must be >= 1")
            updates["max_issues"] = max_issues

        if agents is not None:
            invalid = set(agents) - VALID_AGENTS
            if invalid:
                return err(f"Invalid agents: {invalid}. Valid values: {VALID_AGENTS}")
            if not agents:
                return err("agents list cannot be empty")
            updates["agents"] = agents

        if min_severity is not None:
            if min_severity not in {"low", "medium", "high"}:
                return err("min_severity must be 'low', 'medium', or 'high'")
            updates["min_severity"] = min_severity

        if ignore_patterns is not None:
            updates["ignore_patterns"] = ignore_patterns

        if not updates:
            return err("No fields provided — pass at least one of: max_issues, agents, min_severity, ignore_patterns")

        config = await upsert_repo_config(repo, updates)
        return ok(config)

    except Exception as e:
        logger.error(f"[mcp:set_repo_config] Failed: {e}", exc_info=True)
        return err(str(e))


# ─────────────────────────────────────────────────────────────────────────────
# TOOL 13: reset_repo_config_tool
# ─────────────────────────────────────────────────────────────────────────────
@mcp.tool()
async def reset_repo_config_tool(repo: str) -> dict:
    """
    Reset a repo's review config back to system defaults.

    Deletes any stored custom config. The next review will use defaults:
    all 4 agents, max 10 issues, min_severity 'low', no ignore_patterns.

    Args:
        repo: Full repo name e.g. 'owner/repo'

    Returns:
        had_custom_config flag and confirmation message.
    """
    logger.info(f"[mcp:reset_repo_config] repo={repo}")
    try:
        if (e := _validate_repo(repo)): return err(e)
        deleted = await delete_repo_config(repo)
        return ok({
            "repo": repo,
            "had_custom_config": deleted,
            "message": "Config reset to defaults." if deleted else "No custom config found — already using defaults.",
        })
    except Exception as e:
        logger.error(f"[mcp:reset_repo_config] Failed: {e}", exc_info=True)
        return err(str(e))


# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import os
    host = os.getenv("MCP_HOST", "0.0.0.0")
    port = int(os.getenv("MCP_PORT", "8001"))
    logger.info(f"[mcp_server] Starting PR Reviewer MCP server on {host}:{port} (streamable-http)")
    mcp.run(transport="streamable-http", host=host, port=port)