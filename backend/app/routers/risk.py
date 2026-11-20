from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_owned_project
from ..models import Project, RiskReport
from ..schemas import RiskOut

router = APIRouter(prefix="/projects/{project_id}/risk", tags=["risk"])


@router.get("", response_model=RiskOut)
def get_risk(project: Project = Depends(get_owned_project), db: Session = Depends(get_db)):
    risk = db.scalar(select(RiskReport).where(RiskReport.project_id == project.id))
    if risk is None:
        raise HTTPException(404, "Risk report is not ready yet")
    return risk
