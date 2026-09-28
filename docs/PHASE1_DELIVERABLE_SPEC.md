# Phase 1 구현 스펙 — 결과물 엔티티 + 내 업무 진입 흐름 + Skill/Tool 역할 재정의

사용자 요청(2026-09-28, "구현에 들어가기에 충분한 기획과 설계가 갖춰져
있는지 확인해줘" → 갭 지적 후 "바로 작성해줘"). `docs/
COLLABORATION_PLATFORM_PLAN.md` 4-1/4-2/4-3(= `docs/HAIR_MASTER_PLAN.md`
Phase 1)을 실제로 구현 가능한 수준까지 구체화한다 — 정확한 스키마 DDL,
API 계약, RBAC 결정, 골든셋 테스트 목록. 이 문서가 확정하는 내용은 위
두 기획 문서의 서술형 설명을 **대체**하는 게 아니라 **구체화**한다(배경/
근거는 그 문서들 참고).

**범위 밖**: Phase 2(산출물 승인 게이트, `approvals.subject_type=
'deliverable'`)와 Phase 3(Agent 평가, `agent_evaluations`)는 이 문서에
포함하지 않는다 — 단 Phase 2가 나중에 additive하게 얹을 수 있도록 §1의
`deliverables.status`는 미리 그 전체 lifecycle을 CHECK 제약에 넣어둔다
(SQLite는 CHECK 제약을 나중에 ALTER하기 어려우므로).

## 1. 스키마 DDL

`db.py`의 `SCHEMA` 문자열에 아래 두 테이블을 그대로 추가한다(기존
테이블 ALTER 아님 — `tool_pool`/`skill_pool`을 추가했을 때와 같은
패턴, `_migrate_schema()` 불필요).

```sql
CREATE TABLE IF NOT EXISTS deliverables (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL REFERENCES tasks(id),
    author_type TEXT NOT NULL CHECK (author_type IN ('human', 'agent')),
    author_id INTEGER NOT NULL,
    content TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft', 'pending_review', 'approved', 'rejected', 'revised')),
    revises_id INTEGER REFERENCES deliverables(id),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS deliverable_comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    deliverable_id INTEGER NOT NULL REFERENCES deliverables(id),
    author_type TEXT NOT NULL CHECK (author_type IN ('human', 'agent')),
    author_id INTEGER NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_deliverables_task_id ON deliverables(task_id);
CREATE INDEX IF NOT EXISTS idx_deliverable_comments_deliverable_id
    ON deliverable_comments(deliverable_id);
```

**설계 결정(이번에 정리)**:
- 원래 기획서의 `kind(draft|final)`과 `status(draft→pending_review→
  ...)`는 같은 개념의 중복 표현이었다 — `status` 하나로 통합한다.
- `author_id`는 **항상 `users.id`(사람 쪽) 또는 `agent_pool.id`(Agent
  쪽)를 가리킨다** — `human_pool.id`가 아니다. 로그인한 계정이 실제로
  글을 "쓴" 사람이라는 게 감사 추적상 더 정확하고, `audit.record()`가
  이미 이 프로젝트 전체에서 `actor_id=users.id` 관례를 쓰고 있어
  일관된다.
- Phase 1 범위에서 `status`는 **행동에 영향을 주지 않는다** — 사람
  작성 deliverable은 항상 `'draft'`로, Agent 작성 deliverable은 항상
  `'pending_review'`로 애플리케이션 코드가 명시적으로 넣어두기만 하고,
  실제 승인/반려 로직은 Phase 2가 구현한다. 지금 이렇게 채워두는 이유는
  Phase 2가 추가로 컬럼을 만들 필요 없이 바로 값을 읽어 쓸 수 있게
  하기 위함이다.
- `revises_id`는 Phase 1에서는 안 쓴다(Phase 2의 "수정 후 재제출" 흐름을
  위한 자리만 마련). NULL 허용.

**알려진 한계(정직하게 명시)**: `human_pool.user_id`는 nullable이다
("모든 사람이 로그인 계정을 갖는 것은 아님"). **로그인 계정이 없는
Human Pool 리소스는 이 협업 워크스페이스에 전혀 참여할 수 없다** — "내
업무"에도 안 뜨고 deliverable도 못 남긴다. 이 경우 관리자가 그 사람을
대신해 입력하거나, 먼저 로그인 계정을 만들어 연결해야 한다. Phase 1은
이 한계를 그대로 인정하고 넘어간다(범위 밖).

## 2. API 계약

### 2-1. `GET /api/my-work`

RBAC: 인증된 사용자 전부(추가 role 체크 없음 — 쿼리 자체가 "나와 관련된
것"만 돌려주므로 뷰어가 호출해도 자연히 빈 배열이 나온다).

쿼리 파라미터: `limit`(기본 50), `offset`(기본 0) — 기존 감사로그
엔드포인트와 동일한 페이지네이션 관례.

응답(200):
```json
[
  {
    "task_id": 12,
    "brief_id": 4,
    "role_name": "백엔드 개발자",
    "resource_type": "human",
    "resource_id": 7,
    "assignment_id": 33,
    "my_relationship": "assignee",
    "deliverable_count": 2,
    "latest_deliverable_at": "2026-09-28T10:00:00"
  },
  {
    "task_id": 15,
    "brief_id": 4,
    "role_name": "QA 엔지니어",
    "resource_type": "agent",
    "resource_id": 9,
    "assignment_id": 40,
    "my_relationship": "requester",
    "deliverable_count": 0,
    "latest_deliverable_at": null
  }
]
```

판정 조건(SQL 스케치):
```sql
-- assignee(사람 배정 당사자)
SELECT a.task_id, a.id AS assignment_id, 'assignee' AS rel, ...
FROM assignments a
JOIN human_pool hp ON hp.id = a.resource_id AND a.resource_type = 'human'
WHERE a.status = 'confirmed' AND hp.user_id = :current_user_id

UNION ALL

-- requester(Agent 배정 요청자)
SELECT a.task_id, a.id AS assignment_id, 'requester' AS rel, ...
FROM assignments a
WHERE a.status = 'confirmed' AND a.resource_type = 'agent'
  AND a.requested_by = :current_user_id
```

### 2-2. `GET /api/tasks/{task_id}/deliverables`

RBAC: 관리자/매니저는 무조건 조회 가능(감독). 팀원/뷰어는 그 task의
confirmed 배정에서 본인이 assignee 또는 requester일 때만(§2-1과 같은
판정) — 아니면 403 "이 업무의 협업 워크스페이스에 접근할 권한이
없습니다". `require_task_participant(task_id)`라는 신규 FastAPI
dependency로 구현한다(기존 `require_role()`과 나란히 두는 별도
의존성).

응답(200): `created_at` 오름차순(타임라인 순서) 배열.
```json
[
  {"id": 101, "task_id": 12, "author_type": "human", "author_id": 3,
   "content": "...", "status": "draft", "revises_id": null,
   "created_at": "2026-09-28T09:00:00"},
  {"id": 102, "task_id": 12, "author_type": "agent", "author_id": 7,
   "content": "...", "status": "pending_review", "revises_id": null,
   "created_at": "2026-09-28T09:05:00"}
]
```

### 2-3. `POST /api/tasks/{task_id}/deliverables`

RBAC: **그 task의 assignee 또는 requester 본인만**(관리자/매니저도
불가 — 대리 작성은 진짜 작성자를 흐리므로 허용하지 않는다). 위반 시
403.

요청 바디: `{"content": "..."}` (필수, 빈 문자열 거부 → 400).

동작: `author_type='human'`, `author_id=현재 로그인 사용자 id`,
`status='draft'`로 저장. `audit.record()`로 `deliverable_created` 기록.

응답(201): `{"id": 103}`.

### 2-4. `GET /api/deliverables/{id}/comments` · `POST /api/deliverables/{id}/comments`

RBAC: 그 deliverable이 속한 task와 동일한 판정(§2-2) 재사용.

`POST` 바디: `{"content": "..."}`. `author_type`/`author_id`는 로그인
사용자 기준으로 서버가 채운다(`human`으로 고정 — Agent가 댓글을 남기는
경로는 Phase 1에 없음).

### 2-5. `POST /api/tasks/{task_id}/assist` — "LLM으로 도와줘"

RBAC: assignee 또는 requester 본인.

요청 바디: `{"draft_context": "...(선택, 이미 쓰던 메모)"}`.
**`provider`/`model` 필드는 매니저/관리자만 보낼 수 있다 — 팀원이
보내면 400 "팀원은 provider/model을 직접 선택할 수 없습니다"** (§3
RBAC 결정 참고).

동작: 이 task의 `required_skills`와 이름이 일치하는 `skill_pool` 항목의
`description`을 모아 시스템 프롬프트에 주입(§4)하고,
`llm.call_llm()`을 1회 호출한다. **결과는 저장하지 않는다** — 사람이
검토·편집한 뒤 §2-3으로 별도 제출해야 최종 deliverable이 된다.

응답(200): `{"draft": "..."}`. 이 호출은 실제 LLM API를 부르므로 비용이
발생한다(기존 `use_llm`/`simple_llm` 관례와 동일하게 사용자가 명시적
버튼을 눌렀을 때만).

### 2-6. `POST /api/tasks/{task_id}/execute-agent`

RBAC: 그 task의 confirmed agent 배정 requester, 또는 매니저/관리자
(집행 트리거는 대리 허용 — §2-3의 "작성"과 다르게, "이 Agent를 지금
실행시켜라"는 감독자가 대신 눌러도 저자성이 흐려지지 않는다).

요청 바디: `{"provider": null, "model": null}` — §2-5와 동일하게 팀원은
비워야 함(400).

동작:
1. task의 confirmed 배정 중 `resource_type='agent'`인 것을 찾는다 —
   없으면 409 "이 업무에 확정된 AI 에이전트 배정이 없습니다".
2. 그 task의 가장 최근 `author_type='human'` deliverable을 조회해
   있으면 Task 설명에 포함시킨다(없으면 브리프/역할 정보만으로 진행).
3. `crew_runtime.run_crew()`(단일 Agent, sequential)를 이 정보로
   호출한다 — 이미 있는 `resolve_pool_tools()` 도구 그라운딩을 그대로
   탄다(변경 없음).
4. 결과를 `author_type='agent'`, `author_id=<agent_id>`,
   `status='pending_review'`로 저장.

응답(201): `{"id": 104}`.

### 2-7. 에러 응답 정리

| 상황 | 코드 | 메시지 |
|---|---|---|
| task 없음 | 404 | "할 일을 찾을 수 없습니다" |
| 참가자 아님(§2-2/2-3/2-5 계열) | 403 | "이 업무의 협업 워크스페이스에 접근할 권한이 없습니다" |
| 팀원이 provider/model 지정 | 400 | "팀원은 provider/model을 직접 선택할 수 없습니다" |
| content 빈 문자열 | 400 | "내용을 입력해야 합니다" |
| execute-agent인데 확정 Agent 배정 없음 | 409 | "이 업무에 확정된 AI 에이전트 배정이 없습니다" |

## 3. RBAC 결정(확정)

`HAIR_MASTER_PLAN.md` §8 결정사항 1번("팀원이 provider/model 자유
선택 가능 범위")을 여기서 확정한다:

**팀원(Member)은 LLM 보조(§2-5)·Agent 실행(§2-6)을 직접 트리거할 수
있지만, provider/model은 선택할 수 없다** — 항상 시스템 기본값(전역
기본 provider/model, 또는 execute-agent의 경우 그 Agent에 이미 설정된
`llm_provider`/`llm_model`)을 쓴다. 매니저/관리자는 기존 "모델 설정"
관례와 동일하게 자유롭게 선택 가능하다.

**근거**: 이번에 팀원에게 처음으로 "직접 실행" 권한을 주는 것이므로,
비용 통제 축은 한 단계 더 좁혀 잡는다 — 기존에도 `use_llm`은 항상
명시적 opt-in이었고(암묵적 비용 발생 금지 원칙), provider/model
자유선택까지 팀원에게 주면 관리자가 모르는 새 특정 모델(예: 고가
모델)로 비용이 튈 수 있다. 매니저/관리자가 사전에 설정해둔 기본값
안에서만 팀원이 실행하게 하는 게 안전하다.

## 4. Skill 자동 주입 로직

§2-5(LLM 보조)의 시스템 프롬프트 조립 방식:

```python
def _skill_grounded_system_prompt(conn, role_name, required_skills):
    rows = conn.execute(
        f"SELECT description FROM skill_pool WHERE name IN "
        f"({','.join('?' * len(required_skills))})",
        required_skills,
    ).fetchall() if required_skills else []
    knowledge = "\n".join(f"- {r['description']}" for r in rows if r["description"])
    base = llm.system_prompt_for_role(role_name, required_skills)  # 기존 함수 재사용
    if knowledge:
        return base + "\n\n참고 지식:\n" + knowledge
    return base
```

기존 `llm.system_prompt_for_role()`(F4 `simple_llm` 생성 경로가 이미
씀)을 그대로 재사용하고, 매칭된 Skill 지식을 뒤에 덧붙이기만 한다 —
새 프롬프트 조립 함수를 처음부터 만들지 않는다. `required_skills`가
비어 있으면 지식 주입 없이 기존 동작과 동일하다(회귀 없음).

## 5. 골든셋 테스트 목록 (GC127–137, `tests/unit/test_phase17_collab_workspace.py`)

| GC | 검증 내용 |
|---|---|
| GC127 | `GET /api/my-work` — 사람이 assignee인 confirmed 배정의 task가 `my_relationship: "assignee"`로 반환됨 |
| GC128 | `GET /api/my-work` — Agent가 배정된 task는 그 배정의 requester에게 `my_relationship: "requester"`로 반환됨 |
| GC129 | `GET /api/my-work` — `requested`/`superseded` 상태 배정은 반환되지 않음(confirmed만) |
| GC130 | `POST .../deliverables`로 저장한 내용이 `GET .../deliverables`에 `author_type='human'`, `status='draft'`로 시간순으로 나타남 |
| GC131 | 참가자가 아닌 팀원이 `GET/POST .../deliverables` 호출 시 403 |
| GC132 | 관리자/매니저는 참가자가 아니어도 `GET .../deliverables` 조회 가능(감독), 그러나 `POST`(작성)는 403(대리 작성 금지) |
| GC133 | `POST .../assist` 호출 시 `required_skills`와 이름이 일치하는 `skill_pool.description`이 (mock한) `llm.call_llm()`의 system 인자에 포함됨 |
| GC134 | 팀원이 `POST .../assist`에 `provider`를 넣으면 400, 매니저가 넣으면 200 |
| GC135 | `POST .../execute-agent` — 확정된 human deliverable이 있으면 Task context에 포함되고, 결과가 `author_type='agent'`, `status='pending_review'`로 저장됨 |
| GC136 | `POST .../execute-agent` — 그 task에 confirmed agent 배정이 없으면 409 |
| GC137 | `POST/GET .../comments` — deliverable 댓글이 저장·조회되고, 참가자 판정은 부모 task 기준으로 동일하게 적용됨 |

## 6. 마이그레이션

- `deliverables`/`deliverable_comments`는 신규 테이블이라 `db.py`의
  `SCHEMA` 문자열에 `CREATE TABLE IF NOT EXISTS`만 추가하면 된다 —
  `_migrate_schema()`(기존 테이블에 컬럼 추가) 대상이 아니다.
- 기존 `job_items`/`tasks`/`assignments` 데이터는 전혀 건드리지
  않는다 — Phase 1은 순수 추가(additive)이고 과거 데이터를 소급
  채우지 않는다(과거에 confirmed된 배정도 "내 업무"에 정상적으로
  나타난다 — `assignments.status` 컬럼은 이미 있으므로 새 컬럼 백필이
  필요 없다).

## 7. Phase 1에서 하지 않는 것 (스코프 재확인)

- deliverable 승인/반려(Phase 2) — `status` 값은 채워두지만 행동은 없음.
- Agent 자동 평가(Phase 3) — 없음.
- Task 단위/도구 호출 단위 일시정지(Phase 4) — `execute-agent`는 여전히
  동기 호출이다(HTTP 요청 안에서 끝까지 실행 후 반환).
- Skill Pool 지식 본문 필드 확장(`content`/`content_ref`) — 이번엔
  기존 `description` 필드만 쓴다(`HAIR_MASTER_PLAN.md` §8 결정사항
  2번은 미결로 남겨둔다 — 지식이 짧아도 §4는 그대로 동작하므로 Phase 1
  착수를 막지 않는다).
- Agent의 skill_tags→Tool 자동 부착 마이그레이션(별도 ADR 필요, §협업
  플랜 4-3) — Phase 1은 기존 메커니즘을 그대로 둔 채 진행한다.
