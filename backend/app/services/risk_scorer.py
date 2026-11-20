"""Release risk scorer (AR4): transparent rule-based score + optional ML blend + LLM explanation."""
import json
import logging
import os
import re

from ..config import settings
from .breaking_detector import is_test_path
from .llm_service import LLMError, LLMService

log = logging.getLogger(__name__)

CODE_EXT = re.compile(r"\.(py|js|jsx|ts|tsx|java|go|rb|php|cs|rs|c|cpp|kt|swift)$", re.I)


def compute_features(changes: list[dict], findings: list[dict], release_files: list, hotspot_ratio: float) -> dict:
    names = [f.filename for f in release_files] or sorted({n for c in changes for n in c["files"]})
    code = [n for n in names if CODE_EXT.search(n) and not is_test_path(n)]
    tests = [n for n in names if is_test_path(n)]
    total = max(len(changes), 1)
    return {
        "lines_changed": sum(c["additions"] + c["deletions"] for c in changes),
        "files_touched": len(names),
        "breaking_count": len(findings) + sum(1 for c in changes if c["is_breaking"] and not c.get("from_ast")),
        "security_count": sum(1 for c in changes if c["category"] == "security"),
        "bugfix_ratio": sum(1 for c in changes if c["category"] == "bug_fix") / total,
        "hotspot_ratio": hotspot_ratio,
        "test_signal": min(1.0, len(tests) / max(1.0, len(code) * 0.5)) if code else 1.0,
    }


def _factors(f: dict) -> list[dict]:
    def row(name, value, points, detail):
        return {"name": name, "value": value, "points": round(points, 1), "detail": detail}

    return [
        row("Breaking changes", f["breaking_count"], min(f["breaking_count"], 5) / 5 * 30, f"{f['breaking_count']} suspected breaking change(s)"),
        row("Size of change", f["lines_changed"], min(f["lines_changed"] / 2000, 1) * 20, f"{f['lines_changed']} lines added or removed"),
        row("Files touched", f["files_touched"], min(f["files_touched"] / 60, 1) * 10, f"{f['files_touched']} files changed"),
        row("Security-related changes", f["security_count"], min(f["security_count"], 3) / 3 * 10, f"{f['security_count']} security change(s)"),
        row("Bug-prone files", round(f["hotspot_ratio"], 2), f["hotspot_ratio"] * 10, f"{round(f['hotspot_ratio'] * 100)}% of recent history in touched files was bug fixing"),
        row("Share of bug-fix commits", round(f["bugfix_ratio"], 2), f["bugfix_ratio"] * 10, f"{round(f['bugfix_ratio'] * 100)}% of commits are bug fixes"),
        row("Test coverage signal", round(f["test_signal"], 2), (1 - f["test_signal"]) * 10, "Few test files changed alongside code" if f["test_signal"] < 0.5 else "Tests changed alongside code"),
    ]


def _ml_probability(f: dict) -> float | None:
    """If a trained scikit-learn model exists (RISK_MODEL_PATH), return P(risky) in [0,1]."""
    path = settings.risk_model_path
    if not path or not os.path.exists(path):
        return None
    try:
        import joblib

        model = joblib.load(path)
        x = [[f["lines_changed"], f["files_touched"], f["breaking_count"], f["security_count"], f["bugfix_ratio"], f["hotspot_ratio"], f["test_signal"]]]
        return float(model.predict_proba(x)[0][1])
    except Exception as exc:
        log.warning("Risk model unavailable: %s", exc)
        return None


def level_for(score: float) -> str:
    return "Low" if score < 30 else "Medium" if score < 60 else "High"


def _fallback_text(level: str, factors: list[dict]) -> str:
    top = sorted(factors, key=lambda x: -x["points"])[:3]
    bits = "; ".join(f["detail"] for f in top if f["points"] > 0) or "no significant risk signals were found"
    return f"Risk is {level.lower()} mainly because: {bits}. This score is advisory - the team decides whether to release."


def score_release(changes: list[dict], findings: list[dict], release_files: list, hotspot_ratio: float, llm: LLMService) -> dict:
    feats = compute_features(changes, findings, release_files, hotspot_ratio)
    factors = _factors(feats)
    score = sum(f["points"] for f in factors)

    ml = _ml_probability(feats)
    if ml is not None:
        score = 0.6 * score + 0.4 * (ml * 100)
        factors.append({"name": "Learned model", "value": round(ml, 2), "points": 0, "detail": f"Trained model estimates {round(ml * 100)}% risk (blended 40%)"})

    explanation = None
    if llm.available:
        system = ("You explain release risk to a maintainer. Given the factors, write 3-4 plain sentences naming the biggest drivers "
                  "and what to test first. Optionally nudge the score by -10..+10 if the factors miss something obvious in the change list. "
                  'Return JSON: {"explanation": str, "adjustment": number}.')
        user = json.dumps({"score": round(score, 1), "factors": factors, "breaking": findings[:10], "sample_changes": [{"title": c["title"], "category": c["category"]} for c in changes[:25]]})
        try:
            data = llm.complete_json(system, user, 800)
            explanation = str(data.get("explanation", "")).strip() or None
            score += max(-10.0, min(10.0, float(data.get("adjustment", 0))))
        except (LLMError, ValueError, TypeError, AttributeError) as exc:
            log.warning("Risk explanation fell back to template: %s", exc)

    score = max(0.0, min(100.0, score))
    level = level_for(score)
    return {"level": level, "score": round(score, 1), "factors": factors, "explanation": explanation or _fallback_text(level, factors), "ai_generated": explanation is not None}
