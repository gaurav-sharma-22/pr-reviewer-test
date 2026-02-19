import time
import jwt
import httpx
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

BOT_COMMENT_MARKER = "<!-- agentic-pr-reviewer -->"


def _load_private_key() -> str:
    path = os.getenv("GITHUB_PRIVATE_KEY_PATH", "github-app.pem")
    with open(path, "r") as f:
        return f.read()


def _generate_jwt() -> str:
    private_key = _load_private_key()
    app_id = os.getenv("GITHUB_APP_ID")
    now = int(time.time())
    payload = {
        "iat": now - 60,
        "exp": now + (10 * 60),
        "iss": app_id,
    }
    return jwt.encode(payload, private_key, algorithm="RS256")


async def get_installation_token() -> str:
    installation_id = os.getenv("GITHUB_INSTALLATION_ID")
    app_jwt = _generate_jwt()
    url = f"https://api.github.com/app/installations/{installation_id}/access_tokens"

    async with httpx.AsyncClient() as client:
        response = await client.post(
            url,
            headers={
                "Authorization": f"Bearer {app_jwt}",
                "Accept": "application/vnd.github+json",
            },
        )
        response.raise_for_status()
        token = response.json()["token"]
        logger.info(f"[github_client] Got installation token for installation {installation_id}")
        return token


async def get_pr_diff(repo: str, pr_number: int, token: str) -> str:
    url = f"https://api.github.com/repos/{repo}/pulls/{pr_number}"

    async with httpx.AsyncClient() as client:
        response = await client.get(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github.diff",
            },
        )
        response.raise_for_status()
        logger.info(f"[github_client] Fetched diff for {repo}#{pr_number} ({len(response.text)} chars)")
        return response.text


async def find_bot_comment(repo: str, pr_number: int, token: str) -> Optional[int]:
    url = f"https://api.github.com/repos/{repo}/issues/{pr_number}/comments"

    async with httpx.AsyncClient() as client:
        response = await client.get(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
            },
        )
        response.raise_for_status()
        comments = response.json()

    for comment in comments:
        if BOT_COMMENT_MARKER in comment.get("body", ""):
            logger.info(f"[github_client] Found existing bot comment {comment['id']} on {repo}#{pr_number}")
            return comment["id"]

    return None


async def post_pr_comment(repo: str, pr_number: int, body: str, token: str) -> None:
    url = f"https://api.github.com/repos/{repo}/issues/{pr_number}/comments"

    async with httpx.AsyncClient() as client:
        response = await client.post(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
            },
            json={"body": body},
        )
        response.raise_for_status()
        logger.info(f"[github_client] Posted new comment on {repo}#{pr_number}")


async def update_pr_comment(repo: str, comment_id: int, body: str, token: str) -> None:
    url = f"https://api.github.com/repos/{repo}/issues/comments/{comment_id}"

    async with httpx.AsyncClient() as client:
        response = await client.patch(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
            },
            json={"body": body},
        )
        response.raise_for_status()
        logger.info(f"[github_client] Updated existing comment {comment_id} on {repo}")


async def get_commit_diff(repo: str, base_sha: str, head_sha: str, token: str) -> str:
    """Fetch diff between two commits (incremental review)."""
    url = f"https://api.github.com/repos/{repo}/compare/{base_sha}...{head_sha}"

    async with httpx.AsyncClient() as client:
        response = await client.get(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github.diff",
            },
        )
        response.raise_for_status()
        logger.info(
            f"[github_client] Fetched incremental diff {base_sha[:8]}...{head_sha[:8]} "
            f"for {repo} ({len(response.text)} chars)"
        )
        return response.text


async def upsert_pr_comment(repo: str, pr_number: int, body: str, token: str) -> None:
    existing_comment_id = await find_bot_comment(repo, pr_number, token)
    if existing_comment_id:
        await update_pr_comment(repo, existing_comment_id, body, token)
    else:
        await post_pr_comment(repo, pr_number, body, token)