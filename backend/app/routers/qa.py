from fastapi import APIRouter, Depends, HTTPException

from ..deps import get_owned_project
from ..models import Project
from ..schemas import QARequest, QAResponse
from ..services import rag_service
from ..services.llm_service import get_llm

router = APIRouter(prefix="/projects/{project_id}/qa", tags=["qa"])


@router.post("", response_model=QAResponse)
def ask(body: QARequest, project: Project = Depends(get_owned_project)):
    if project.status != "done":
        raise HTTPException(409, "Wait for the analysis to finish before asking questions.")
    return rag_service.answer(project.id, body.question, get_llm())
