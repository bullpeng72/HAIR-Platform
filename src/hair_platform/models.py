"""Pydantic v2 요청 모델 — v0 범위(F1·F12·승인 결정)만 다룬다."""

from __future__ import annotations

from pydantic import BaseModel


class LoginRequest(BaseModel):
    username: str
    password: str


class BriefSubmission(BaseModel):
    """F1 — 규칙기반 스텁. 실제 Spec Interpreter(LLM) 연동은 이후 버전에서 이뤄진다."""

    raw_text: str


class HumanPoolCreate(BaseModel):
    """F12 — Human Pool 온보딩 폼."""

    name: str
    role: str
    skill_tags: list[str] = []
    availability: str = "available"
    manager_id: int | None = None


class ApprovalDecision(BaseModel):
    approve: bool
