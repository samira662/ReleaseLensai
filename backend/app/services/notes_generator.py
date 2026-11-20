"""Audience-specific release notes (AR3): end-user, developer and QA views."""
import json
import logging
from collections import defaultdict

from .llm_service import LLMError, LLMService

log = logging.getLogger(__name__)

AUDIENCES = ("end_user", "developer", "qa")
LABELS = {
    "feature": "New features", "bug_fix": "Bug fixes", "performance": "Performance", "security": "Security",
    "breaking": "Breaking changes", "refactor": "Refactoring", "docs": "Documentation",
}

STYLE = {
    "end_user": "Write for non-technical users. Short sections: What's new, Improvements, Fixes, Things to be aware of. No jargon, no commit hashes. Skip refactors and docs-only changes unless they affect users.",
    "developer": "Write for developers. Group by category, include PR numbers (#123) and short SHAs, and give a 'Breaking changes' section with file, symbol, old vs new signature and a migration hint.",
    "qa": "Write for testers. Give: 1) Areas to test first (ordered by risk), 2) A checklist of test focus per change, 3) Regression risks, 4) Breaking changes to verify. Reference modules/files.",
}


def _title(project: dict, audience: str) -> str:
    name = {"end_user": "Release notes", "developer": "Developer release notes", "qa": "QA test notes"}[audience]
    return f"# {name}: {project['name']} {project['target']}\n\n_Changes since {project['base']}_\n"


def _ref(c: dict) -> str:
    return f"#{c['pr_number']}" if c.get("pr_number") else c["sha"][:7]


def _module(path: str) -> str:
    parts = path.split("/")
    return "/".join(parts[:2]) if len(parts) > 2 else (parts[0] if len(parts) > 1 else "(root)")


def _template(audience: str, project: dict, changes: list[dict], findings: list[dict], risk: dict | None) -> str:
    by = defaultdict(list)
    for c in changes:
        by[c["category"]].append(c)
    out = [_title(project, audience)]

    if audience == "end_user":
        for key, head in (("feature", "What's new"), ("performance", "Improvements"), ("bug_fix", "Fixes"), ("security", "Security")):
            if by[key]:
                out.append(f"## {head}\n" + "\n".join(f"- {c['summary']}" for c in by[key]) + "\n")
        if findings or by["breaking"]:
            items = [f"- {f['reason']}" for f in findings[:8]] or [f"- {c['summary']}" for c in by["breaking"]]
            out.append("## Things to be aware of\nSome changes may require updates to how you use this software:\n" + "\n".join(items) + "\n")

    elif audience == "developer":
        for key in ("breaking", "feature", "bug_fix", "security", "performance", "refactor", "docs"):
            if by[key]:
                out.append(f"## {LABELS[key]}\n" + "\n".join(f"- {c['summary']} ({_ref(c)})" for c in by[key]) + "\n")
        if findings:
            rows = "\n".join(f"- `{f['file']}` - **{f['symbol']}**: {f['reason']}" + (f"\n  - before: `{f['old_signature']}`" if f["old_signature"] else "") + (f"\n  - after: `{f['new_signature']}`" if f["new_signature"] else "") for f in findings)
            out.append("## API compatibility findings\n" + rows + "\n")

    else:
        mods = defaultdict(list)
        for c in changes:
            for m in {_module(n) for n in c["files"]} or {"(unknown)"}:
                mods[m].append(c)
        ranked = sorted(mods.items(), key=lambda kv: -sum(x["additions"] + x["deletions"] for x in kv[1]))[:8]
        if risk:
            out.append(f"**Release risk: {risk['level']}** ({risk['score']}/100). {risk['explanation']}\n")
        out.append("## Areas to test first\n" + "\n".join(f"- `{m}` - {len(cs)} change(s)" for m, cs in ranked) + "\n")
        out.append("## Test checklist\n" + "\n".join(f"- [ ] {c['summary']} ({LABELS[c['category']]}, {_ref(c)})" for c in changes if c["category"] not in ("docs",))[:6000] + "\n")
        if findings:
            out.append("## Breaking changes to verify\n" + "\n".join(f"- [ ] `{f['file']}`: {f['reason']}" for f in findings) + "\n")
    if len(out) == 1:
        out.append("_No notable changes in this range._\n")
    return "\n".join(out)


def generate_notes(project: dict, changes: list[dict], findings: list[dict], risk: dict | None, llm: LLMService) -> dict[str, tuple[str, bool]]:
    """Return {audience: (markdown, ai_generated)}. Falls back to templates if the LLM is unavailable."""
    compact = [{"ref": _ref(c), "category": c["category"], "summary": c["summary"], "files": c["files"][:6], "breaking": c["is_breaking"]} for c in changes[:150]]
    out: dict[str, tuple[str, bool]] = {}
    for aud in AUDIENCES:
        text, ai = None, False
        if llm.available and changes:
            system = ("You write release notes from structured change data. Use ONLY the supplied data; never invent features, files or PR numbers. "
                      "Output Markdown starting at '##' headings (no top-level title). " + STYLE[aud])
            user = json.dumps({"project": project, "risk": risk and {"level": risk["level"], "explanation": risk["explanation"]}, "changes": compact, "breaking_findings": findings[:25]})
            try:
                body = llm.complete(system, user, max_tokens=3000).strip()
                if body:
                    text, ai = _title(project, aud) + "\n" + body + "\n", True
            except LLMError as exc:
                log.warning("Notes for %s fell back to template: %s", aud, exc)
        out[aud] = (text or _template(aud, project, changes, findings, risk), ai)
    return out
