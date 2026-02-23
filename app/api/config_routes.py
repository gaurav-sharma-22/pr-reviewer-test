from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from app.services.repo_config import get_repo_config, upsert_repo_config, delete_repo_config

router = APIRouter()


class RepoConfigUpdate(BaseModel):
    max_issues: Optional[int] = None
    agents: Optional[List[str]] = None
    min_severity: Optional[str] = None
    ignore_patterns: Optional[List[str]] = None


@router.get("/{owner}/{repo_name}")
async def get_config(owner: str, repo_name: str):
    repo = f"{owner}/{repo_name}"
    return await get_repo_config(repo)


@router.post("/{owner}/{repo_name}")
async def set_config(owner: str, repo_name: str, body: RepoConfigUpdate):
    repo = f"{owner}/{repo_name}"
    updates = {k: v for k, v in body.dict().items() if v is not None}
    if not updates:
        raise HTTPException(status_code=400, detail="No valid fields provided")
    if "agents" in updates:
        valid = {"security", "code_quality", "performance", "tests"}
        invalid = set(updates["agents"]) - valid
        if invalid:
            raise HTTPException(status_code=400, detail=f"Invalid agents: {invalid}")
    if "min_severity" in updates and updates["min_severity"] not in {"low", "medium", "high"}:
        raise HTTPException(status_code=400, detail="min_severity must be low, medium, or high")
    return await upsert_repo_config(repo, updates)


@router.delete("/{owner}/{repo_name}")
async def reset_config(owner: str, repo_name: str):
    repo = f"{owner}/{repo_name}"
    deleted = await delete_repo_config(repo)
    return {"repo": repo, "reset": deleted}