from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def _now():
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    projects: Mapped[list["Project"]] = relationship(back_populates="owner", cascade="all, delete-orphan")


class Project(Base):
    """One repository + one release comparison (base -> target)."""

    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    repo_full_name: Mapped[str] = mapped_column(String(255))
    repo_url: Mapped[str] = mapped_column(String(500))
    base_ref: Mapped[str] = mapped_column(String(200))
    target_ref: Mapped[str] = mapped_column(String(200))
    github_token_enc: Mapped[str | None] = mapped_column(Text, nullable=True)  # Fernet-encrypted
    send_diff_to_llm: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|running|done|failed
    progress: Mapped[str] = mapped_column(String(200), default="")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    owner: Mapped[User] = relationship(back_populates="projects")
    changes: Mapped[list["Change"]] = relationship(cascade="all, delete-orphan")
    findings: Mapped[list["BreakingFinding"]] = relationship(cascade="all, delete-orphan")
    notes: Mapped[list["Note"]] = relationship(cascade="all, delete-orphan")
    risk: Mapped["RiskReport | None"] = relationship(cascade="all, delete-orphan", uselist=False)


class Change(Base):
    __tablename__ = "changes"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    sha: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(String(500))
    author: Mapped[str] = mapped_column(String(200), default="")
    url: Mapped[str] = mapped_column(String(500), default="")
    pr_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    issues: Mapped[list] = mapped_column(JSON, default=list)  # [{"number":1,"title":"..."}]
    files: Mapped[list] = mapped_column(JSON, default=list)  # filenames only (diffs are not stored)
    additions: Mapped[int] = mapped_column(Integer, default=0)
    deletions: Mapped[int] = mapped_column(Integer, default=0)
    category: Mapped[str] = mapped_column(String(20), default="refactor")
    summary: Mapped[str] = mapped_column(Text, default="")
    is_breaking: Mapped[bool] = mapped_column(Boolean, default=False)
    breaking_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    edited: Mapped[bool] = mapped_column(Boolean, default=False)


class BreakingFinding(Base):
    __tablename__ = "breaking_findings"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    file: Mapped[str] = mapped_column(String(500))
    symbol: Mapped[str] = mapped_column(String(300))
    kind: Mapped[str] = mapped_column(String(40))
    reason: Mapped[str] = mapped_column(Text)
    old_signature: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_signature: Mapped[str | None] = mapped_column(Text, nullable=True)
    commit_sha: Mapped[str | None] = mapped_column(String(40), nullable=True)


class Note(Base):
    __tablename__ = "notes"
    __table_args__ = (UniqueConstraint("project_id", "audience"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    audience: Mapped[str] = mapped_column(String(20))  # end_user | developer | qa
    content: Mapped[str] = mapped_column(Text, default="")
    ai_generated: Mapped[bool] = mapped_column(Boolean, default=True)
    approved: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class RiskReport(Base):
    __tablename__ = "risk_reports"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), unique=True)
    level: Mapped[str] = mapped_column(String(10))  # Low | Medium | High
    score: Mapped[float] = mapped_column(Float)
    factors: Mapped[list] = mapped_column(JSON, default=list)
    explanation: Mapped[str] = mapped_column(Text, default="")
    ai_generated: Mapped[bool] = mapped_column(Boolean, default=True)
