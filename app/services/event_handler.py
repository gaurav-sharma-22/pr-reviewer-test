import logging

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

    logger.info(
        f"[pull_request] action={action} | repo={repo_name} | "
        f"pr=#{pr_number} | title='{pr_title}'"
    )

    if action not in HANDLED_PR_ACTIONS:
        logger.info(f"[pull_request] Ignoring action '{action}' — not in handled set {HANDLED_PR_ACTIONS}")
        return

    logger.info(f"[pull_request] ✅ Accepted — proceeding to fetch diff & review for PR #{pr_number}")

    # TODO: fetch diff, run review, post comment