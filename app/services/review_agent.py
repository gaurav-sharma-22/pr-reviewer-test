import os
import json
import logging
import asyncio
from groq import AsyncGroq
from typing import List

from app.services.agents.security import SECURITY_SYSTEM_PROMPT
from app.services.agents.code_quality import CODE_QUALITY_SYSTEM_PROMPT
from app.services.agents.performance import PERFORMANCE_SYSTEM_PROMPT
from app.services.agents.tests import TESTS_SYSTEM_PROMPT
from app.services.planner import plan_agents

logger = logging.getLogger(__name__)

BOT_COMMENT_MARKER = "<!-- agentic-pr-reviewer -->"
MAX_TOTAL_ISSUES = 10

AGENT_PROMPTS = {
    "security": SECURITY_SYSTEM_PROMPT,
    "code_quality": CODE_QUALITY_SYSTEM_PROMPT,
    "performance": PERFORMANCE_SYSTEM_PROMPT,
    "tests": TESTS_SYSTEM_PROMPT,
}


async def _run_agent(client: AsyncGroq, agent_name: str, chunk: dict, pr_title: str) -> dict:
    system_prompt = AGENT_PROMPTS[agent_name]
    user_message = f"PR Title: {pr_title}\nFile: {chunk['file']}\n\nDiff:\n{chunk['content']}"

    try:
        response = await client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            temperature=0.2,
            max_tokens=1500,
        )
        raw = response.choices[0].message.content.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        raw = raw.strip()
        result = json.loads(raw)
        logger.info(f"[review_agent] {agent_name} on {chunk['file']} → {len(result.get('issues', []))} issues")
        return result
    except Exception as e:
        logger.error(f"[review_agent] {agent_name} failed on {chunk['chunk_id']}: {e}")
        return {"agent": agent_name, "issues": []}


def _aggregate(all_results: List[dict], chunks_count: int) -> dict:
    all_issues = []
    risk_order = {"low": 0, "medium": 1, "high": 2}
    max_risk = "low"

    for result in all_results:
        for issue in result.get("issues", []):
            all_issues.append(issue)
            risk = issue.get("severity", "low")
            if risk_order.get(risk, 0) > risk_order.get(max_risk, 0):
                max_risk = risk

    seen = set()
    deduped = []
    for issue in all_issues:
        key = (issue.get("title", ""), issue.get("file", ""))
        if key not in seen:
            seen.add(key)
            deduped.append(issue)

    severity_order = {"high": 0, "medium": 1, "low": 2}
    deduped.sort(key=lambda x: severity_order.get(x.get("severity", "low"), 2))
    deduped = deduped[:MAX_TOTAL_ISSUES]

    checklist = [f"Fix: {i.get('title', '')}" for i in deduped if i.get("severity") in {"high", "medium"}]

    agent_counts = {}
    for r in all_results:
        agent = r.get("agent", "unknown")
        agent_counts[agent] = agent_counts.get(agent, 0) + len(r.get("issues", []))

    summary_parts = []
    if agent_counts.get("security", 0): summary_parts.append(f"{agent_counts['security']} security issue(s)")
    if agent_counts.get("code_quality", 0): summary_parts.append(f"{agent_counts['code_quality']} code quality issue(s)")
    if agent_counts.get("performance", 0): summary_parts.append(f"{agent_counts['performance']} performance issue(s)")
    if agent_counts.get("tests", 0): summary_parts.append(f"{agent_counts['tests']} test coverage gap(s)")
    summary = ("Found " + ", ".join(summary_parts) + ".") if summary_parts else "No significant issues found."

    return {
        "summary": summary,
        "risk": max_risk,
        "issues": deduped,
        "checklist": checklist,
        "_chunks_count": chunks_count,
    }


async def review_diff(diff: str, pr_title: str) -> dict:
    from app.services.chunker import chunk_diff

    client = AsyncGroq(api_key=os.getenv("GROQ_API_KEY"))
    chunks = chunk_diff(diff)

    if not chunks:
        return {"summary": "No reviewable code changes found.", "risk": "low", "issues": [], "checklist": [], "_chunks_count": 0}

    plan = plan_agents(chunks)

    tasks = [(agent_name, chunk) for agent_name, agent_chunks in plan.items() for chunk in agent_chunks]

    if not tasks:
        return {"summary": "No reviewable code changes found.", "risk": "low", "issues": [], "checklist": [], "_chunks_count": len(chunks)}

    logger.info(f"[review_agent] Running {len(tasks)} agent-chunk tasks in parallel")

    results = await asyncio.gather(
        *[_run_agent(client, agent_name, chunk, pr_title) for agent_name, chunk in tasks]
    )

    final = _aggregate(list(results), len(chunks))
    logger.info(f"[review_agent] Final: {len(final['issues'])} issues, risk={final['risk']}")
    return final


def format_review_comment(review: dict, pr_number: int) -> str:
    risk_emoji = {"low": "🟢", "medium": "🟡", "high": "🔴"}.get(review.get("risk", "unknown"), "⚪")
    severity_emoji = {"low": "🔵", "medium": "🟡", "high": "🔴"}
    category_emoji = {"security": "🔐", "bug": "🐛", "maintainability": "🔧", "performance": "⚡", "tests": "🧪"}

    lines = [
        BOT_COMMENT_MARKER,
        f"## 🤖 Agentic PR Reviewer — PR #{pr_number}",
        f"",
        f"{risk_emoji} **Risk:** {review.get('risk', 'unknown').upper()}",
        f"",
        f"**Summary:** {review.get('summary', 'N/A')}",
        f"",
    ]

    issues = review.get("issues", [])
    if issues:
        lines.append(f"### 🔍 Issues Found ({len(issues)})")
        lines.append("")
        for i, issue in enumerate(issues, 1):
            sev = issue.get("severity", "low")
            cat = issue.get("category", "general")
            lines.append(f"**{i}. {severity_emoji.get(sev, '🔵')} [{sev.upper()}] {category_emoji.get(cat, '📌')} {issue.get('title', 'Issue')}**")
            lines.append(f"- **File:** `{issue.get('file', 'unknown')}`" + (f" (line {issue.get('line')})" if issue.get('line') else ""))
            lines.append(f"- **Category:** {cat}")
            lines.append(f"- **What:** {issue.get('explanation', '')}")
            lines.append(f"- **Fix:** {issue.get('suggestion', '')}")
            lines.append("")
    else:
        lines.append("### ✅ No issues found")
        lines.append("")

    checklist = review.get("checklist", [])
    if checklist:
        lines.append("### 📋 Action Checklist")
        lines.append("")
        for item in checklist:
            lines.append(f"- [ ] {item}")
        lines.append("")

    lines.append("---")
    lines.append("_Powered by Agentic PR Reviewer · Security · Code Quality · Performance · Tests_")
    return "\n".join(lines)