"""Breaking-change detector (AR2): compares public function/API signatures between two versions.

Python files are parsed with the `ast` module. JavaScript/TypeScript uses a lightweight
extractor for *exported* functions (swap in tree-sitter later for deeper coverage).
The LLM is only used to rephrase reasons for maintainers - detection itself is deterministic.
"""
import ast
import json
import logging
import re
from dataclasses import dataclass, field

from ..config import settings
from .llm_service import LLMError, LLMService

log = logging.getLogger(__name__)

PY_EXT = (".py",)
JS_EXT = (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs")
_TEST_PATH = re.compile(r"(^|/)(tests?|__tests__|spec|node_modules|dist|build)(/|$)|(_test|\.test|\.spec)\.\w+$|(^|/)test_[^/]*$")


@dataclass
class Param:
    name: str
    kind: str  # pos | vararg | kwonly | kwarg
    has_default: bool = False


@dataclass
class Signature:
    name: str
    params: list[Param] = field(default_factory=list)
    lang: str = "python"

    def render(self) -> str:
        out = []
        for p in self.params:
            s = {"vararg": "*", "kwarg": "**"}.get(p.kind, "") + p.name
            if p.has_default and p.kind not in ("vararg", "kwarg"):
                s += "=…"
            out.append(s)
        return f"{self.name}({', '.join(out)})"


def is_test_path(path: str) -> bool:
    return bool(_TEST_PATH.search(path))


def language_of(path: str) -> str | None:
    p = path.lower()
    if p.endswith(PY_EXT):
        return "python"
    if p.endswith(JS_EXT):
        return "js"
    return None


# ---------------------------------------------------------------- Python
def _py_params(args: ast.arguments, drop_first_self: bool) -> list[Param]:
    pos = args.posonlyargs + args.args
    first_default = len(pos) - len(args.defaults)
    params = [Param(a.arg, "pos", i >= first_default) for i, a in enumerate(pos)]
    if drop_first_self and params and params[0].name in ("self", "cls"):
        params = params[1:]
    if args.vararg:
        params.append(Param(args.vararg.arg, "vararg", True))
    params += [Param(a.arg, "kwonly", d is not None) for a, d in zip(args.kwonlyargs, args.kw_defaults)]
    if args.kwarg:
        params.append(Param(args.kwarg.arg, "kwarg", True))
    return params


def extract_python(src: str) -> dict[str, Signature]:
    try:
        tree = ast.parse(src)
    except (SyntaxError, ValueError):
        return {}
    out: dict[str, Signature] = {}

    def visit(body, prefix=""):
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if not node.name.startswith("_") or node.name == "__init__":
                    q = prefix + node.name
                    out[q] = Signature(q, _py_params(node.args, bool(prefix)), "python")
            elif isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
                visit(node.body, f"{prefix}{node.name}.")

    visit(tree.body)
    return out


# ---------------------------------------------------------------- JS / TS
_JS_FUNC = re.compile(r"export\s+(?:default\s+)?(?:async\s+)?function\s*\*?\s*(\w+)\s*(?:<[^>(]*>)?\s*\(")
_JS_ARROW = re.compile(r"export\s+(?:const|let|var)\s+(\w+)\s*(?::[^=\n]+)?=\s*(?:async\s*)?(function\s*\w*\s*)?\(")
_JS_CJS = re.compile(r"(?:module\.)?exports\.(\w+)\s*=\s*(?:async\s*)?(function\s*\w*\s*)?\(")


def _balanced(src: str, open_idx: int) -> tuple[str, int] | None:
    depth = 0
    for i in range(open_idx, len(src)):
        ch = src[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return src[open_idx + 1 : i], i
    return None


def _split_params(text: str) -> list[str]:
    parts, depth, cur = [], 0, ""
    for ch in text:
        if ch in "([{<":
            depth += 1
        elif ch in ")]}>":
            depth = max(depth - 1, 0)
        if ch == "," and depth == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    if cur.strip():
        parts.append(cur)
    return [p.strip() for p in parts if p.strip()]


def _js_params(text: str) -> list[Param]:
    params = []
    for raw in _split_params(text):
        rest = raw.startswith("...")
        body = raw.lstrip(".")
        name = re.split(r"[?:=]", body, maxsplit=1)[0].strip() or body
        optional = "=" in raw or bool(re.match(r"^[\w$]+\?", body))
        params.append(Param(name, "vararg" if rest else "pos", optional or rest))
    return params


def extract_js(src: str) -> dict[str, Signature]:
    out: dict[str, Signature] = {}
    for rx, needs_arrow in ((_JS_FUNC, False), (_JS_ARROW, True), (_JS_CJS, True)):
        for m in rx.finditer(src):
            res = _balanced(src, m.end() - 1)
            if res is None:
                continue
            inner, close = res
            is_function_kw = (not needs_arrow) or bool(m.lastindex and m.lastindex >= 2 and m.group(2))
            if not is_function_kw and not re.match(r"\s*(?::[^=;{]+)?=>", src[close + 1 : close + 120]):
                continue  # parenthesised expression, not an arrow function
            out[m.group(1)] = Signature(m.group(1), _js_params(inner), "js")
    return out


# ---------------------------------------------------------------- comparison
def _finding(file, sym, kind, reason, old: Signature | None, new: Signature | None) -> dict:
    return {
        "file": file,
        "symbol": sym,
        "kind": kind,
        "reason": reason,
        "old_signature": old.render() if old else None,
        "new_signature": new.render() if new else None,
    }


def _same_shape(a: Signature, b: Signature) -> bool:
    return [p.name for p in a.params] == [p.name for p in b.params]


def compare_signatures(old: dict[str, Signature], new: dict[str, Signature], file: str, lang: str) -> list[dict]:
    findings: list[dict] = []
    added = [n for n in new if n not in old]
    for name, o in old.items():
        n = new.get(name)
        if n is None:
            twin = next((a for a in added if _same_shape(o, new[a])), None)
            if twin:
                findings.append(_finding(file, name, "renamed", f"`{name}` was renamed to `{twin}`; existing callers will break.", o, new[twin]))
            else:
                findings.append(_finding(file, name, "removed", f"`{name}` was removed (or is no longer exported); existing callers will break.", o, None))
            continue

        old_names = [p.name for p in o.params]
        new_names = [p.name for p in n.params]
        gone = [x for x in old_names if x not in new_names]
        fresh = [x for x in new_names if x not in old_names]
        new_by = {p.name: p for p in n.params}
        old_by = {p.name: p for p in o.params}

        if gone and len(gone) == len(fresh):
            if lang == "python":
                findings.append(_finding(file, name, "param_renamed", f"Parameter(s) {', '.join(f'`{g}`' for g in gone)} renamed to {', '.join(f'`{f}`' for f in fresh)}; keyword callers will break.", o, n))
        elif gone:
            findings.append(_finding(file, name, "param_removed", f"Parameter(s) {', '.join(f'`{g}`' for g in gone)} removed from `{name}`.", o, n))
        required_new = [f for f in fresh if not new_by[f].has_default and new_by[f].kind in ("pos", "kwonly")]
        if required_new and not (len(gone) == len(fresh)):
            findings.append(_finding(file, name, "param_added_required", f"New required parameter(s) {', '.join(f'`{r}`' for r in required_new)} added to `{name}`.", o, n))
        if not gone and not fresh:
            op = [p.name for p in o.params if p.kind == "pos"]
            np_ = [p.name for p in n.params if p.kind == "pos"]
            if op != np_:
                findings.append(_finding(file, name, "param_reordered", f"Positional parameter order of `{name}` changed.", o, n))
        for pname, op in old_by.items():
            np_ = new_by.get(pname)
            if np_ is None:
                continue
            if op.has_default and not np_.has_default and op.kind not in ("vararg", "kwarg"):
                findings.append(_finding(file, name, "param_now_required", f"Parameter `{pname}` of `{name}` lost its default and is now required.", o, n))
            if lang == "python" and op.kind == "pos" and np_.kind == "kwonly":
                findings.append(_finding(file, name, "param_kwonly", f"Parameter `{pname}` of `{name}` is now keyword-only.", o, n))
    return findings


def detect_in_file(path: str, old_src: str | None, new_src: str | None) -> list[dict]:
    lang = language_of(path)
    if lang is None or old_src is None:
        return []
    extract = extract_python if lang == "python" else extract_js
    old = extract(old_src)
    new = extract(new_src) if new_src is not None else {}
    return compare_signatures(old, new, path, lang)


def detect_breaking_changes(github, full_name: str, base: str, head: str, release_files) -> list[dict]:
    """Fetch old/new versions of changed Python/JS/TS files and compare their public signatures."""
    findings: list[dict] = []
    candidates = [
        f for f in release_files
        if f.status in ("modified", "removed", "renamed") and language_of(f.filename) and not is_test_path(f.filename)
    ][: settings.max_breaking_files]
    for f in candidates:
        old_path = f.previous_filename or f.filename
        old_src = github.get_file_content(full_name, old_path, base)
        new_src = None if f.status == "removed" else github.get_file_content(full_name, f.filename, head)
        if old_src is None or (new_src is None and f.status != "removed"):
            continue
        findings.extend(detect_in_file(f.filename, old_src, new_src))
    return findings


def refine_reasons(findings: list[dict], llm: LLMService) -> list[dict]:
    """Optionally let the LLM rewrite each reason as a short maintainer-facing sentence."""
    if not findings or not llm.available:
        return findings
    payload = [{"id": i, "file": f["file"], "kind": f["kind"], "old": f["old_signature"], "new": f["new_signature"], "reason": f["reason"]} for i, f in enumerate(findings[:40])]
    system = ("You review API compatibility findings. For each id, rewrite the reason as one clear sentence "
              "telling a maintainer what breaks and for whom. Do not add facts that are not in the input. "
              'Return JSON: [{"id": int, "reason": str}].')
    try:
        for row in llm.complete_json(system, json.dumps(payload), 2000):
            i = int(row["id"])
            if 0 <= i < len(findings) and row.get("reason"):
                findings[i]["reason"] = str(row["reason"]).strip()
    except (LLMError, KeyError, ValueError, TypeError) as exc:
        log.warning("Reason refinement skipped: %s", exc)
    return findings
