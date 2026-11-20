"""Change classifier: category + one-line summary for every commit/PR (AR1)."""
import logging
import re

from ..config import settings
from .github_service import ChangeData
from .llm_service import LLMError, LLMService

log = logging.getLogger(__name__)

CATEGORIES = ["feature", "bug_fix", "refactor", "docs", "security", "performance", "breaking"]
BATCH = 8

SYSTEM = """You are a release engineer. For each code change, assign exactly one category and write a one-sentence summary.
Categories: feature, bug_fix, refactor, docs, security, performance, breaking.
- 'breaking' = removes/renames public API, changes a function signature or behaviour in a way that forces callers to change.
- Summaries must be plain, factual, <= 25 words, and describe the effect (not the commit wording). Never invent details that are not in the input.
Return a JSON array: [{"id": <int>, "category": "<category>", "summary": "<sentence>"}] with one entry per input id."""

_RULES = [
    ("breaking", re.compile(r"breaking[ -]change|^\w+(\(.+\))?!:", re.I | re.M)),
    ("security", re.compile(r"\b(security|cve-\d+|vulnerab\w*|xss|csrf|sql injection|sanitiz\w+)\b", re.I)),
    ("performance", re.compile(r"\b(perf\w*|speed ?up|faster|optimi[sz]\w*|cache|latency)\b", re.I)),
    ("bug_fix", re.compile(r"\b(fix(es|ed)?|bug|hotfix|resolve[sd]?|patch|crash|regression)\b", re.I)),
    ("docs", re.compile(r"\b(docs?|readme|documentation|typo|comment)\b", re.I)),
    ("refactor", re.compile(r"\b(refactor\w*|clean ?up|rename[sd]?|restructure|move[sd]?|chore|lint|format)\b", re.I)),
    ("feature", re.compile(r"\b(feat\w*|add(s|ed)?|implement\w*|support|introduce[sd]?|new)\b", re.I)),
]


def heuristic_classify(change: ChangeData) -> dict:
    """Offline fallback: Conventional-Commit prefixes, keywords and file types."""
    text = f"{change.title}\n{change.message}"
    names = [f.filename for f in change.files]
    if names and all(re.search(r"\.(md|rst|txt)$|^docs?/", n, re.I) for n in names):
        category = "docs"
    else:
        category = next((cat for cat, rx in _RULES if rx.search(text)), "refactor")
    summary = re.sub(r"^\w+(\([^)]*\))?!?:\s*", "", change.title).strip()
    summary = (summary[:1].upper() + summary[1:])[:140] or change.title
    return {"category": category, "summary": summary}


def _diff_excerpt(change: ChangeData, budget: int) -> str:
    parts, used = [], 0
    for f in change.files:
        if not f.patch or used >= budget:
            continue
        chunk = f"--- {f.filename} ({f.status})\n{f.patch}"[: budget - used]
        parts.append(chunk)
        used += len(chunk)
    return "\n".join(parts)


def classify_changes(changes: list[ChangeData], llm: LLMService, send_diff: bool = True) -> tuple[list[dict], str]:
    """Returns (results aligned with `changes`, mode) where mode is 'llm' or 'heuristic'."""
    results = [heuristic_classify(c) for c in changes]
    if not llm.available or not changes:
        return results, "heuristic"
    mode = "llm"
    for start in range(0, len(changes), BATCH):
        batch = changes[start : start + BATCH]
        payload = []
        for i, c in enumerate(batch):
            item = {
                "id": start + i,
                "title": c.title,
                "message": c.message[:600],
                "pr": (c.pr_title or "") + " " + (c.pr_body or "")[:400],
                "files": [f.filename for f in c.files][:12],
            }
            if send_diff:
                item["diff"] = _diff_excerpt(c, settings.max_diff_chars_per_change)
            payload.append(item)
        try:
            import json

            data = llm.complete_json(SYSTEM, json.dumps(payload), max_tokens=2500)
            for row in data:
                idx = int(row["id"])
                cat = str(row.get("category", "")).strip().lower().replace(" ", "_")
                if 0 <= idx < len(changes) and cat in CATEGORIES:
                    results[idx] = {"category": cat, "summary": str(row.get("summary", "")).strip() or results[idx]["summary"]}
        except (LLMError, KeyError, ValueError, TypeError) as exc:
            log.warning("Classifier batch failed, using heuristics for it: %s", exc)
            mode = "mixed"
    return results, mode
