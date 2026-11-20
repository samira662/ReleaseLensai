from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---- auth ----
class UserCreate(BaseModel):
    email: EmailStr
    name: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=8, max_length=128)


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class UserOut(ORM):
    id: int
    email: str
    name: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


# ---- projects ----
class RefsRequest(BaseModel):
    repo_url: str
    github_token: str | None = None


class RefsOut(BaseModel):
    repo: str
    default_branch: str
    tags: list[str]
    branches: list[str]


class ProjectCreate(BaseModel):
    name: str | None = None
    repo_url: str
    base_ref: str
    target_ref: str
    github_token: str | None = None
    send_diff_to_llm: bool = True


class ProjectOut(ORM):
    id: int
    name: str
    repo_full_name: str
    repo_url: str
    base_ref: str
    target_ref: str
    status: str
    progress: str
    error: str | None
    send_diff_to_llm: bool
    created_at: datetime
    risk_level: str | None = None


# ---- changes ----
class ChangeOut(ORM):
    id: int
    sha: str
    title: str
    author: str
    url: str
    pr_number: int | None
    issues: list
    files: list
    additions: int
    deletions: int
    category: str
    summary: str
    is_breaking: bool
    breaking_reason: str | None
    edited: bool


class ChangeUpdate(BaseModel):
    category: str | None = None
    summary: str | None = None


class BreakingOut(ORM):
    id: int
    file: str
    symbol: str
    kind: str
    reason: str
    old_signature: str | None
    new_signature: str | None
    commit_sha: str | None


# ---- notes / risk / qa ----
class NoteOut(ORM):
    audience: str
    content: str
    ai_generated: bool
    approved: bool
    updated_at: datetime


class NoteUpdate(BaseModel):
    content: str


class RiskOut(ORM):
    level: str
    score: float
    factors: list
    explanation: str
    ai_generated: bool


class QARequest(BaseModel):
    question: str = Field(min_length=3, max_length=500)


class QASource(BaseModel):
    ref: str
    sha: str
    title: str
    url: str
    pr_number: int | None = None
    category: str | None = None


class QAResponse(BaseModel):
    answer: str
    sources: list[QASource]
    mode: str  # "llm" | "extractive"
    ai_generated: bool = True
