import logging
from datetime import datetime, timezone
from app.services.github_client import (
    get_installation_token, get_pr_diff, get_commit_diff, upsert_pr_comment
)
from app.services.review_agent import review_diff, format_review_comment
from app.services.audit_logger import log_review_run, get_last_reviewed_sha

logger = logging.getLogger(__name__)

HANDLED_PR_ACTIONS = {"opened", "synchronize", "reopened"}


async def handle_github_event(event_type: str, payload: dict) -> None:
    logger.info(f"[event_handler] Dispatching event type: '{event_type}'")
    if event_type == "pull_request":
        await handle_pull_request_event(payload)
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

    started_at = datetime.now(timezone.utc)
    diff_size = 0
    chunks_count = 0
    issues_found = 0
    risk = "unknown"
    review_mode = "full"

    try:
        token = await get_installation_token()

        # Decide: full review or incremental?
        if action == "synchronize" and head_sha:
            last_sha = await get_last_reviewed_sha(repo_name, pr_number)
            if last_sha and last_sha != head_sha:
                logger.info(
                    f"[pull_request] Incremental review: {last_sha[:8]}...{head_sha[:8]}"
                )
                diff = await get_commit_diff(repo_name, last_sha, head_sha, token)
                review_mode = "incremental"
            else:
                logger.info("[pull_request] No prior review found — doing full review")
                diff = await get_pr_diff(repo_name, pr_number, token)
        else:
            # opened / reopened → always full review
            diff = await get_pr_diff(repo_name, pr_number, token)

        diff_size = len(diff)
        logger.info(f"[pull_request] Diff fetched — {diff_size} chars (mode={review_mode})")

        if not diff.strip():
            logger.info(f"[pull_request] Empty diff — skipping review for PR #{pr_number}")
            return

        review = await review_diff(diff, pr_title)
        chunks_count = review.get("_chunks_count", 1)
        issues_found = len(review.get("issues", []))
        risk = review.get("risk", "unknown")

        comment = format_review_comment(review, pr_number, review_mode)
        await upsert_pr_comment(repo_name, pr_number, comment, token)
        logger.info(f"[pull_request] ✅ Review upserted on PR #{pr_number}")

        ended_at = datetime.now(timezone.utc)
        await log_review_run(
            repo=repo_name, pr_number=pr_number, pr_title=pr_title, action=action,
            diff_size=diff_size, chunks_count=chunks_count, issues_found=issues_found,
            risk=risk, status="success", started_at=started_at, ended_at=ended_at,
            commit_sha=head_sha, review_mode=review_mode,
        )

    except Exception as e:
        ended_at = datetime.now(timezone.utc)
        logger.error(f"[pull_request] ❌ Failed to process PR #{pr_number}: {e}", exc_info=True)
        await log_review_run(
            repo=repo_name, pr_number=pr_number, pr_title=pr_title, action=action,
            diff_size=diff_size, chunks_count=chunks_count, issues_found=issues_found,
            risk=risk, status="failed", started_at=started_at, ended_at=ended_at,
            commit_sha=head_sha, review_mode=review_mode, error=str(e),
        )