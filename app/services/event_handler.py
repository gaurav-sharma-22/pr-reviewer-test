from typing import Optional
import logging
import asyncio
from datetime import datetime, timezone
from app.services.github_client import (
    get_installation_token, get_pr_diff, get_commit_diff, upsert_pr_comment,
    post_inline_comment, get_bot_inline_comments, delete_inline_comment,
    get_pr_info, add_reaction,
)
from app.services.review_agent import review_diff, format_review_comment, format_inline_comment
from app.services.audit_logger import log_review_run, get_last_reviewed_sha
from app.services.repo_config import get_repo_config

logger = logging.getLogger(__name__)

HANDLED_PR_ACTIONS = {"opened", "synchronize", "reopened"}
INLINE_SEVERITIES = {"high", "medium"}

# Slash command → agent filter mapping
SLASH_AGENT_MAP = {
    "security":    ["security"],
    "perf":        ["performance"],
    "performance": ["performance"],
    "quality":     ["code_quality"],
    "tests":       ["tests"],
    "test":        ["tests"],
}


def _parse_slash_command(body: str):
    """
    Parse /agent review [mode] from comment body.
    Returns (mode_label, agent_list) or None if not a command.
    """
    body = body.strip()
    if not body.startswith("/agent review"):
        return None
    parts = body.split()
    # /agent review           → full, all agents
    # /agent review security  → security only
    if len(parts) == 2:
        return "full", None  # None = use repo config defaults
    mode = parts[2].lower()
    agents = SLASH_AGENT_MAP.get(mode)
    if agents is None:
        logger.warning(f"[slash_command] Unknown mode '{mode}' — running full review")
        return "full", None
    return mode, agents


async def _clear_old_inline_comments(repo: str, pr_number: int, token: str) -> None:
    existing = await get_bot_inline_comments(repo, pr_number, token)
    if not existing:
        return
    await asyncio.gather(*[delete_inline_comment(repo, c["id"], token) for c in existing])
    logger.info(f"[event_handler] Cleared {len(existing)} old inline comments")


async def _post_inline_comments(
    repo: str, pr_number: int, commit_sha: str, issues: list,
    token: str, min_severity: str = "low"
) -> int:
    sev_order = {"low": 0, "medium": 1, "high": 2}
    min_sev_val = sev_order.get(min_severity, 0)
    inline_issues = [
        i for i in issues
        if i.get("severity") in INLINE_SEVERITIES
        and sev_order.get(i.get("severity", "low"), 0) >= min_sev_val
        and i.get("line", 0) > 0
    ]
    if not inline_issues:
        logger.info("[event_handler] No inline-eligible issues")
        return 0

    async def _post_one(issue):
        try:
            return await post_inline_comment(
                repo=repo, pr_number=pr_number, commit_id=commit_sha,
                path=issue["file"], line=issue["line"],
                body=format_inline_comment(issue), token=token,
            )
        except Exception as e:
            logger.error(f"[event_handler] Inline comment failed: {e}")
            return None

    results = await asyncio.gather(*[_post_one(i) for i in inline_issues])
    posted = sum(1 for r in results if r is not None)
    logger.info(f"[event_handler] Posted {posted}/{len(inline_issues)} inline comments")
    return posted


async def _run_review(
    repo_name: str, pr_number: int, pr_title: str, head_sha: str,
    diff: str, token: str, config: dict, review_mode: str, action: str,
) -> None:
    """Core review logic shared by PR events and slash commands."""
    started_at = datetime.now(timezone.utc)
    diff_size = len(diff)
    chunks_count = 0
    issues_found = 0
    risk = "unknown"

    try:
        review = await review_diff(diff, pr_title, config=config)
        chunks_count = review.get("_chunks_count", 1)
        issues = review.get("issues", [])
        issues_found = len(issues)
        risk = review.get("risk", "unknown")

        await _clear_old_inline_comments(repo_name, pr_number, token)
        inline_count = await _post_inline_comments(
            repo_name, pr_number, head_sha, issues, token,
            min_severity=config.get("min_severity", "low"),
        )
        logger.info(f"[event_handler] Inline comments posted: {inline_count}")

        comment = format_review_comment(review, pr_number, review_mode)
        await upsert_pr_comment(repo_name, pr_number, comment, token)
        logger.info(f"[event_handler] ✅ Review complete on PR #{pr_number}")

        ended_at = datetime.now(timezone.utc)
        await log_review_run(
            repo=repo_name, pr_number=pr_number, pr_title=pr_title, action=action,
            diff_size=diff_size, chunks_count=chunks_count, issues_found=issues_found,
            risk=risk, status="success", started_at=started_at, ended_at=ended_at,
            commit_sha=head_sha, review_mode=review_mode,
        )

    except Exception as e:
        ended_at = datetime.now(timezone.utc)
        logger.error(f"[event_handler] ❌ Review failed for PR #{pr_number}: {e}", exc_info=True)
        await log_review_run(
            repo=repo_name, pr_number=pr_number, pr_title=pr_title, action=action,
            diff_size=diff_size, chunks_count=chunks_count, issues_found=issues_found,
            risk=risk, status="failed", started_at=started_at, ended_at=ended_at,
            commit_sha=head_sha, review_mode=review_mode, error=str(e),
        )
        raise


