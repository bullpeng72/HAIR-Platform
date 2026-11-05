"""업무 요청(Brief)을 독립 검증 가능한 할 일(Task) 묶음으로 쪼개도록
LLM이 제안하는 선택적 보조 기능 — 사용자 요청(2026-11-04, "업무 배정,
파이프라인, crew 팀 구성 등을 이용해 로드러너 수준의 게임... 개선
계획을 수립해줘" → "1번(업무 분해 단위 가이드)... 설계 들어가줘").

실시간 데모(park_member 계정, 로드러너 게임 웹 구현)에서 복잡한 업무를
통째로 Task 하나로 만들어 한 Agent에게 맡겼다가, Crew 재시도를 거듭할
때마다 이전에 되던 기능이 조용히 퇴행하는 문제를 겪었다 — 애초에
"맵 엔진/플레이어 물리/적 AI"처럼 독립 검증 가능한 단위로 쪼갰어야
했는데, `POST /api/briefs/{id}/tasks`는 100% 수동 입력이라 그런 분해를
유도하는 장치가 전혀 없었다.

`match_llm_judge.judge_candidates()`/`agent_optimizer.refine_with_llm()`과
같은 fail-safe 계약: `POST /api/briefs/{id}/decompose-suggest`(상위
`app.py`)가 명시적으로 호출했을 때만 동작하고, API 에러·JSON 파싱
실패·존재하지 않는 참조 등 어떤 이유로든 실패하면 절대 예외를 올리지
않고 빈 제안(`{"tasks": []}`)으로 안전하게 되돌아간다 — 이 함수는 DB에
아무것도 쓰지 않는다(admin이 검토·수정 후 기존 decompose 엔드포인트로
직접 제출해야 실제로 생성됨).

`task_dependencies`(`db.py`)는 이미 있는 표시용 기능이라(강제 차단
없음 — `dashboard.bottlenecks()` 경고용) 여기서 제안하는
`depends_on_index`도 어디까지나 제안일 뿐, 생성 시점에 admin이 자유롭게
바꿔서 제출할 수 있다."""

from __future__ import annotations

import json
import re
import sqlite3

from hair_platform import llm

_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)

_SYSTEM_PROMPT = (
    "당신은 업무 분해 보좌관입니다. 주어진 업무 요청(Brief) 원문을 읽고, "
    "독립적으로 검증 가능한 할 일(Task) 단위로 쪼개 제안하세요. "
    "**중요**: 복잡한 작업(예: 게임·앱 전체 구현) 하나를 한 Agent에게 "
    "통째로 맡기면, 나중에 일부를 수정하다가 이미 잘 동작하던 다른 "
    "부분이 조용히 망가지는 일이 흔합니다 — 그러니 가능하면 \"맵/엔진\", "
    "\"플레이어 로직\", \"적 AI\", \"UI/점수\"처럼 서로 독립적으로 만들고 "
    "검증할 수 있는 단위로 나누고, 순서가 있는 경우 선행 작업을 "
    "지정하세요(예: \"통합\"은 다른 모듈들이 끝난 뒤에 와야 함). "
    "너무 잘게 쪼개 1인분이 안 되는 단위로 나누지는 마세요. "
    "반드시 JSON 객체 하나만 출력하세요: "
    '{"tasks": [{"role_name": "...", "required_skills": ["..."], '
    '"depends_on_index": int|null}]}. '
    "depends_on_index는 이 응답의 tasks 배열 안에서 그 작업보다 먼저 "
    "와야 하는 항목의 0부터 시작하는 인덱스입니다(선행 작업이 없으면 "
    "null)."
)


def suggest_decomposition(conn: sqlite3.Connection, brief_id: int) -> dict:
    """`{"tasks": [{"role_name", "required_skills", "depends_on_index"}]}`를
    반환한다. DB에 아무것도 쓰지 않는다 — 순수 제안.

    `brief_id`가 없으면 `ValueError("brief_not_found")`(호출부가
    404로 변환) — 이건 명백한 호출 실수라 안전 폴백 대상이 아니다.
    그 외의 모든 실패(LLM API 에러, JSON 파싱 실패, 검증 실패)는
    `{"tasks": []}`로 조용히 폴백한다."""
    brief = conn.execute(
        "SELECT * FROM briefs WHERE id = ?", (brief_id,)
    ).fetchone()
    if brief is None:
        raise ValueError("brief_not_found")

    try:
        provider, model = llm.resolve_provider_and_model(None, None, conn=conn)
        prompt = f"업무 요청 원문:\n{brief['raw_text']}"
        raw = llm.call_llm(provider, model, _SYSTEM_PROMPT, prompt)
        match = _JSON_BLOCK_RE.search(raw)
        if not match:
            raise ValueError("no_json_block")
        parsed = json.loads(match.group(0))
        raw_items = parsed.get("tasks", [])
        if not isinstance(raw_items, list):
            raise ValueError("tasks_not_a_list")

        return {"tasks": _validate_and_remap(raw_items)}
    except Exception:  # noqa: BLE001 — 절대 raise하지 않는다, 항상 빈 제안으로 폴백
        return {"tasks": []}


def _validate_and_remap(raw_items: list) -> list[dict]:
    """환각/불량 항목을 제거하면 리스트 위치가 밀리므로, `depends_on_index`는
    "원래 인덱스 → 생존 후 새 인덱스" 맵을 만들어 2단계로 재매핑해야 한다 —
    단순 `len(raw_items)` 범위 체크만으로는 제거 이후의 인덱스 어긋남을
    못 잡는다."""
    survivors: list[tuple[int, dict]] = []  # (원래 인덱스, 정제된 항목)
    for i, raw in enumerate(raw_items):
        if not isinstance(raw, dict):
            continue
        role_name = raw.get("role_name")
        if not isinstance(role_name, str) or not role_name.strip():
            continue
        required_skills = raw.get("required_skills", [])
        if not isinstance(required_skills, list) or not all(
            isinstance(s, str) for s in required_skills
        ):
            required_skills = []
        depends_on_raw = raw.get("depends_on_index")
        depends_on_raw = depends_on_raw if isinstance(depends_on_raw, int) else None
        survivors.append(
            (
                i,
                {
                    "role_name": role_name.strip(),
                    "required_skills": required_skills,
                    "depends_on_index": depends_on_raw,  # 아직 원래 인덱스 기준 — 아래서 재매핑
                },
            )
        )

    old_to_new = {old_i: new_i for new_i, (old_i, _) in enumerate(survivors)}

    result: list[dict] = []
    for new_i, (_old_i, item) in enumerate(survivors):
        dep = item["depends_on_index"]
        remapped = old_to_new.get(dep) if dep is not None else None
        if remapped == new_i:  # 자기참조는 그대로 두면 안 됨
            remapped = None
        item["depends_on_index"] = remapped
        result.append(item)
    return result
