from datetime import datetime
from typing import Optional
from sqlmodel import SQLModel, Field, Column
from sqlalchemy import JSON
import uuid
import secrets


class AgentRun(SQLModel, table=True):
    __tablename__ = "agent_runs"

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    provider: str
    model: str
    status: str = "running"
    started_at: datetime = Field(default_factory=datetime.utcnow)
    ended_at: Optional[datetime] = None
    input_tokens: int = 0
    output_tokens: int = 0
    label: str = ""
    task_description: Optional[str] = None
    user: Optional[str] = None
    git_commits: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    git_prs: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    ticket_refs: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    parent_id: Optional[str] = None
    meta: dict = Field(default_factory=dict, sa_column=Column(JSON))


class TranscriptStore(SQLModel, table=True):
    __tablename__ = "transcript_store"
    session_id: str = Field(primary_key=True)
    content: str = Field(default="")


class ApiKey(SQLModel, table=True):
    __tablename__ = "api_keys"
    key: str = Field(
        default_factory=lambda: f"adk_{secrets.token_urlsafe(32)}",
        primary_key=True,
    )
    user: str
    created_at: datetime = Field(default_factory=datetime.utcnow)


class AgentRunRead(SQLModel):
    id: str
    provider: str
    model: str
    status: str
    started_at: datetime
    ended_at: Optional[datetime] = None
    duration_seconds: Optional[float] = None
    input_tokens: int
    output_tokens: int
    label: str
    task_description: Optional[str] = None
    user: Optional[str] = None
    git_commits: list[str] = Field(default_factory=list)
    git_prs: list[str] = Field(default_factory=list)
    ticket_refs: list[str] = Field(default_factory=list)
    parent_id: Optional[str] = None
