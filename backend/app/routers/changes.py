from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_owned_project
from ..models import BreakingFinding, Change, Project
from ..schemas import BreakingOut, ChangeOut, ChangeUpdate
from ..services.classifier import CATEGORIES

router = APIRouter(prefix="/projects/{project_id}", tags=["changes"])


@router.get("/changes", response_model=list[ChangeOut])
def list_changes(category: str | None = None, project: Project = Depends(get_owned_project), db: Session = Depends(get_db)):
    q = select(Change).where(Change.project_id == project.id)
    if category:
        q = q.where(Change.category == category)
    return db.scalars(q.order_by(Change.id)).all()


@router.patch("/changes/{change_id}", response_model=ChangeOut)  # FR5: editable category
def update_change(change_id: int, body: ChangeUpdate, project: Project = Depends(get_owned_project), db: Session = Depends(get_db)):
    change = db.get(Change, change_id)
    if change is None or change.project_id != project.id:
        raise HTTPException(404, "Change not found")
    if body.category is not None:
        if body.category not in CATEGORIES:
            raise HTTPException(400, f"Category must be one of: {', '.join(CATEGORIES)}")
        change.category = body.category
        change.is_breaking = body.category == "breaking" or bool(change.breaking_reason and change.is_breaking)
    if body.summary is not None:
        change.summary = body.summary.strip()
    change.edited = True
    db.commit()
    db.refresh(change)
    return change


@router.get("/breaking", response_model=list[BreakingOut])
def list_breaking(project: Project = Depends(get_owned_project), db: Session = Depends(get_db)):
    return db.scalars(select(BreakingFinding).where(BreakingFinding.project_id == project.id)).all()
