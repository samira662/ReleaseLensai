import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..auth import decrypt_token, encrypt_token
from ..database import SessionLocal, get_db
from ..deps import get_current_user, get_owned_project
from ..models import BreakingFinding, Change, Note, Project, RiskReport, User
from ..schemas import ProjectCreate, ProjectOut, RefsOut, RefsRequest
from ..services import rag_service
from ..services.breaking_detector import detect_breaking_changes, is_test_path, language_of, refine_reasons
from ..services.classifier import classify_changes
from ..services.github_service import GitHubError, GitHubService, parse_repo_url
from ..services.llm_service import get_llm
from ..services.notes_generator import generate_notes
from ..services.risk_scorer import score_release

log = logging.getLogger(__name__)
router = APIRouter(prefix="/projects", tags=["projects"])


def _out(p: Project) -> ProjectOut:
    out = ProjectOut.model_validate(p)
    out.risk_level = p.risk.level if p.risk else None
    return out


@router.post("/refs", response_model=RefsOut)
def list_refs(body: RefsRequest, _: User = Depends(get_current_user)):
    """Tags and branches of a repository, so the user can pick the two versions to compare."""
    try:
        full = parse_repo_url(body.repo_url)
        return GitHubService(body.github_token).list_refs(full)
    except GitHubError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("", response_model=ProjectOut, status_code=201)