async def handle_github_event(event_type: str, payload: dict) -> None:
    logger.info(f"[event_handler] Dispatching event type: '{event_type}'")
    if event_type == "pull_request":
        await handle_pull_request_event(payload)
    elif event_type == "issue_comment":
        await handle_issue_comment_event(payload)
    else:
        logger.info(f"[event_handler] No handler for event type: '{event_type}' — skipping")


async def handle_pull_request_event(payload: dict) -> None:
    action = payload.get("action")
    pr = payload.get("pull_request", {})
    repo = payload.get("repository", {})

    pr_number = pr.get("number")
    pr_title = pr.get("title", "untitled")
    repo_name = repo.get("full_name", "unknown/repo")
    head_sha = pr.get("head", {}).get("sha")

    logger.info(
        f"[pull_request] action={action} | repo={repo_name} | "
        f"pr=#{pr_number} | sha={head_sha[:8] if head_sha else 'unknown'}"
    )

    if action not in HANDLED_PR_ACTIONS:
        logger.info(f"[pull_request] Ignoring action '{action}'")
        return

    logger.info(f"[pull_request] ✅ Accepted — starting review for PR #{pr_number}")

    try:
        token = await get_installation_token()
        config = await get_repo_config(repo_name)
        logger.info(
            f"[pull_request] Config: max_issues={config['max_issues']} "
            f"agents={config['agents']} min_severity={config['min_severity']}"
        )

        review_mode = "full"
        if action == "synchronize" and head_sha:
            last_sha = await get_last_reviewed_sha(repo_name, pr_number)
            if last_sha and last_sha != head_sha:
                logger.info(f"[pull_request] Incremental review: {last_sha[:8]}...{head_sha[:8]}")
                diff = await get_commit_diff(repo_name, last_sha, head_sha, token)
                review_mode = "incremental"
            else:
                logger.info("[pull_request] No prior review found — doing full review")
                diff = await get_pr_diff(repo_name, pr_number, token)
        else:
            diff = await get_pr_diff(repo_name, pr_number, token)

        logger.info(f"[pull_request] Diff fetched — {len(diff)} chars (mode={review_mode})")

        if not diff.strip():
            logger.info(f"[pull_request] Empty diff — skipping review for PR #{pr_number}")
            return

        await _run_review(
            repo_name=repo_name, pr_number=pr_number, pr_title=pr_title,
            head_sha=head_sha, diff=diff, token=token, config=config,
            review_mode=review_mode, action=action,
        )

    except Exception as e:
        logger.error(f"[pull_request] ❌ Failed: {e}", exc_info=True)


async def handle_issue_comment_event(payload: dict) -> None:
    """Handle /agent review slash commands posted as PR comments."""
    action = payload.get("action")
    if action != "created":
        return

    # Only handle comments on PRs (not plain issues)
    if "pull_request" not in payload.get("issue", {}):
        logger.info("[slash_command] Comment is on an issue, not a PR — skipping")
        return

    comment = payload.get("comment", {})
    comment_body = comment.get("body", "").strip()
    comment_id = comment.get("id")
    issue = payload.get("issue", {})
    repo = payload.get("repository", {})

    pr_number = issue.get("number")
    repo_name = repo.get("full_name", "unknown/repo")

    # Parse the slash command
    result = _parse_slash_command(comment_body)
    if result is None:
        logger.info(f"[slash_command] Not a /agent command — skipping")
        return

    mode_label, forced_agents = result
    logger.info(f"[slash_command] ✅ Command '/agent review {mode_label}' on {repo_name}#{pr_number}")

    try:
        token = await get_installation_token()

        # React with 👀 to signal we're starting
        await add_reaction(repo_name, comment_id, "eyes", token)

        # Fetch PR info to get head SHA and title
        pr_info = await get_pr_info(repo_name, pr_number, token)
        pr_title = pr_info.get("title", "untitled")
        head_sha = pr_info.get("head", {}).get("sha")

        # Load config, override agents if command specifies
        config = await get_repo_config(repo_name)
        if forced_agents:
            config = {**config, "agents": forced_agents}
            logger.info(f"[slash_command] Overriding agents to: {forced_agents}")

        logger.info(
            f"[slash_command] Config: max_issues={config['max_issues']} "
            f"agents={config['agents']} min_severity={config['min_severity']}"
        )

        # Always full diff for slash commands
        diff = await get_pr_diff(repo_name, pr_number, token)
        logger.info(f"[slash_command] Diff fetched — {len(diff)} chars")

        if not diff.strip():
            logger.info(f"[slash_command] Empty diff — skipping")
            return

        await _run_review(
            repo_name=repo_name, pr_number=pr_number, pr_title=pr_title,
            head_sha=head_sha, diff=diff, token=token, config=config,
            review_mode=f"slash:{mode_label}", action="slash_command",
        )

        # React with rocket to signal completion
        await add_reaction(repo_name, comment_id, "rocket", token)

    except Exception as e:
        logger.error(f"[slash_command] ❌ Failed: {e}", exc_info=True)
        # React with confused to signal failure
        try:
            await add_reaction(repo_name, comment_id, "confused", token)
        except Exception:
            pass