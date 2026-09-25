"""Gate A–G 평가 배선 — SPEC.md §12 오픈 이슈였던 agent-evaluator SDK 통합.

**배선만 되어 있다(사용자 선택, 2026-09-25)**: `evaluate_generation()`을 호출하면
실제로 Gate 측정이 실행된다. 이 함수가 감싸는 `jobs.create_and_run_job()`은
결정론적 스텁(`default_stub_generator`, LLM 미사용 — ADR 결정 2)만 호출하고,
아래 설정도 `llm_judge`·환각탐지를 켜지 않았으므로 **호출해도 Anthropic/OpenAI
API를 부르지 않는다 — 이 배선 자체는 비용이 들지 않을 것으로 보인다**(단, 이건
이 세션의 판단이지 실측은 아니다 — 실행 전 `agent-eval check`로 직접 확인 권장).

실행 방법(직접 돌릴 때):
    python -m hair_platform.eval_wiring
    agent-eval gate results/*.json --tcr 85   # 이후 Gate 채점

한계(정직하게 명시, docs/LIMITS.md §1 참고): `default_stub_generator`는
"역할명·스킬을 그대로 되돌려주는" 규칙기반 스텁이라 Gate A(응답품질)·G(설명가능성)
가 요구하는 "실제 판단 근거"가 없다. 이 배선으로 처음 측정하면 낮은 점수가 나올
가능성이 높다 — 이건 버그가 아니라 스텁의 정직한 한계를 드러내는 것이다.
AgentForge의 Spec Interpreter/Code Generator가 실제로 vendor/import되면(SPEC.md
§12 남은 일) 그때 이 배선이 의미 있는 점수를 낸다. Gate C(재현성)는 스텁이
결정론적이라 항상 100%로 나와 정보가 없고, Gate F(멀티에이전트 조율)는 에이전트
간 위임이 없어 애초에 해당 사항이 없다 — 둘 다 이 배선 대상에서 제외했다.
"""

from __future__ import annotations

import json

from agent_evaluator import (
    EvalMetadata,
    ExplainabilityConfig,
    PerformanceMonitor,
    ScopeConfig,
    SLAConfig,
    agent_eval,
)

from hair_platform import auth, db, jobs

monitor = PerformanceMonitor(output_dir="results/", agent_version="auto")


def _run_generation(
    role_name: str, required_skills: list[str]
) -> tuple[str, EvalMetadata]:
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    admin_id = auth.create_user(conn, "eval-admin", "eval-pass", auth.Role.ADMIN)

    brief_cur = conn.execute(
        "INSERT INTO briefs (submitted_by, raw_text, stub_roles) VALUES (?, ?, '[]')",
        (admin_id, role_name),
    )
    brief_id = brief_cur.lastrowid
    task_cur = conn.execute(
        "INSERT INTO tasks (brief_id, role_name, required_skills) VALUES (?, ?, ?)",
        (brief_id, role_name, json.dumps(required_skills, ensure_ascii=False)),
    )
    task_id = task_cur.lastrowid

    result = jobs.create_and_run_job(conn, brief_id, [task_id], admin_id)
    item = result["items"][0]
    succeeded = item["status"] == "succeeded"

    if succeeded:
        reasoning = (
            "규칙기반 스텁이 역할명과 요구 스킬을 그대로 반영해 최소 AgentContract를 "
            "만들었고, 샌드박스 smoke-test를 통과했기 때문에 성공으로 판정했다."
        )
    else:
        reasoning = (
            f"샌드박스 검증에서 실패했기 때문에 실패로 판정했다: {item.get('error')}"
        )

    response = (
        f"역할 '{role_name}'(요구 스킬: {required_skills})에 대해 Agent 생성을 "
        f"시도했다. 결과 상태는 {item['status']}이다. {reasoning}"
    )

    metadata = EvalMetadata(
        completion_score=1.0 if succeeded else 0.0,
        errors=[item["error"]] if item.get("error") else None,
        tool_calls=[{"tool": "sandbox:smoke_test"}],
        extra={
            "role_name": role_name,
            "required_skills": required_skills,
            "job_status": result["status"],
            # 정직하게 표시 — 실제 LLM 생성 결과가 아니라 규칙기반 스텁이다.
            "generator": "default_stub_generator",
        },
    )
    return response, metadata


@agent_eval(
    monitor,
    task_type="planning",
    question_arg="role_name",
    # 결정론적 스텁 + 로컬 샌드박스 실행이라 LLM 파이프라인(수십 초)보다 훨씬
    # 빨라야 정상이다 — SLA를 그 성격에 맞게 좁게 잡는다(AgentForge와 같은 원칙,
    # 근거는 반대: 여기는 "빨라야 정상"이라 임계값을 낮춘다).
    sla=SLAConfig(p95_ms=5000.0, p99_ms=10000.0),
    # Gate G — 스텁 응답에 판단 근거 문장을 넣어뒀다(위 reasoning). 그래도 이건
    # "진짜 추론"이 아니라 템플릿이라는 한계를 위 모듈 docstring에 명시했다.
    explainability=ExplainabilityConfig(require_reasoning=True),
    # Gate B — 이 파이프라인이 실제로 쓰는 도구는 샌드박스 smoke-test 하나뿐이다.
    scope=ScopeConfig(
        allowed_tools=["sandbox:smoke_test"],
        fail_on_violation=False,
        violation_penalty=0.5,
    ),
)
def evaluate_generation(
    role_name: str, required_skills: list[str]
) -> tuple[str, EvalMetadata]:
    """F4 생성 파이프라인 Gate A/B/D/E/G 측정용. 호출하면 실제로 실행된다."""
    return _run_generation(role_name, required_skills)


if __name__ == "__main__":
    # 직접 실행 예시 — 실행 전 `agent-eval check`로 설정을 먼저 확인하는 걸 권장한다.
    evaluate_generation("백엔드 개발자", ["python", "fastapi"])
    evaluate_generation("디자이너", ["figma"])
    print("완료 — results/ 아래 저장된 결과에 `agent-eval gate`를 실행해 채점하세요.")
