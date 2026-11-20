import io
import re
from xml.sax.saxutils import escape

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_owned_project
from ..models import BreakingFinding, Change, Note, Project, RiskReport
from ..schemas import NoteOut, NoteUpdate
from ..services.llm_service import get_llm
from ..services.notes_generator import AUDIENCES, generate_notes

router = APIRouter(prefix="/projects/{project_id}/notes", tags=["notes"])


def _get_note(db: Session, project: Project, audience: str) -> Note:
    if audience not in AUDIENCES:
        raise HTTPException(400, f"Audience must be one of: {', '.join(AUDIENCES)}")
    note = db.scalar(select(Note).where(Note.project_id == project.id, Note.audience == audience))
    if note is None:
        raise HTTPException(404, "Notes have not been generated yet")
    return note


@router.get("", response_model=list[NoteOut])
def list_notes(project: Project = Depends(get_owned_project), db: Session = Depends(get_db)):
    return db.scalars(select(Note).where(Note.project_id == project.id)).all()


@router.put("/{audience}", response_model=NoteOut)  # FR7: edit AI text before export
def edit_note(audience: str, body: NoteUpdate, project: Project = Depends(get_owned_project), db: Session = Depends(get_db)):
    note = _get_note(db, project, audience)
    note.content, note.approved = body.content, False  # any edit needs re-approval
    db.commit()
    db.refresh(note)
    return note


@router.post("/{audience}/approve", response_model=NoteOut)  # AR6
def approve_note(audience: str, project: Project = Depends(get_owned_project), db: Session = Depends(get_db)):
    note = _get_note(db, project, audience)
    note.approved = True
    db.commit()
    db.refresh(note)
    return note


@router.post("/regenerate", response_model=list[NoteOut])
def regenerate(project: Project = Depends(get_owned_project), db: Session = Depends(get_db)):
    """Rewrite all three versions from the (possibly user-corrected) change list."""
    rows = db.scalars(select(Change).where(Change.project_id == project.id).order_by(Change.id)).all()
    changes = [{
        "sha": c.sha, "title": c.title, "author": c.author, "url": c.url, "pr_number": c.pr_number, "files": c.files,
        "additions": c.additions, "deletions": c.deletions, "category": c.category, "summary": c.summary, "is_breaking": c.is_breaking,
    } for c in rows]
    findings = [{"file": f.file, "symbol": f.symbol, "kind": f.kind, "reason": f.reason, "old_signature": f.old_signature, "new_signature": f.new_signature}
                for f in db.scalars(select(BreakingFinding).where(BreakingFinding.project_id == project.id))]
    risk_row = db.scalar(select(RiskReport).where(RiskReport.project_id == project.id))
    risk = risk_row and {"level": risk_row.level, "score": risk_row.score, "explanation": risk_row.explanation}
    notes = generate_notes({"name": project.name, "base": project.base_ref, "target": project.target_ref}, changes, findings, risk, get_llm())
    db.execute(delete(Note).where(Note.project_id == project.id))
    db.add_all(Note(project_id=project.id, audience=a, content=t, ai_generated=ai, approved=False) for a, (t, ai) in notes.items())
    db.commit()
    return db.scalars(select(Note).where(Note.project_id == project.id)).all()


@router.get("/{audience}/export")  # FR6
def export_note(audience: str, format: str = "md", project: Project = Depends(get_owned_project), db: Session = Depends(get_db)):
    note = _get_note(db, project, audience)
    if not note.approved:  # AR6: human approval before export
        raise HTTPException(409, "Approve the notes before exporting.")
    base = f"{project.name}-{project.target_ref}-{audience}".replace("/", "-").replace(" ", "-")
    if format == "md":
        return Response(note.content, media_type="text/markdown", headers={"Content-Disposition": f'attachment; filename="{base}.md"'})
    if format == "pdf":
        return Response(markdown_to_pdf(note.content), media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{base}.pdf"'})
    raise HTTPException(400, "format must be 'md' or 'pdf'")


def _inline(text: str) -> str:
    text = escape(text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"`(.+?)`", r"<font face='Courier'>\1</font>", text)
    text = re.sub(r"(?<![\w])_(.+?)_(?![\w])", r"<i>\1</i>", text)
    return text


def markdown_to_pdf(md: str) -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

    ss = getSampleStyleSheet()
    body = ParagraphStyle("b", parent=ss["BodyText"], fontSize=10, leading=14)
    bullet = ParagraphStyle("li", parent=body, leftIndent=14, bulletIndent=4)
    heads = {1: ss["Title"], 2: ss["Heading2"], 3: ss["Heading3"]}
    story = []
    for line in md.splitlines():
        s = line.rstrip()
        if not s.strip():
            story.append(Spacer(1, 4))
        elif m := re.match(r"^(#{1,3})\s+(.*)", s):
            story.append(Paragraph(_inline(m.group(2)), heads[len(m.group(1))]))
        elif m := re.match(r"^\s*[-*]\s+(\[[ x]\]\s+)?(.*)", s):
            mark = "\u2610 " if m.group(1) else ""
            story.append(Paragraph(mark + _inline(m.group(2)), bullet, bulletText="\u2022"))
        else:
            story.append(Paragraph(_inline(s.strip()), body))
    buf = io.BytesIO()
    SimpleDocTemplate(buf, pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm, topMargin=18 * mm, bottomMargin=18 * mm).build(story)
    return buf.getvalue()
