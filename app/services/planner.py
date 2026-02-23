import logging
from typing import List

logger = logging.getLogger(__name__)

SECURITY_EXTENSIONS = {".py", ".js", ".ts", ".go", ".java", ".rb", ".php", ".env", ".yaml", ".yml", ".json", ".toml"}
PERFORMANCE_EXTENSIONS = {".py", ".js", ".ts", ".go", ".java", ".rb", ".sql"}
TESTS_EXTENSIONS = {".py", ".js", ".ts", ".go", ".java", ".rb"}
TEST_FILE_PATTERNS = ["test_", "_test", "/tests/", "/test/", "spec."]


def _get_extension(filepath: str) -> str:
    if "." in filepath:
        return "." + filepath.rsplit(".", 1)[-1].lower()
    return ""


def _is_test_file(filepath: str) -> bool:
    return any(p in filepath for p in TEST_FILE_PATTERNS)


def plan_agents(chunks: List[dict], active_agents: list = None) -> dict:
    all_agents = ["security", "code_quality", "performance", "tests"]
    enabled = set(active_agents) if active_agents else set(all_agents)
    plan = {a: [] for a in all_agents}

    for chunk in chunks:
        filepath = chunk.get("file", "")
        ext = _get_extension(filepath)
        is_test = _is_test_file(filepath)

        if (ext in SECURITY_EXTENSIONS or ext in PERFORMANCE_EXTENSIONS) and "code_quality" in enabled:
            plan["code_quality"].append(chunk)
        if ext in SECURITY_EXTENSIONS and "security" in enabled:
            plan["security"].append(chunk)
        if ext in PERFORMANCE_EXTENSIONS and not is_test and "performance" in enabled:
            plan["performance"].append(chunk)
        if ext in TESTS_EXTENSIONS and not is_test and "tests" in enabled:
            plan["tests"].append(chunk)

    for agent, agent_chunks in plan.items():
        if agent_chunks:
            files = list({c["file"] for c in agent_chunks})
            logger.info(f"[planner] {agent} → {len(agent_chunks)} chunks across {files}")
        else:
            logger.info(f"[planner] {agent} → skipped (no relevant files)")

    return plan