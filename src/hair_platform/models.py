"""Pydantic v2 요청 모델 — v4(전체) 범위까지 다룬다."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class LoginRequest(BaseModel):
    username: str
    password: str


class UserCreate(BaseModel):
    """조직도(F5/F7) 코드 재점검 후 신규 — `users.manager_id`를 Web UI로 채울 수 있는
    유일한 경로였던 부트스트랩 스크립트 의존을 없앤다(ADMIN 전용)."""

    username: str
    password: str
    role: Literal["admin", "manager", "member", "viewer"]
    manager_id: int | None = None


class ManagerUpdate(BaseModel):
    """F7 결재라인 자동라우팅이 참조하는 `users.manager_id`를 갱신한다(ADMIN 전용)."""

    manager_id: int | None = None


class BriefSubmission(BaseModel):
    """F1 — 규칙기반 스텁. 실제 Spec Interpreter(LLM) 연동은 이후 버전에서 이뤄진다."""

    raw_text: str


class HumanPoolCreate(BaseModel):
    """F12 — Human Pool 온보딩 폼."""

    name: str
    role: str
    skill_tags: list[str] = []
    availability: str = "available"
    cost_estimate: float = 0.0
    manager_id: int | None = None


class HumanPoolUpdate(BaseModel):
    """코드 재점검(R3)에서 발견 — Agent Pool엔 onboard/deprecate 라이프사이클이
    있는데 Human Pool엔 생성 후 수정할 방법이 전혀 없던 비대칭을 없앤다.
    전부 optional — 보낸 필드만 갱신한다."""

    role: str | None = None
    skill_tags: list[str] | None = None
    availability: str | None = None
    cost_estimate: float | None = None


class ApprovalDecision(BaseModel):
    approve: bool


class TaskItem(BaseModel):
    """F2 — Resource Designer가 산출한 Task 분해 결과 한 건(ADR 결정 1-A: 후보 매칭 미포함)."""

    role_name: str
    required_skills: list[str] = []
    headcount: int = 1


class TaskDecomposition(BaseModel):
    """F2 — 매니저가 검토·조정한 Task 분해 결과 전체."""

    tasks: list[TaskItem]


class ResourceCandidate(BaseModel):
    """ADR 결정 4 — Human Pool·Agent Pool 공통 인터페이스."""

    id: int
    type: Literal["human", "agent"]
    role: str
    is_available: bool
    cost: float
    skill_tags: list[str]
    performance_score: float | None = None


class AssignmentRequest(BaseModel):
    """F6 — 배분 화면에서 매니저가 후보를 배정 요청한다(상태는 항상 requested로 시작)."""

    resource_type: Literal["human", "agent"]
    resource_id: int


class GenerateRequest(BaseModel):
    """F4 — 생성 버튼. 부족한 역할(Task)들을 지정해 배치로 Agent 생성을 트리거한다."""

    task_ids: list[int]


class ConsentRequest(BaseModel):
    """F8 — 사람 성과데이터 수집 사전 동의(opt-in, NF9)."""

    consent: bool


class PerformanceScoreRequest(BaseModel):
    """F8 — 성과 점수 입력(0~1). Gate 실측 배선 전까지는 수동 입력이다."""

    score: float


class CostUpdateRequest(BaseModel):
    """F10 — Agent 비용 수동 입력(1차 범위, HR/LLM 과금 연동은 오픈 이슈)."""

    cost_estimate: float