def create_project(body: ProjectCreate, tasks: BackgroundTasks, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    try:
        full = parse_repo_url(body.repo_url)
    except GitHubError as exc:
        raise HTTPException(400, str(exc)) from exc
    if body.base_ref == body.target_ref:
        raise HTTPException(400, "Choose two different versions to compare.")
    project = Project(
        user_id=user.id,
        name=(body.name or full.split("/")[1]).strip(),
        repo_full_name=full,
        repo_url=f"https://github.com/{full}",
        base_ref=body.base_ref,
        target_ref=body.target_ref,
        github_token_enc=encrypt_token(body.github_token) if body.github_token else None,
        send_diff_to_llm=body.send_diff_to_llm,
        status="pending",
    )
    db.add(project)
    db.commit()
    db.refresh(project)
    tasks.add_task(run_analysis, project.id)
    return _out(project)


@router.get("", response_model=list[ProjectOut])
def list_projects(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(Project).where(Project.user_id == user.id).order_by(Project.created_at.desc())).all()
    return [_out(p) for p in rows]


@router.get("/{project_id}", response_model=ProjectOut)
def get_project(project: Project = Depends(get_owned_project)):
    return _out(project)


@router.delete("/{project_id}", status_code=204)
def delete_project(project: Project = Depends(get_owned_project), db: Session = Depends(get_db)):
    db.delete(project)
    db.commit()


@router.post("/{project_id}/reanalyze", response_model=ProjectOut)
def reanalyze(tasks: BackgroundTasks, project: Project = Depends(get_owned_project), db: Session = Depends(get_db)):
    if project.status == "running":
        raise HTTPException(409, "Analysis is already running.")
    project.status, project.progress, project.error = "pending", "", None
    db.commit()
    tasks.add_task(run_analysis, project.id)
    return _out(project)


# ------------------------------------------------------------------ pipeline
def _set(db: Session, project: Project, **kw):
    for k, v in kw.items():
        setattr(project, k, v)
    db.commit()


def _to_dict(c: Change) -> dict:
    return {
        "sha": c.sha, "title": c.title, "author": c.author, "url": c.url, "pr_number": c.pr_number,
        "files": c.files, "additions": c.additions, "deletions": c.deletions, "category": c.category,
        "summary": c.summary, "is_breaking": c.is_breaking,
    }


def run_analysis(project_id: int) -> None:
    """Workflow steps 2-6: collect -> preprocess -> analyse -> generate. Runs as a background task."""
    db = SessionLocal()
    try:
        project = db.get(Project, project_id)
        if project is None:
            return
        llm = get_llm()
        _set(db, project, status="running", progress="Fetching commits and diffs from GitHub", error=None)
        gh = GitHubService(decrypt_token(project.github_token_enc))  # token stays here; never passed to the LLM
        full, base, head = project.repo_full_name, project.base_ref, project.target_ref
        raw_changes, release_files = gh.fetch_changes(full, base, head)

        _set(db, project, progress=f"Classifying {len(raw_changes)} changes")
        classified, _mode = classify_changes(raw_changes, llm, project.send_diff_to_llm)

        _set(db, project, progress="Checking API signatures for breaking changes")
        findings = detect_breaking_changes(gh, full, base, head, release_files)
        findings = refine_reasons(findings, llm)

        # clear old results (re-analysis)
        for model in (Change, BreakingFinding, Note, RiskReport):
            db.execute(delete(model).where(model.project_id == project.id))
        db.commit()

        rows: list[Change] = []
        for raw, cls in zip(raw_changes, classified):
            rows.append(Change(
                project_id=project.id, sha=raw.sha, title=raw.title, author=raw.author, url=raw.url,
                pr_number=raw.pr_number, issues=raw.issues, files=[f.filename for f in raw.files],
                additions=raw.additions, deletions=raw.deletions, category=cls["category"], summary=cls["summary"],
                is_breaking=cls["category"] == "breaking",
                breaking_reason="Marked as breaking by the classifier." if cls["category"] == "breaking" else None,
            ))
        # link signature findings to the latest commit that touched the file
        for f in findings:
            owner = next((r for r in reversed(rows) if f["file"] in r.files), None)
            f["commit_sha"] = owner.sha if owner else None
            if owner:
                owner.is_breaking, owner.category = True, "breaking"
                owner.breaking_reason = f["reason"]
                owner.from_ast = True  # transient marker used for risk counting
        db.add_all(rows)
        db.add_all(BreakingFinding(project_id=project.id, **f) for f in findings)
        db.commit()
        changes = [_to_dict(r) | {"from_ast": getattr(r, "from_ast", False)} for r in rows]

        _set(db, project, progress="Scoring release risk")
        hotspot = _hotspot_ratio(gh, full, base, release_files)
        risk = score_release(changes, findings, release_files, hotspot, llm)
        db.add(RiskReport(project_id=project.id, **risk))
        db.commit()

        _set(db, project, progress="Writing release notes")
        meta = {"name": project.name, "base": base, "target": head}
        notes = generate_notes(meta, changes, findings, risk, llm)
        db.add_all(Note(project_id=project.id, audience=a, content=text, ai_generated=ai, approved=False) for a, (text, ai) in notes.items())
        db.commit()

        _set(db, project, progress="Indexing for questions")
        rag_service.index_project(project.id, changes, findings)
        _set(db, project, status="done", progress="")
    except GitHubError as exc:
        db.rollback()
        _fail(db, project_id, str(exc))
    except Exception as exc:  # keep the UI informed instead of failing silently
        log.exception("Analysis failed")
        db.rollback()
        _fail(db, project_id, f"Analysis failed: {exc}")
    finally:
        db.close()


def _fail(db: Session, project_id: int, message: str) -> None:
    project = db.get(Project, project_id)
    if project:
        _set(db, project, status="failed", progress="", error=message)


def _hotspot_ratio(gh: GitHubService, full: str, base: str, release_files) -> float:
    """Share of bug-fix commits in the recent history of the 5 biggest changed code files."""
    code = [f for f in release_files if language_of(f.filename) and not is_test_path(f.filename)]
    top = sorted(code, key=lambda f: -(f.additions + f.deletions))[:5]
    fixes = total = 0
    for f in top:
        a, b = gh.file_fix_history(full, f.filename, base)
        fixes, total = fixes + a, total + b
    return fixes / total if total else 0.0
