import logging

logger = logging.getLogger(__name__)


async def handle_github_event(event_type: str, payload: dict):
    logger.info(f"Handling GitHub event: {event_type}")

    if event_type == "pull_request":
        await handle_pull_request_event(payload)
    else:
        logger.info(f"Ignoring unsupported event: {event_type}")


async def handle_pull_request_event(payload: dict):
    action = payload.get("action")
    pr = payload.get("pull_request", {})
    repo = payload.get("repository", {})

    pr_number = pr.get("number")
    repo_name = repo.get("full_name")

    logger.info(
        f"PR EVENT → action={action}, repo={repo_name}, pr_number={pr_number}"
    )

    # Only act on meaningful PR lifecycle events
    if action not in {"opened", "synchronize", "reopened"}:
        logger.info("PR action ignored")
        return

    logger.info("✅ PR event accepted (next: fetch diff & review)")