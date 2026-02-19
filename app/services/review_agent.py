import os
import json
import logging
from groq import AsyncGroq
from typing import List

logger = logging.getLogger(__name__)

BOT_COMMENT_MARKER = "<!-- agentic-pr-reviewer -->"

SYSTEM_PROMPT = """You are an expert code reviewer. Analyze the given PR diff chunk and return a JSON review.

Rules:
- Output ONLY valid JSON, no markdown, no explanation outside the JSON
- Be concise and actionable
- Only flag real issues, not style nitpicks unless they are serious
- Max 5 issues per chunk

Output this exact schema:
{
  "summary": "1-2 sentence overall assessment of this chunk",
  "risk": "low | medium | high",
  "issues": [
    {
      "severity": "low | medium | high",
      "category": "bug | security | performance | maintainability | tests",
      "file": "path/to/file",
      "line": 0,
      "title": "short title",
      "explanation": "what is wrong and why",
      "suggestion": "how to fix it"
    }
  ],
  "checklist": ["action item 1", "action item 2"]
}

If there are no issues, return an empty issues array and low risk."""


async def _review_chunk(client: AsyncGroq, chunk: dict, pr_title: str) -> dict:
    """Review a single diff chunk."""
    user_message = (
        f"PR Title: {pr_title}\n"
        f"File: {chunk['file']}\n\n"
        f"Diff:\n{chunk['content']}"
    )

    response = await client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
        temperature=0.2,
        max_tokens=2000,
    )

    raw = response.choices[0].message.content.strip()

    # Strip markdown code fences if model adds them
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()

    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        logger.error(f"[review_agent] Failed to parse JSON for chunk {chunk['chunk_id']}: {e}")
        return {"summary": "", "risk": "low", "issues": [], "checklist": []}


def _merge_reviews(chunk_reviews: List[dict]) -> dict:
    """Merge findings from multiple chunk reviews into one."""
    all_issues = []
    all_checklist_items = set()
    summaries = []
    risk_order = {"low": 0, "medium": 1, "high": 2}
    max_risk = "low"

    for review in chunk_reviews:
        if review.get("summary"):
            summaries.append(review["summary"])

        risk = review.get("risk", "low")
        if risk_order.get(risk, 0) > risk_order.get(max_risk, 0):
            max_risk = risk

        for issue in review.get("issues", []):
            all_issues.append(issue)

        for item in review.get("checklist", []):
            all_checklist_items.add(item)

    # Deduplicate issues by title+file (same issue caught in overlapping chunks)
    seen = set()
    deduped_issues = []
    for issue in all_issues:
        key = (issue.get("title", ""), issue.get("file", ""))
        if key not in seen:
            seen.add(key)
            deduped_issues.append(issue)

    # Sort by severity descending
    severity_order = {"high": 0, "medium": 1, "low": 2}
    deduped_issues.sort(key=lambda x: severity_order.get(x.get("severity", "low"), 2))

    return {
        "summary": " ".join(summaries) if summaries else "Review complete.",
        "risk": max_risk,
        "issues": deduped_issues,
        "checklist": list(all_checklist_items),
    }


async def review_diff(diff: str, pr_title: str) -> dict:
    """Review a full PR diff, chunking if necessary."""
    from app.services.chunker import chunk_diff

    client = AsyncGroq(api_key=os.getenv("GROQ_API_KEY"))
    chunks = chunk_diff(diff)

    if not chunks:
        logger.info("[review_agent] No reviewable chunks found")
        return {
            "summary": "No reviewable code changes found.",
            "risk": "low",
            "issues": [],
            "checklist": [],
        }

    logger.info(f"[review_agent] Reviewing {len(chunks)} chunks for PR: '{pr_title}'")

    chunk_reviews = []
    for chunk in chunks:
        logger.info(f"[review_agent] Reviewing chunk {chunk['chunk_id']} ({chunk['size']} chars)")
        review = await _review_chunk(client, chunk, pr_title)
        chunk_reviews.append(review)

    merged = _merge_reviews(chunk_reviews)
    logger.info(
        f"[review_agent] Merged review: {len(merged['issues'])} issues, risk={merged['risk']}"
    )
    return merged


def format_review_comment(review: dict, pr_number: int) -> str:
    """Format the review JSON into a markdown PR comment with hidden idempotency marker."""
    risk_emoji = {"low": "🟢", "medium": "🟡", "high": "🔴"}.get(review.get("risk", "unknown"), "⚪")
    severity_emoji = {"low": "🔵", "medium": "🟡", "high": "🔴"}

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
            emoji = severity_emoji.get(sev, "🔵")
            lines.append(f"**{i}. {emoji} [{sev.upper()}] {issue.get('title', 'Issue')}**")
            lines.append(f"- **File:** `{issue.get('file', 'unknown')}`" +
                        (f" (line {issue.get('line')})" if issue.get('line') else ""))
            lines.append(f"- **Category:** {issue.get('category', 'general')}")
            lines.append(f"- **What:** {issue.get('explanation', '')}")
            lines.append(f"- **Fix:** {issue.get('suggestion', '')}")
            lines.append("")
    else:
        lines.append("### ✅ No issues found")
        lines.append("")

    checklist = review.get("checklist", [])
    if checklist:
        lines.append("### 📋 Checklist")
        lines.append("")
        for item in checklist:
            lines.append(f"- [ ] {item}")
        lines.append("")

    lines.append("---")
    lines.append("_Powered by Agentic PR Reviewer + Groq_")

    return "\n".join(lines)