import logging
from typing import List

logger = logging.getLogger(__name__)

# File extensions that warrant each agent
SECURITY_EXTENSIONS = {".py", ".js", ".ts", ".go", ".java", ".rb", ".php", ".env", ".yaml", ".yml", ".json", ".toml"}
PERFORMANCE_EXTENSIONS = {".py", ".js", ".ts", ".go", ".java", ".rb", ".sql"}
TESTS_EXTENSIONS = {".py", ".js", ".ts", ".go", ".java", ".rb"}

# Paths that suggest test files — skip tests agent for these (they ARE the tests)
TEST_FILE_PATTERNS = ["test_", "_test", "/tests/", "/test/", "spec."]


def _get_extension(filepath: str) -> str:
    if "." in filepath:
        return "." + filepath.rsplit(".", 1)[-1].lower()
    return ""


def _is_test_file(filepath: str) -> bool:
    return any(p in filepath for p in TEST_FILE_PATTERNS)


def plan_agents(chunks: List[dict]) -> dict:
    """
    Decide which agents to run based on file types in the chunks.

    Returns:
    {
        "security": [chunk, ...],
        "code_quality": [chunk, ...],
        "performance": [chunk, ...],
        "tests": [chunk, ...]
    }
    """
    plan = {
        "security": [],
        "code_quality": [],
        "performance": [],
        "tests": [],
    }

    for chunk in chunks:
        filepath = chunk.get("file", "")
        ext = _get_extension(filepath)
        is_test = _is_test_file(filepath)

        # Code quality runs on everything that's not a config/data file
        if ext in SECURITY_EXTENSIONS or ext in PERFORMANCE_EXTENSIONS:
            plan["code_quality"].append(chunk)

        # Security on all code files
        if ext in SECURITY_EXTENSIONS:
            plan["security"].append(chunk)

        # Performance on compute-heavy languages
        if ext in PERFORMANCE_EXTENSIONS and not is_test:
            plan["performance"].append(chunk)

        # Tests agent only on non-test source files
        if ext in TESTS_EXTENSIONS and not is_test:
            plan["tests"].append(chunk)

    # Log the plan
    for agent, agent_chunks in plan.items():
        if agent_chunks:
            files = list({c["file"] for c in agent_chunks})
            logger.info(f"[planner] {agent} → {len(agent_chunks)} chunks across {files}")
        else:
            logger.info(f"[planner] {agent} → skipped (no relevant files)")

    return plan