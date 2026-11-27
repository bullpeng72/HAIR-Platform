"""DB 일회성 마이그레이션·데이터 보정(`db.py`에서 분리 — 2026-11-27, 개선 제안 6-2: 3,674줄 한 파일).

`db.init_schema()`가 부른다. 함수 이름은 `db`에서도 그대로 쓸 수 있다(예전 호출부·테스트 호환).
"""

from __future__ import annotations

import json
import re
import sqlite3

from hair_platform import reference_pipeline_seeds

def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(row["name"] == column for row in rows)


def _ensure_column(
    conn: sqlite3.Connection, table: str, column: str, coltype_with_default: str
) -> None:
    if not _column_exists(conn, table, column):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype_with_default}")


def _migrate_tool_pool_custom_python_kind(conn: sqlite3.Connection) -> None:
    """사용자 요청(2026-10-13, "@데코레이터 툴을 추가 생성할 수 있는 기능")
    — `tool_pool.kind`는 컬럼이 아니라 **CHECK 제약**으로 허용값을
    제한한다(`'mcp_stdio', 'mcp_remote', 'native'`). SQLite는 기존 테이블의
    CHECK 제약을 `ALTER TABLE`로 못 바꾼다(`deliverables.status`를 못 넓혀
    `agent_execution_jobs`를 새로 만들었던 것과 같은 한계) — 여기선 컬럼이
    아니라 제약 자체를 넓혀야 해서 테이블을 통째로 재생성하는 수밖에 없다.

    **실측으로 발견한 버그**: 이 마이그레이션 없이 `kind='custom_python'`
    으로 INSERT하면 CHECK 제약 위반으로 `sqlite3.IntegrityError`가 나는데,
    `app.py`의 예외 처리가 그 예외를 전부 "이미 있는 이름입니다"(이름
    UNIQUE 위반 전용 메시지)로 오분류해 보여준다 — 원인이 완전히 다른데
    사용자에게 거짓 사유를 보여주는 상태였다(직접 작성한 테스트로 재현
    확인)."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'tool_pool'"
    ).fetchone()
    if row is None or row["sql"] is None or "custom_python" in row["sql"]:
        return  # 테이블이 아직 없거나(신규 DB — CREATE TABLE에 이미 포함) 이미 마이그레이션됨.
    # 실측으로 발견한 버그 — `agent_tool_links.tool_pool_id`가 이 테이블을
    # FK로 참조한다(`PRAGMA foreign_keys=ON`이 전역으로 걸려 있다, `db.py`
    # `get_connection()` 참고). FK가 켜진 채로 참조되는 테이블을 DROP하면
    # `sqlite3.IntegrityError: FOREIGN KEY constraint failed`가 난다 —
    # 이 마이그레이션 구간만 꺼둔다(SQLite 공식 문서가 테이블 재생성
    # 마이그레이션의 표준 절차로 권장하는 방식).
    # 실측으로 발견한 두 번째 버그 — `PRAGMA foreign_keys`는 SQLite
    # 공식 문서에 "트랜잭션 도중엔 no-op"이라고 명시돼 있다. 앞/뒤에
    # `commit()`으로 트랜잭션을 확실히 비우지 않으면 OFF/ON 토글 자체가
    # 조용히 무시돼, 마이그레이션은 성공해도 그 뒤로 FK 강제가 영영
    # 꺼진 채로 남는다(직접 작성한 재현 스크립트로 확인).
    conn.commit()
    conn.execute("PRAGMA foreign_keys=OFF")
    try:
        # 실측으로 발견한 세 번째 버그 — 이 마이그레이션이 DROP TABLE
        # 단계에서 실패하면(예: 위 FK 버그가 고쳐지기 전 실제 서버에서
        # 한 번 터졌던 경우) `tool_pool_new`가 고아 테이블로 남아
        # 다음 재시작 때 "table already exists"로 또 실패한다 —
        # 재시도가 항상 깨끗하게 시작되도록 먼저 치운다.
        conn.execute("DROP TABLE IF EXISTS tool_pool_new")
        conn.execute("""
            CREATE TABLE tool_pool_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                description TEXT NOT NULL DEFAULT '',
                kind TEXT NOT NULL CHECK (kind IN ('mcp_stdio', 'mcp_remote', 'native', 'custom_python')),
                config TEXT NOT NULL DEFAULT '{}',
                tool_names TEXT NOT NULL DEFAULT '[]',
                is_active INTEGER NOT NULL DEFAULT 1
            )
            """)
        conn.execute("INSERT INTO tool_pool_new SELECT * FROM tool_pool")
        conn.execute("DROP TABLE tool_pool")
        conn.execute("ALTER TABLE tool_pool_new RENAME TO tool_pool")
        conn.commit()
    finally:
        conn.execute("PRAGMA foreign_keys=ON")


def _backfill_conversation_last_read(conn: sqlite3.Connection) -> None:
    """`conversation_participants.last_read_message_id`가 막 추가된
    뒤(위 `_ensure_column`) 전부 `NULL`인 채로 두면, "읽은 적 없음"으로
    해석돼 그 참가자가 참여 중인 대화의 **모든 과거 메시지**가 "안 읽은
    멘션"으로 한꺼번에 몰려온다 — 몇 달치 대화를 이미 다 본 사람에게
    로그인 한 번에 수십 건씩 브라우저 알림이 쏟아지는 셈이다.

    완벽한 과거 "읽음" 시점을 되살릴 방법은 없지만(그런 기록을 애초에
    안 남겼다), 합리적인 근사치를 쓴다 — **이 대화에 직접 메시지를 보낸
    적이 있는 참가자는, 그 자신의 마지막 메시지 시점까지는 적극적으로
    읽고 있었다고 보고 거기까지 읽은 것으로 표시한다.**

    실측으로 바로잡은 지점(2026-10-14, 사용자가 직접 재현해 보고한
    "호출이 안 되는 거 같아") — 처음엔 "지금 시점(=백필 실행 시점)까지
    전부 읽음"으로 더 단순하게 짰었다. 그런데 실데이터로 검증하다가
    바로 이 버그를 만났다: pm_kim이 과거에 이 대화에서 한 번 말한 적은
    있었지만(그래서 "말한 적 없는 참가자"엔 안 걸림), 그 **이후에** 새로
    온 "@pm_kim" 멘션 3건까지 전부 "이미 읽음"으로 뭉개버렸다 — 참가자의
    마지막 활동 이후에 온 멘션은 당연히 본 적이 없는데 "지금 시점"을
    기준으로 삼은 게 원인이었다. 그래서 "지금 시점"이 아니라 "**그 참가자
    자신의 가장 최근 메시지 id**"로 바꿨다 — 그 이후에 온 멘션은(설령
    백필이 뒤늦게 돌아도) 여전히 안 읽음으로 남는다.

    메시지를 한 번도 안 보낸 참가자(초대만 되고 아직 대화를 열어본 적
    없을 가능성이 높음)는 `NULL`로 남겨 "전부 안 읽음" 그대로 둔다.

    `WHERE last_read_message_id IS NULL` 조건 덕에 매 기동 재실행해도
    안전하다(이미 값이 있는 행은 다시 안 건드림 — 한 번만 적용되는
    1회성 백필)."""
    conn.execute("""
        UPDATE conversation_participants
        SET last_read_message_id = (
            SELECT MAX(cm.id) FROM conversation_messages cm
            WHERE cm.conversation_id = conversation_participants.conversation_id
              AND cm.author_type = 'human'
              AND cm.author_id = conversation_participants.participant_id
        )
        WHERE last_read_message_id IS NULL
          AND participant_type = 'human'
          AND EXISTS (
              SELECT 1 FROM conversation_messages cm2
              WHERE cm2.conversation_id = conversation_participants.conversation_id
                AND cm2.author_type = 'human'
                AND cm2.author_id = conversation_participants.participant_id
          )
        """)
    conn.commit()


# 사용자 요청(2026-11-20, "skill 이름에서 '가이드'를 제거해줘") — 2026-11-11
# 한글화 때 붙은 "~가이드" 접미사를 뗀다. 시드 코드(`_seed_default_pools`/
# `reference_pipeline_seeds`)는 이미 새 이름을 쓰므로, 이 함수는 그 이전에
# 만들어진 DB만 맞춘다 — 반드시 시드보다 먼저 돌아야 한다(안 그러면 시드가
# 새 이름 행을 먼저 만들어 UNIQUE 충돌/중복 스킬이 생긴다).
_GUIDE_SUFFIX_SKILL_RENAMES: tuple[tuple[str, str], ...] = tuple(
    (old, old.removesuffix("가이드"))
    for old in (
        "문서작성가이드",
        "기획서작성가이드",
        "아키텍처설계가이드",
        "데이터모델설계가이드",
        "프론트엔드구현가이드",
        "UIUX기획가이드",
        "테스트계획설계가이드",
        "품질인수기준가이드",
        "데이터인프라구현가이드",
        "데이터분석가이드",
        "제안서작성가이드",
        "실적서작성가이드",
        "스킬생성가이드",
        "데이터시각화가이드",
    )
)


# 사용자 요청(2026-11-20, "스킬명을 변경해줘 지식검색 -> 내부지식검색,
# 관계조회도 기능에 맞춰 오해없도록 스킬명 변경") — "지식검색"은 외부 웹이
# 아니라 플랫폼 지식베이스(VectorDB)만 찾는다는 점을, "관계조회"는 실제로
# 조직도(상사/부하)와 업무 배정 현황만 조회한다는 점(문서 내용 검색이나
# 일반적인 "관계 분석"이 아님 — `graphquery.py`)을 이름에 드러낸다.
_FUNCTIONAL_SKILL_RENAMES: tuple[tuple[str, str], ...] = (
    ("지식검색", "내부지식검색"),
    ("관계조회", "조직배정관계조회"),
)

# 이름과 함께 바뀌는 설명 — 기존 DB의 설명이 예전 기본 문구 그대로일 때만
# 새 문구로 바꾼다(admin이 직접 고친 설명은 건드리지 않는다).
_FUNCTIONAL_SKILL_DESCRIPTIONS: dict[str, tuple[str, str]] = {
    "내부지식검색": (
        "VectorDB 기반 지식검색 역량",
        (
            "플랫폼 지식베이스(admin 직접 등록·결재 등록 자료)를 VectorDB로 검색해 "
            "출처와 함께 답하는 역량 — 외부 웹은 검색하지 않는다"
        ),
    ),
    "조직배정관계조회": (
        "GraphDB 기반 관계분석 역량",
        (
            "조직도(상사·부하)와 업무 배정 현황(어느 업무를 누가 맡았는지)을 조회하는 "
            "역량 — 문서·지식 내용 검색이 아니다"
        ),
    ),
}


def _rename_guide_suffixed_skills(conn: sqlite3.Connection) -> None:
    """`skill_pool.name`은 Task/Agent/사람의 스킬 태그와 대화 메시지의
    `used_skill`이 문자열로 그대로 참조한다 — 이름만 바꾸면 그 연결이
    조용히 끊기므로 같이 바꾼다. 새 이름 행이 이미 있으면(비정상 상태)
    태그만 새 이름으로 합치고 옛 행을 지운다. 대상이 없으면 0건(멱등).

    "~가이드" 접미사 제거(`_GUIDE_SUFFIX_SKILL_RENAMES`)와 기능 기준 개명
    (`_FUNCTIONAL_SKILL_RENAMES`) 둘 다 이 함수 하나로 처리한다."""
    old_name_of = {new: old for old, new in _FUNCTIONAL_SKILL_RENAMES}
    for new_name, (old_desc, new_desc) in _FUNCTIONAL_SKILL_DESCRIPTIONS.items():
        old_name = old_name_of[new_name]
        conn.execute(
            "UPDATE skill_pool SET description = ? WHERE name IN (?, ?) AND description = ?",
            (new_desc, old_name, new_name, old_desc),
        )
    for old_name, new_name in _GUIDE_SUFFIX_SKILL_RENAMES + _FUNCTIONAL_SKILL_RENAMES:
        old_row = conn.execute(
            "SELECT id FROM skill_pool WHERE name = ?", (old_name,)
        ).fetchone()
        if old_row is None:
            continue
        new_exists = conn.execute(
            "SELECT 1 FROM skill_pool WHERE name = ?", (new_name,)
        ).fetchone()
        if new_exists:
            conn.execute("DELETE FROM skill_pool WHERE id = ?", (old_row["id"],))
        else:
            conn.execute(
                "UPDATE skill_pool SET name = ? WHERE id = ?", (new_name, old_row["id"])
            )
        for table, column in (
            ("tasks", "required_skills"),
            ("agent_pool", "skill_tags"),
            ("human_pool", "skill_tags"),
        ):
            for row in conn.execute(f"SELECT id, {column} FROM {table}").fetchall():
                tags = json.loads(row[column] or "[]")
                if old_name in tags:
                    renamed: list[str] = []
                    for t in tags:
                        t = new_name if t == old_name else t
                        if t not in renamed:
                            renamed.append(t)
                    conn.execute(
                        f"UPDATE {table} SET {column} = ? WHERE id = ?",
                        (json.dumps(renamed, ensure_ascii=False), row["id"]),
                    )
        conn.execute(
            "UPDATE conversation_messages SET used_skill = ? WHERE used_skill = ?",
            (new_name, old_name),
        )


# 대화 컨텍스트 정책 기본값(2026-11-20 사용자 승인안). 나머지 스킬은 컬럼
# 기본값('full' + 첨부 사용 — 기존 동작 그대로)을 쓴다.
_SKILL_CONTEXT_POLICY_DEFAULTS: dict[str, tuple[str, int]] = {
    # 지식베이스만 근거로 답해야 한다 — 이전 답변이 섞이면 출처가 흐려진다.
    "내부지식검색": ("none", 0),
    # 웹검색은 "그거 더 찾아줘" 같은 후속 질문만 대응하면 된다.
    "실시간웹검색": ("recent", 0),
    "일반웹검색": ("recent", 0),
}


def _backfill_skill_context_policy(conn: sqlite3.Connection) -> None:
    """`skill_pool.history_mode` 컬럼이 처음 생길 때 한 번만 — 이후 admin이
    스킬 관리 화면에서 바꾼 값을 재기동이 덮어쓰지 않게 한다(`_backfill_
    skill_structured_fields()`와 같은 원칙)."""
    for name, (history_mode, use_attachments) in _SKILL_CONTEXT_POLICY_DEFAULTS.items():
        conn.execute(
            "UPDATE skill_pool SET history_mode = ?, use_attachments = ? WHERE name = ?",
            (history_mode, use_attachments, name),
        )
    conn.commit()


_OLD_STAGE_MODE_CHECK = "CHECK (mode IN ('single', 'sequential_relay', 'parallel_delegated'))"
_NEW_STAGE_MODE_CHECK = "CHECK (mode IN ('single', 'sequential_relay', 'parallel_delegated', 'plan_merge'))"


def _migrate_parallel_delegated_to_plan_merge(conn: sqlite3.Connection) -> int:
    """병행 위임 단계를 계획 분담으로(2026-11-26, P1-7 4단계 — 사용자 결정 "계획 분담으로 자동 전환"). 같은 리드·같은 팀원이라
    가장 가까운 방식이다(리드가 나누고 팀원이 각자 맡은 뒤 리드가 합침 — CrewAI 위임 도구 대신 명시적 계획). 리드가 비어 있으면
    첫 참여 역할을 리드로. admin이 있으면 활동 기록을 남긴다. 다시 돌려도 아무 일 없다."""
    rows = conn.execute(
        "SELECT s.id, s.name, s.lead_role_name, t.name AS template FROM pipeline_stages s "
        "JOIN pipeline_templates t ON t.id = s.pipeline_template_id WHERE s.mode = 'parallel_delegated'"
    ).fetchall()
    for r in rows:
        lead = r["lead_role_name"] or (conn.execute(
            "SELECT role_name FROM pipeline_stage_agents WHERE stage_id = ? ORDER BY seq_order LIMIT 1", (r["id"],)
        ).fetchone() or [None])[0]
        conn.execute("UPDATE pipeline_stages SET mode = 'plan_merge', lead_role_name = ? WHERE id = ?", (lead, r["id"]))
    if rows:
        admin = conn.execute("SELECT id FROM users WHERE role = 'admin' ORDER BY id LIMIT 1").fetchone()
        if admin is not None:
            from hair_platform import audit

            audit.record(conn, admin[0], "pipeline_stage_mode_migrated", "pipeline_stages", None,
                         after={"from": "parallel_delegated", "to": "plan_merge",
                                "stages": [f"{r['template']}:{r['name']}" for r in rows]})
        conn.commit()
    return len(rows)


def _migrate_pipeline_stage_plan_merge_mode(conn: sqlite3.Connection) -> None:
    """`pipeline_stages.mode` CHECK에 'plan_merge'(계획 분담 — 2026-11-23, P2-9 `plan_merge.py`)를
    더한다. SQLite는 CHECK를 ALTER로 못 바꿔 테이블을 다시 만든다(`_migrate_tool_pool_custom_
    python_kind()`와 같은 방식) — 옛 CHECK가 그대로 있을 때만. 행·id는 그대로 옮기고 인덱스도
    다시 만든다. 자식 테이블(단계 참여자·실행 단계)은 테이블 이름으로 참조하므로 그대로 유효하다."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'pipeline_stages'"
    ).fetchone()
    if row is None or _OLD_STAGE_MODE_CHECK not in row["sql"]:
        return
    # 한 번 ALTER RENAME된 테이블은 sqlite_master에 `CREATE TABLE "pipeline_stages"`(따옴표)로 남는다.
    new_sql = re.sub(
        r'^CREATE TABLE\s+["`\[]?pipeline_stages["`\]]?',
        "CREATE TABLE pipeline_stages__new",
        row["sql"].replace(_OLD_STAGE_MODE_CHECK, _NEW_STAGE_MODE_CHECK),
        count=1,
    )
    if "pipeline_stages__new" not in new_sql:
        raise RuntimeError("pipeline_stages_migration_unexpected_sql")
    indexes = [
        r["sql"]
        for r in conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'index' AND tbl_name = 'pipeline_stages' "
            "AND sql IS NOT NULL"
        )
    ]
    conn.commit()
    conn.execute("PRAGMA foreign_keys=OFF")
    try:
        conn.execute("BEGIN")
        conn.execute(new_sql)
        conn.execute("INSERT INTO pipeline_stages__new SELECT * FROM pipeline_stages")
        conn.execute("DROP TABLE pipeline_stages")
        conn.execute("ALTER TABLE pipeline_stages__new RENAME TO pipeline_stages")
        for sql in indexes:
            conn.execute(sql)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.execute("PRAGMA foreign_keys=ON")


def _migrate_schema(conn: sqlite3.Connection) -> None:
    """`CREATE TABLE IF NOT EXISTS`는 이미 존재하는 테이블에 새 컬럼을 추가하지
    못한다 — 이 저장소가 커오면서 `agent_pool`에 여러 번 컬럼을 추가했는데,
    이미 만들어진 DB 파일을 쓰는 배포(이 세션의 데모 서버 포함)에서는 이
    마이그레이션 없이 컬럼이 영영 안 생겨 `SELECT *` 결과에 없는 키에 접근할
    때 500 에러가 실제로 발생했다("AI 에이전트 관리"·"Crew 실행" 메뉴)."""
    _ensure_column(conn, "agent_pool", "llm_provider", "TEXT")
    _ensure_column(conn, "agent_pool", "llm_model", "TEXT")
    _ensure_column(conn, "agent_pool", "last_output", "TEXT")
    _ensure_column(conn, "agent_pool", "framework", "TEXT NOT NULL DEFAULT 'stub'")
    # 사용자 요청(2026-09-29, "사용자 삭제 기능 필요") — users.id는
    # audit_log/assignments/approvals/human_pool.user_id 등에서 NOT NULL
    # FK로 참조되므로 하드 삭제는 감사 이력·배정 이력을 깨뜨린다(append-only
    # 원칙과 정면 충돌). Human/Agent Pool이 이미 쓰는
    # "오프보딩"(레코드는 남기고 비활성화) 패턴을 계정에도 그대로
    # 적용한다.
    _ensure_column(conn, "users", "is_active", "INTEGER NOT NULL DEFAULT 1")
    _ensure_column(conn, "agent_pool", "mcp_servers", "TEXT NOT NULL DEFAULT '[]'")
    # 사용자 요청(2026-09-29, "Tool, Skill을 적극 활용하는 시나리오를
    # 기획해 진행해줘") 라이브 실행에서 발견한 갭 — VectorDB/GraphDB
    # 그라운딩이 실제로 프롬프트에 뭘 주입했는지 사후에
    # 확인할 방법이 어디에도 없었다(결재자가 "이 답변이 실제 근거에
    # 기반했는지" 검증 불가). 실행 job에 근거를 같이 남긴다.
    _ensure_column(conn, "agent_execution_jobs", "retrieved_context", "TEXT")
    # 사용자 요청(2026-10-06, "필요 스킬" 자동완성에 "websearch"/"외부검색"이
    # 완전히 같은 뜻(둘 다 web_search native 도구)인데 둘 다 떠 중복으로
    # 보인다는 리포트 → "하나만 표시되도록 개선") — 두 별칭 중 "외부검색"을
    # 정식 이름으로 남기고 "websearch"는 skill_pool에서 제거한다. 같은
    # 요청으로 "vectordb"/"내부지식검색"(둘 다 vectordb_search), "조직배정관계조회"/"관계질의"
    # (둘 다 graphdb_query)도 같은 방식으로 통합한다 — "내부지식검색"/"조직배정관계조회"는
    # 실제 테스트·데이터에서 더 많이 쓰이던 쪽. 이미
    # 만들어진 DB에 남아있는 제거 대상 행과, 그 이름을 쓰던
    # tasks.required_skills/agent_pool.skill_tags의 값도 남기는 쪽 이름으로
    # 맞춰야 매칭이 끊기지 않는다 — `_seed_default_pools`의 `INSERT OR
    # IGNORE`만으로는 이미 심어진 행을 지우지 못하므로 여기서 명시적으로
    # 정리한다(멱등 — 제거 대상 행이 없으면 조용히 0건).
    for removed_name, kept_name in (
        ("websearch", "외부검색"),
        ("vectordb", "내부지식검색"),
        ("관계질의", "조직배정관계조회"),
    ):
        removed_row = conn.execute(
            "SELECT id FROM skill_pool WHERE name = ?", (removed_name,)
        ).fetchone()
        if removed_row is None:
            continue
        for table, column in (
            ("tasks", "required_skills"),
            ("agent_pool", "skill_tags"),
        ):
            for row in conn.execute(f"SELECT id, {column} FROM {table}").fetchall():
                tags = json.loads(row[column] or "[]")
                if removed_name in tags:
                    tags = [kept_name if t == removed_name else t for t in tags]
                    conn.execute(
                        f"UPDATE {table} SET {column} = ? WHERE id = ?",
                        (json.dumps(tags, ensure_ascii=False), row["id"]),
                    )
        conn.execute("DELETE FROM skill_pool WHERE id = ?", (removed_row["id"],))

    # 사용자 요청(2026-11-11, "가능하면 Skill 이름은 한국어로 해줘") —
    # HAIR-Platform이 직접 지은 영어 스킬 이름 30개를 한글로 바꾼다.
    # agent-evaluator/Harness Methodology 생태계를 그대로 미러링한 18개
    # (harness-gate-ci 등, `_SW_DEV_CATEGORY_SKILL_NAMES` 중 일부)는
    # 실제 외부 Claude Skills 이름과 1:1 대응이 깨지면 안 돼 제외한다
    # (사용자 확인 완료). 위 "vectordb"/"관계질의" 통합과 같은 이유로
    # `skill_pool.name`은 `tasks.required_skills`/`agent_pool.
    # skill_tags`가 문자열로 그대로 참조하므로, 이름만 바꾸고 그 두
    # 컬럼을 같이 안 바꾸면 이미 만들어진 Task/Agent의 연결이 조용히
    # 끊어진다 — 같은 트랜잭션에서 함께 바꾼다. `UPDATE ... WHERE name
    # = ?`는 대상이 없으면 0행이라 매 기동마다 불러도 안전하다(멱등).
    for old_name, new_name in (
        ("rag", "내부지식검색"),
        ("graphdb", "조직배정관계조회"),
        ("spec-ears-lint", "요구사항검증"),
        ("failure-mode-derivation", "실패모드도출"),
        ("golden-set-coverage-check", "커버리지체크"),
        ("adr-template", "설계결정기록"),
        ("code-review-checklist", "코드리뷰체크리스트"),
        ("commit-pr-message-rules", "커밋메시지작성"),
        ("docs-style-guide", "문서작성"),
        ("incident-postmortem", "장애회고"),
        ("human-approval-loop", "승인루프"),
        ("3c-4p-swot-framework", "전략분석프레임워크"),
        ("business-plan-writing-guide", "기획서작성"),
        ("business-strategy-formulation", "사업전략수립"),
        ("executive-report-writing-style", "경영보고서작성"),
        ("presentation-outline-builder", "발표자료구성"),
        ("test-case-mechanical-derivation", "테스트케이스도출"),
        ("pm-delivery-coordination", "진행관리조율"),
        ("application-architecture-design-guide", "아키텍처설계"),
        ("data-model-design-guide", "데이터모델설계"),
        ("module-decomposition-and-task-assignment", "모듈분해업무배분"),
        ("frontend-ui-implementation-guide", "프론트엔드구현"),
        ("ui-ux-planning-guide", "UIUX기획"),
        ("test-plan-and-case-design-guide", "테스트계획설계"),
        ("quality-acceptance-criteria-guide", "품질인수기준"),
        ("data-infra-implementation-guide", "데이터인프라구현"),
        ("data-analysis-guide", "데이터분석"),
        ("proposal-writing-guide", "제안서작성"),
        ("performance-report-writing-guide", "실적서작성"),
        ("framework-template-generator", "프레임워크템플릿생성"),
    ):
        old_row = conn.execute(
            "SELECT id FROM skill_pool WHERE name = ?", (old_name,)
        ).fetchone()
        if old_row is None:
            continue
        conn.execute(
            "UPDATE skill_pool SET name = ? WHERE id = ?", (new_name, old_row["id"])
        )
        for table, column in (
            ("tasks", "required_skills"),
            ("agent_pool", "skill_tags"),
        ):
            for row in conn.execute(f"SELECT id, {column} FROM {table}").fetchall():
                tags = json.loads(row[column] or "[]")
                if old_name in tags:
                    tags = [new_name if t == old_name else t for t in tags]
                    conn.execute(
                        f"UPDATE {table} SET {column} = ? WHERE id = ?",
                        (json.dumps(tags, ensure_ascii=False), row["id"]),
                    )

    _rename_guide_suffixed_skills(conn)

    # 사용자 요청(2026-10-06, 다자간 팀 협업 기획 Phase 1+2) —
    # `team_leader_id`는
    # resource_type='team' 배정의 결재 주체(기본값은 배정 요청자,
    # 변경 가능 — `assignments.update_team_leader()`). `replaces_
    # assignment_id`는 "다중 confirmed 허용"으로 바꾸면서(같은 task·
    # 같은 resource_type이어도 더 이상 자동으로 서로를 밀어내지 않는다)
    # 그래도 "이 배정은 저 배정을 명시적으로 교체한다"는 의도를 표현할
    # 길이 필요해서 추가했다 — 이 값이 있을 때만 승인 시 그 특정
    # assignment가 superseded된다(`assignments.resolve_assignment_
    # decision()`).
    _ensure_column(
        conn, "assignments", "team_leader_id", "INTEGER REFERENCES users(id)"
    )
    _ensure_column(
        conn,
        "assignments",
        "replaces_assignment_id",
        "INTEGER REFERENCES assignments(id)",
    )
    # 사용자 요청(2026-10-07, "HAIR 협업 결과물이 코드, 마크다운, PPT,
    # HTML 등 다양할 수 있을텐데 현재 지원 가능한 범위와... Tier 2까지
    # 진행해줘") — 지금까지 `deliverables.content`는 포맷 구분 없는
    # 순수 TEXT였다(LLM이 뭘 내든, 사람이 뭘 쓰든 전부 같은 컬럼에
    # 섞여 들어감 — 렌더링도 `renderContent()`의 코드펜스/굵게 처리
    # 뿐이었다). `content_type`으로 저장 시점에 포맷을 명시해 화면이
    # 포맷에 맞게 렌더링(마크다운 전체 렌더링, HTML은 sandbox iframe
    # 미리보기)할 수 있게 한다 — 실제 파일(PPT 등) 저장은 Tier 3(스키마
    # 재구성 필요)로 남겨둔 범위 밖이다.
    _ensure_column(
        conn,
        "deliverables",
        "content_type",
        "TEXT NOT NULL DEFAULT 'text' "
        "CHECK (content_type IN ('text', 'markdown', 'html', 'code'))",
    )

    # 2026-10-08, 사용자 요청 "ASANA 모방
    # 수준의 기능 및 UI 제공을 위한 기획 및 설계안을 제안해줘" → Phase 1
    # 승인 — Task 자체에 메모를 남길 수 있는 자유 텍스트 공간. 기존
    # `deliverables.content`(결과물)나 `deliverable_comments`(결과물에 대한
    # 댓글)와 달리, 결과물이 아직 없는 단계에서도 Task 참가자들이 맥락/
    # 의사결정을 적어둘 수 있는 곳이 없었다.
    _ensure_column(conn, "tasks", "notes", "TEXT NOT NULL DEFAULT ''")

    # 2026-11-05, 로드맵 9번 "Task 레벨 비용/재시도 예산 추적" — 기본값
    # NULL(= 예산 제한 없음, 기존 Task 전부 동작 무변화). 설정하면
    # `pipeline_runtime._execute_stage_attempt()`가 `dashboard.
    # task_retry_summary()`의 합산치와 비교해, 단계 내 `max_retry`가
    # 아직 남아 있어도 Task 전체 예산을 넘으면 바로 에스컬레이션한다.
    _ensure_column(conn, "tasks", "max_retry_budget", "INTEGER")

    # 사용자 요청(2026-11-17, "Human Agency Scale... 인간이 갖고
    # 있어야 할 고유 능력을 보호하는 Task는 Gate 점수와 무관하게 항상
    # 사람에게 올라가야 한다" → "구현 시작") — 기본값 0(false, 기존
    # Task 전부 동작 무변화). 켜면 `pipeline_runtime._execute_stage_
    # attempt()`가 그 Task가 타는 모든 파이프라인 단계에서, 그 단계
    # 템플릿 자체의 `requires_human_approval` 설정과 무관하게 Gate
    # 통과를 자동 승인으로 치지 않고 항상 결재를 거치게 한다.
    _ensure_column(
        conn, "tasks", "requires_human_judgment", "INTEGER NOT NULL DEFAULT 0"
    )

    # 사용자 요청(2026-11-17, "Human Agency Scale... 코파일럿(사람이
    # 실시간으로 제어)에 해당하는 기능이 없다, 실행 중 중단 요청
    # 정도가 최소한의 첫 단계" → "구현 시작") — `pipeline_runs.status`
    # CHECK 제약은 이미 'cancelled' 값을 허용해뒀지만(스키마 선제
    # 준비) 실제로 그 상태로 전이시키는 코드가 지금까지 전혀 없었다.
    # 이 플래그는 "지금 당장 멈춰라"가 아니라 "다음 안전한 지점
    # (`_execute_stage_attempt()` 진입 직후, 비용이 드는 Crew 호출
    # 시작 전)에서 멈춰라"는 요청이다 — 이미 시작된 Crew 실행 중간에
    # 끼어들지 않는다(그건 더 큰 작업, 범위 밖).
    _ensure_column(
        conn, "pipeline_runs", "cancel_requested", "INTEGER NOT NULL DEFAULT 0"
    )

    # 2026-10-08, Phase 3 — 댓글
    # `@멘션`으로 트리거된 Agent 실행을 "AI 에이전트 실행" 버튼으로
    # 트리거된 것과 구분하기 위한 출처 표시. NULL이면 기존 경로(버튼),
    # 값이 있으면 그 댓글이 트리거했다는 뜻 — 어느 쪽이든 같은
    # `agent_execution_jobs` 테이블·폴링 UI를 그대로 재사용한다.
    _ensure_column(
        conn,
        "agent_execution_jobs",
        "source_comment_id",
        "INTEGER REFERENCES deliverable_comments(id)",
    )

    # 2026-10-08, Phase 4 — 작업 구조
    # 심화(상태/우선순위). `task_dependencies`는 새 테이블이라
    # `_ensure_column()` 불필요 — SCHEMA의 `CREATE TABLE IF NOT EXISTS`
    # 만으로 기존 DB에도 안전하게 추가된다.
    _ensure_column(
        conn,
        "tasks",
        "status",
        "TEXT NOT NULL DEFAULT 'queued' "
        "CHECK (status IN ('backlog', 'queued', 'in_progress', 'done'))",
    )
    _ensure_column(
        conn,
        "tasks",
        "priority",
        "TEXT NOT NULL DEFAULT 'medium' "
        "CHECK (priority IN ('low', 'medium', 'high', 'urgent'))",
    )

    # 사용자 요청(2026-10-13, "AI Agent에도 이름을 지정하고... 호출시(채팅
    # 포함)에도 활용될 수 있도록 개선") — 지금까지 Agent는 `role_name`
    # (생성 당시 역할, 예: "백엔드 개발자 Agent")이 식별자 역할까지
    # 겸하고 있어, 같은 역할의 Agent가 여럿이면 구분이 안 되고
    # "에이전트 #7"처럼 ID로만 불러야 했다. Human Pool의 `name`과 같은
    # 자리 — 선택 입력(NULL 허용)이라 기존 Agent는 전부 비어 있는
    # 채로 시작하고, 비어 있으면 화면·멘션 전부 `role_name`으로
    # 그대로 돌아간다(하위 호환 100%, 아무것도 깨지지 않음).
    _ensure_column(conn, "agent_pool", "name", "TEXT")

    # 사용자 요청(2026-10-13, "문서 내용에서 얻을 인사이트" → 1,2번
    # 구현) — "AI 재현 가능한 감사로그" 인사이트. 지금까지 `audit_log`의
    # `agent_executed` 행이 원본(증강 전) 프롬프트만 `before_value`에
    # 남기고, 실제로 LLM에 들어간 system prompt·RAG/GraphDB/외부검색로
    # 증강된 최종 프롬프트·그때 쓴 provider/model은 어디에도 안
    # 남았다(`agent_pool.llm_provider`/`llm_model`은 다음 실행에
    # 덮어써져 과거 실행 시점 값을 잃는다) — Gate 점수가 낮게 나온
    # 산출물을 "왜 이렇게 나왔는지" 사후 재현할 방법이 없었다. 이
    # 실행(`run_agent_execution_job()`)과 직접 연결된
    # `agent_execution_jobs`에 그 시점 스냅샷을 JSON으로 남긴다.
    _ensure_column(conn, "agent_execution_jobs", "prompt_snapshot", "TEXT")
    # 결과물 수정 이력(2026-11-23, P1-6 — `deliverables.deliverable_history`).
    from hair_platform import deliverables as _deliverables

    _deliverables.ensure_revision_schema(conn)
    # 개인 API 키(2026-11-23, P1-5 — `integrations.py`).
    from hair_platform import integrations

    integrations.ensure_schema(conn)
    # 코드 저장소 읽기 연동(2026-11-24, P0-3 — `code_repos.py`).
    from hair_platform import code_repos

    code_repos.ensure_schema(conn)
    from hair_platform import data_connectors  # 사내 DB 조회 연동(2026-11-26, P1-9)

    data_connectors.ensure_schema(conn)
    from hair_platform import mail_inbox  # 메일 수신함 읽기(2026-11-26, P1-9 2단계)

    mail_inbox.ensure_schema(conn)
    from hair_platform import calendar_feeds  # 캘린더 읽기(2026-11-26, P1-9 3단계)

    calendar_feeds.ensure_schema(conn)
    from hair_platform import job_queue  # 백그라운드 작업 큐·작업 프로세스(2026-11-26, P1-6 2단계)

    job_queue.ensure_schema(conn)
    from hair_platform import tool_audit  # 도구 사용 기록(2026-11-26, 개선 계획 1-1)

    tool_audit.ensure_schema(conn)
    from hair_platform import connection_health  # 연결 상태(2026-11-26, 개선 계획 2-3)

    connection_health.ensure_schema(conn)
    # 결과물 → 저장소 커밋 기록(2026-11-24, P0-3 2단계 — `repo_commit.py`).
    from hair_platform import repo_commit

    repo_commit.ensure_schema(conn)
    # 결과물 부분 재작성 제안(2026-11-24, P1-6 — `edit_proposals.py`).
    from hair_platform import edit_proposals

    edit_proposals.ensure_schema(conn)
    # 이벤트 트리거(2026-11-24, P1-5 — `triggers.py`).
    from hair_platform import triggers

    triggers.ensure_schema(conn)
    from hair_platform import evaluation_reports  # 평가 HTML 리포트(2026-11-25)

    evaluation_reports.ensure_schema(conn)
    from hair_platform import evaluation_config  # 평가 기준 설정(2026-11-25)

    evaluation_config.ensure_schema(conn)
    from hair_platform import knowledge_access  # 지식 베이스 문서별 공개 범위(2026-11-25, P1-5)

    knowledge_access.ensure_schema(conn)
    # 대화에서 전환한 업무 요청의 출처(2026-11-23, P1-4).
    _ensure_column(conn, "briefs", "source_conversation_id", "INTEGER REFERENCES conversations(id)")
    # 실행 중 단계 표시(2026-11-23, P0-2 — `progress.py`).
    for _table in ("conversation_jobs", "agent_execution_jobs"):
        _ensure_column(conn, _table, "progress", "TEXT")
        _ensure_column(conn, _table, "progress_at", "TEXT")
        # 작성 중인 응답 미리보기(2026-11-24, P0-2 스트리밍 — `progress.partial_sink()`).
        _ensure_column(conn, _table, "partial_output", "TEXT")
    # 파이프라인 진행률(2026-11-24, P0-2) — 실행 중인 단계의 지금 하는 일·시작 시각·작성 중 미리보기.
    # 단계 행(pipeline_run_stages)은 단계가 끝나야 생겨, 지금까지는 진행 중 단계를 보여 줄 수 없었다.
    for _column in ("progress", "progress_at", "stage_started_at", "partial_output"):
        _ensure_column(conn, "pipeline_runs", _column, "TEXT")

    # 사용자 요청(2026-10-13, "생성된 CrewAI 에이전트를 선택해 Finetuning
    # 하는 기능") — 실제 LLM 가중치 재학습이 아니라(사용자 확인) CrewAI
    # `Agent`/`LLM`의 동작 파라미터(max_iter/max_rpm/max_execution_time/
    # max_retry_limit/reasoning/allow_code_execution/
    # respect_context_window/temperature)를 Agent별로 저장해두고
    # `crew_runtime.py`의 CrewAI 생성 경로(생성 시점 단일 Agent + Crew
    # 실행 둘 다)에 반영하는 기능이다. 전부 선택값이라 JSON 하나로
    # 묶는다(mcp_servers와 같은 관례) — 키가 없으면 CrewAI 기본값을
    # 그대로 쓴다(지정 안 한 Agent는 지금과 100% 동일하게 동작).
    _ensure_column(conn, "agent_pool", "tuning_config", "TEXT NOT NULL DEFAULT '{}'")
    _migrate_tool_pool_custom_python_kind(conn)
    _migrate_pipeline_stage_plan_merge_mode(conn)
    _migrate_parallel_delegated_to_plan_merge(conn)
    # 사용자 요청(2026-10-14, "호출 받은 사람에게 브라우저 알림이
    # 오고... ") — 실측으로 발견한 버그: 처음엔 "최초 폴링은 알림 없이
    # 기준점만 잡는다"는 클라이언트 메모리 커서(`_mentionNotifySinceId`)
    # 로 중복 알림을 막았는데, 그 탭이 멘션이 이미 온 *뒤에* 열리면(실제
    # 테스트 상황 그대로 — admin이 먼저 세 번 호출하고, pm_kim이 그 뒤에
    # 로그인) 그 멘션들이 전부 "최초 폴링"에 걸려 영원히 알림이 안 뜬다.
    # 서버가 참가자별 "마지막으로 읽은 메시지"를 기억해야 진짜로 고쳐진다.
    _ensure_column(conn, "conversation_participants", "last_read_message_id", "INTEGER")
    _backfill_conversation_last_read(conn)
    # 사용자 요청(2026-10-14, "10건 이전의 메시지는 요약 형태로 전달되도록
    # 개선해줘") — `_build_transcript()`(conversations.py)가 최근
    # `_CONTEXT_MESSAGE_LIMIT`(10)건보다 오래된 메시지는 그냥 버리던 것을,
    # 캐시된 요약으로 대체한다.
    _ensure_column(conn, "conversations", "context_summary", "TEXT")
    _ensure_column(conn, "conversations", "context_summary_through_id", "INTEGER")
    # 사용자 요청(2026-10-14, "대화창에 사용중인 모델이 명시되도록 UI를
    # 개선해줘") — 같은 대화라도 트리거(기본 AI/Agent 멘션/Team 멘션)마다
    # 실제로 쓰이는 provider/model이 다르다(`conversations.py`의
    # `run_default_ai_job`/`run_agent_mention_job`/`run_team_mention_job`
    # 참고 — 대화의 `llm_provider`/`llm_model` 설정은 기본 AI 응답에만
    # 쓰이고, Agent 멘션은 그 Agent 자신의 저장값을, Team 멘션은 환경변수/
    # API 키 기반 자동판별값을 쓴다). 메시지마다 실제로 쓰인 값을 저장해야
    # 화면에 정확히 보여줄 수 있어 대화 설정이 아니라 메시지 단위로 둔다.
    _ensure_column(conn, "conversation_messages", "llm_provider", "TEXT")
    _ensure_column(conn, "conversation_messages", "llm_model", "TEXT")
    # 사용자 요청(2026-10-18, "@자율 개발 크루... 생성 결과물의 품질을
    # 개선하기 위해... 어떤 개선이 필요할지") 분석 후속 — 실측으로 확인한
    # "Gate가 fail해도 대화창 경로에서는 측정만 하고 버려진다" 문제를
    # 메우는 최소한의 안전장치. `run_crew()`가 이미 계산해서 반환하는
    # gate_scores/delegations 개수를 메시지에 같이 저장해, 화면에서
    # "이 결과가 Gate 기준을 통과했는지"·"매니저가 실제로 위임했는지"를
    # 바로 보여준다(Task 협업워크스페이스 경로의 agent_evaluations/
    # 결재 체인만큼 엄격하진 않지만, 적어도 조용히 사라지진 않는다).
    # 모델별 생성 옵션(2026-11-21, `model_options.py`) — top_k/top_p 등.
    # 비어 있으면 아무것도 보내지 않는다(그 모델의 기본값).
    _ensure_column(conn, "model_registry", "sampling_options", "TEXT NOT NULL DEFAULT '{}'")
    # 조직 정책(2026-11-21, `org_policy.py`) — 사번 형식(정규식)·설명. 행이 없으면 기본값
    # (숫자 8~12자리)을 쓴다.
    conn.execute(
        "CREATE TABLE IF NOT EXISTS org_policy ("
        "id INTEGER PRIMARY KEY CHECK (id = 1), employee_id_pattern TEXT, employee_id_hint TEXT)"
    )
    _ensure_column(conn, "org_policy", "output_language", "TEXT")
    # 외부 알림 웹훅·정기 실행(2026-11-23, P1-5 — `integrations.py`·`automation.py`).
    from hair_platform import automation, integrations

    integrations.ensure_notify_schema(conn)
    automation.ensure_schema(conn)
    # 팀별 "공장 초기화 시 보호"(2026-11-21) — 예전엔 `data_admin`이 팀 이름("데이터 분석·
    # 시각화 전담팀")으로 보호해, 팀 이름을 바꾸면 보호가 풀렸다.
    _ensure_column(conn, "teams", "protect_on_reset", "INTEGER NOT NULL DEFAULT 0")
    # 대화 컨텍스트 정책(2026-11-20, `context_policy.py` 참고) — 스킬별 기본값,
    # Agent별 덮어쓰기(NULL = 스킬에서 계산), 메시지 단위 요청/적용 기록.
    _ensure_column(
        conn,
        "skill_pool",
        "history_mode",
        "TEXT NOT NULL DEFAULT 'full' CHECK (history_mode IN ('full', 'recent', 'none'))",
    )
    _ensure_column(conn, "skill_pool", "use_attachments", "INTEGER NOT NULL DEFAULT 1")
    # 스킬 포함 관계(2026-11-20, "제안서 작성의 경우, 프레젠테이션 스킬을
    # 활용 또는 참조하는 방식으로 개선") — 이 스킬을 쓸 때 함께 적용할 다른
    # 스킬 이름 목록(JSON). `pool_registry.skill_knowledge_text()`가 펼친다.
    _ensure_column(conn, "skill_pool", "includes", "TEXT NOT NULL DEFAULT '[]'")
    _ensure_column(
        conn,
        "agent_pool",
        "history_mode",
        "TEXT CHECK (history_mode IS NULL OR history_mode IN ('full', 'recent', 'none'))",
    )
    _ensure_column(conn, "agent_pool", "use_attachments", "INTEGER")
    _ensure_column(conn, "conversation_messages", "context_override", "TEXT")
    _ensure_column(conn, "conversation_messages", "context_policy", "TEXT")
    # 지식베이스 등록 체계 개편(2026-11-20) — `documents` CREATE TABLE 설명 참고.
    _ensure_column(conn, "documents", "source_type", "TEXT NOT NULL DEFAULT 'direct'")
    _ensure_column(
        conn,
        "documents",
        "submission_id",
        "INTEGER REFERENCES knowledge_submissions(id)",
    )
    _ensure_column(conn, "conversation_messages", "gate_scores", "TEXT")
    _ensure_column(conn, "conversation_messages", "delegation_count", "INTEGER")
    # 사용자 보고(2026-10-19, "Todo 앱 최종 결과물에 코드가 아니라 품질
    # 리포트만 있다") — `pipeline_stages` CREATE TABLE에 추가한 설명
    # 참고. 어느 단계가 "진짜 최종 산출물"인지 표시하는 플래그.
    _ensure_column(
        conn, "pipeline_stages", "is_primary_deliverable", "INTEGER NOT NULL DEFAULT 0"
    )
    # 사용자 요청(2026-11-08, "게임이 아닌 일상 사무 업무도 일반화") —
    # `pipeline_stages` CREATE TABLE에 추가한 설명 참고. 단계명과 무관한
    # 요구사항 추적성 검증 플래그.
    _ensure_column(
        conn,
        "pipeline_stages",
        "checks_requirements_traceability",
        "INTEGER NOT NULL DEFAULT 0",
    )
    # 사용자 요청(2026-11-10, "결과물 출력 유형 다양화") — `pipeline_stages`
    # CREATE TABLE에 추가한 설명 참고.
    _ensure_column(
        conn,
        "pipeline_stages",
        "output_content_type",
        "TEXT NOT NULL DEFAULT 'text' "
        "CHECK (output_content_type IN ('text', 'markdown', 'html', 'code'))",
    )
    # 사용자 요청(2026-10-20, "CrewAI agent의 경우 에이전트 생성은 role,
    # goal, backstory, tool 등을 설정하고... HAIR-Platform에 crewai
    # 에이전트 및 에이전트 그룹을 상세조정하고 수정하기 위한 고급 에이전트
    # 관리 기능 추가를 기획해줘") — 지금까지 `Agent.goal`/`backstory`는
    # `crew_runtime._agent_goal()`/`_agent_backstory()`가 role_name+
    # skill_tags로 매번 자동 생성했고, `Task.expected_output`은 플랫폼
    # 전역에서 하드코딩 문자열 하나뿐이었다(관리자가 덮어쓸 방법 없음).
    # 전부 NULL 허용 — NULL/빈 문자열이면 기존처럼 자동 생성(100% 하위
    # 호환, `tuning_config` 빈 값 관례와 동일).
    _ensure_column(conn, "agent_pool", "goal", "TEXT")
    _ensure_column(conn, "agent_pool", "backstory", "TEXT")
    _ensure_column(conn, "agent_pool", "expected_output", "TEXT")
    # Team(재사용 가능한 사람+Agent 그룹, `teams` 테이블)은 멤버 목록뿐
    # process(sequential/hierarchical)·매니저 기본값이 없어, "팀 Agent
    # 실행"을 누를 때마다 매번 새로 골라야 했다(또는
    # `manager_agent_id_for_team()`의 `role_label`="매니저" 문자열 추정에
    # 의존). NULL이면 기존처럼 그 추정/매 실행 시 선택에 그대로 맡긴다.
    _ensure_column(conn, "teams", "default_process", "TEXT")
    _ensure_column(
        conn, "teams", "default_manager_agent_id", "INTEGER REFERENCES agent_pool(id)"
    )
    # 파이프라인 단계는 `instructions`(Task.description 보강)는 있었지만
    # `expected_output`은 없어, 모든 단계가 똑같은 하드코딩 문구를 썼다
    # (예: "QA 기능테스트" 단계도 "구현" 단계와 같은 기대 산출물 문구).
    # 빈 문자열 = 기존 하드코딩 문구 그대로 사용.
    _ensure_column(
        conn, "pipeline_stages", "expected_output", "TEXT NOT NULL DEFAULT ''"
    )
    # 사용자 요청(2026-10-20, "실패 시 일부 재시도") — 실패한 crew_run을
    # 복구하려면 원본 Agent별 task description(재시도 시 복원용)과, 실제로
    # Task를 끝내 deliverable이 만들어진 Agent 목록(남은 Agent만 추려
    # 재시도하기 위함)이 필요하다. 둘 다 NULL/빈 배열이면 기존 run(이
    # 마이그레이션 이전에 생성된 행)은 그냥 "재시도 불가"로 취급된다.
    _ensure_column(conn, "crew_runs", "task_descriptions", "TEXT")
    _ensure_column(
        conn, "crew_runs", "completed_agent_ids", "TEXT NOT NULL DEFAULT '[]'"
    )
    # 사용자 요청(2026-10-21, "팀 워크플로우 노드 캔버스") — 지금까지
    # `team_members`엔 순서 개념이 전혀 없어(암묵적으로 `id` 삽입 순서만
    # 있음) 사용자가 순차 릴레이 순서를 바꿀 수단이 없었다. NULL = "아직
    # 캔버스로 순서를 정한 적 없음" → 기존처럼 `id` 순서로 폴백(하위 호환).
    _ensure_column(conn, "team_members", "sequence_order", "INTEGER")
    _ensure_column(
        conn, "skill_pool", "show_in_conversation", "INTEGER NOT NULL DEFAULT 1"
    )
    # 사용자 요청(2026-11-11, "스킬과 도구를 SW개발업무와 일반업무로
    # 구분하고 필터링 되도록 개선") — 기존 DB(이 컬럼 없이 만들어진)에도
    # 추가. 기본값 'general'은 구조적 안전망일 뿐이고, 실제 레퍼런스
    # 스킬/도구의 분류는 `_backfill_skill_tool_category()`가 컬럼이
    # 막 추가된 시점에 한 번만 적용한다(아래 `init_schema()` 참고).
    _ensure_column(
        conn,
        "skill_pool",
        "category",
        "TEXT NOT NULL DEFAULT 'general' CHECK (category IN ('sw_dev', 'general'))",
    )
    _ensure_column(
        conn,
        "tool_pool",
        "category",
        "TEXT NOT NULL DEFAULT 'general' CHECK (category IN ('sw_dev', 'general'))",
    )
    _ensure_column(
        conn,
        "knowledge_chunks",
        "source_document_id",
        "INTEGER REFERENCES documents(id)",
    )
    # 사용자 요청(2026-10-26, "모델 레지스트리에 llm 설정 최적화 각
    # tier에 적용되는 모델을 매핑") — 기존 DB(이 컬럼 없이 만들어진)에도
    # 추가. '[]'(기본값) = 이 tier엔 아직 명시적 매핑이 없음(패턴 추정
    # 폴백, 기존 동작 유지).
    _ensure_column(
        conn, "model_registry", "optimization_tiers", "TEXT NOT NULL DEFAULT '[]'"
    )
    # 사용자 요청(2026-11-18, "...엔터프라이즈 환경 적용이 가능한 수준의
    # 확실한 개선 방안을 도출해줘" 개선축 B) — 기존 DB에도 추가.
    # NULL(기본값) = provider별 보수적 기본값으로 폴백(위 스키마 주석
    # 참고), admin이 등록해야 모델별 실제 한도가 정확히 반영된다.
    _ensure_column(conn, "model_registry", "context_window", "INTEGER")
    # 성능 테스트 정교화(2026-11-27) — 빠른 측정/전체 점검 구분과 세부 측정값(JSON).
    _ensure_column(conn, "model_benchmarks", "mode", "TEXT NOT NULL DEFAULT 'quick'")
    _ensure_column(conn, "model_benchmarks", "details", "TEXT")
    # 모델별 동시 실행 상한(2026-11-27, 개선 계획 3-1 — `llm_gate`). 비우면 Ollama 1, 클라우드 상한 없음.
    _ensure_column(conn, "model_registry", "max_concurrent", "INTEGER")
    # 모델별 단가·실제 사용량(2026-11-22, 하드코딩·스텁 점검 3순위 — `llm_usage.py`).
    _ensure_column(conn, "model_registry", "price_input_per_1m", "REAL")
    _ensure_column(conn, "pipeline_stages", "is_requirements_source", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "pipeline_stages", "on_fail_return_stage_id", "INTEGER")
    # 엄격 검토(2026-11-25, P0-4) — 켜면 "조건부 승인"(승인하면서 고칠 곳을 지적)도 보완 요청으로 처리한다.
    _ensure_column(conn, "pipeline_stages", "strict_review", "INTEGER NOT NULL DEFAULT 0")
    # 필수 도구(2026-11-26, 개선 계획 1-2) — JSON 목록(도구 Pool 이름). NULL = 지정 안 함.
    _ensure_column(conn, "pipeline_stages", "required_tools", "TEXT")
    # 단계별 모델(2026-11-26, 개선 계획 3-2) — NULL이면 대표 Agent의 모델
    _ensure_column(conn, "pipeline_stages", "llm_provider", "TEXT")
    _ensure_column(conn, "pipeline_stages", "llm_model", "TEXT")
    # 외부로 쓰거나 실행하는 도구(2026-11-26, 개선 계획 1-3) — 외부 데이터를 읽은 실행에서는 막힌다.
    _ensure_column(conn, "tool_pool", "side_effects", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "model_registry", "price_output_per_1m", "REAL")
    from hair_platform import llm_usage

    conn.executescript(llm_usage.SCHEMA)
    llm_usage.ensure_scope_columns(conn)  # 업무 맥락(2026-11-23, P2-11)
    from hair_platform import ai_budget  # AI 사용 한도·사용자별 기록(2026-11-25, P2-14)

    ai_budget.ensure_schema(conn)
    # 사용자 요청(2026-10-27, "에이전트, 도구/skill ID 체계를 수립하고
    # (종류 구분 가능) 초기화하여 정리") — 기존 DB에도 `code` 컬럼을
    # 추가한다. UNIQUE 인덱스는 컬럼이 확실히 생긴 뒤인 여기서만
    # 만든다(정적 SCHEMA에 두면 아직 컬럼 없는 기존 DB에서 깨짐 —
    # 위 실측 기록 참고). 실제 코드 배정(`id_scheme.backfill_missing_
    # codes()`)은 `init_schema()`가 시드까지 다 끝낸 뒤 호출한다.
    for table in ("agent_pool", "tool_pool", "skill_pool"):
        _ensure_column(conn, table, "code", "TEXT")
        conn.execute(
            f"CREATE UNIQUE INDEX IF NOT EXISTS idx_{table}_code ON {table}(code)"
        )
    # 사용자 요청(2026-10-27, "사람의 경우 ID는 향후 사번 적용 예정으로
    # 8자리~12자리 입력 및 표시 가능해야 함") — 기존 DB에도 컬럼을
    # 추가한다(위 code 컬럼들과 같은 이유로 정적 SCHEMA에 안 두고
    # 여기서만 인덱스를 만든다).
    _ensure_column(conn, "human_pool", "employee_id", "TEXT")
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_human_pool_employee_id "
        "ON human_pool(employee_id)"
    )
    # 사용자 요청(2026-10-27, "사용자 등록에 사번 입력 및 표시되도록
    # UI개선") — human_pool과 별개로 로그인 계정(`users`)에도 같은
    # 체계로 둔다(두 테이블 간 값을 서로 강제 일치시키진 않는다 — 계정
    # 없는 human_pool 레코드도 있고, 그 반대로 human_pool에 아직 안
    # 올라온 계정도 있어 1:1이 보장되지 않는다).
    _ensure_column(conn, "users", "employee_id", "TEXT")
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_employee_id "
        "ON users(employee_id)"
    )
    _sync_dev_pipeline_implementation_stage_agents(conn)
    # 사용자 요청(2026-10-28, "반려 사유를 재시도 지시문에 직접 끼워
    # 넣는 식으로 개선") — 기존 DB에도 컬럼을 추가한다.
    _ensure_column(conn, "pipeline_run_stages", "decision_reason", "TEXT")
    # 단계 시작 시각(2026-11-25, P0-3 예상 남은 시간) — 단계 기록은 단계가 끝날 때 생겨 created_at은 끝난 시각이다.
    _ensure_column(conn, "pipeline_run_stages", "started_at", "TEXT")
    # 사용자 요청(2026-10-28, "팀 관리의 '#3 자율 개발 크루'의 경우
    # 파이프라인을 참조해 실제 개발 품질을 보장하며 수행될 수 있도록
    # 재구성해줘") — 정적 SCHEMA의 `teams`/`pipeline_runs`는 새로
    # 만들어지는 DB에만 해당 컬럼이 있다. 기존 DB에도 추가한다.
    _ensure_column(
        conn,
        "teams",
        "pipeline_template_id",
        "INTEGER REFERENCES pipeline_templates(id)",
    )
    # 사용자 요청(2026-11-18, "다음 개선 진행해줘" → conversations.py
    # 통합) — `teams.pipeline_template_id`로 파이프라인에 연결된 팀이
    # `@팀멘션`될 때, 기존 `pipeline_runtime.py` 엔진 대신 신규
    # LangGraph 엔진(`execution_graph.py`)을 opt-in으로 쓸지 결정하는
    # 플래그. 기본 0(기존 동작 100% 무변화) — admin이 `PATCH /api/
    # teams/{id}/defaults`로 켤 수 있다(단, 그 팀의 템플릿이
    # `parallel_delegated` 단계를 포함하면 거부됨, `teams.py` 참고).
    _ensure_column(conn, "teams", "use_graph_engine", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(
        conn,
        "pipeline_runs",
        "source_conversation_id",
        "INTEGER REFERENCES conversations(id)",
    )
    # 사용자 요청(2026-11-18, "langgraph + langgraph-checkpoint-sqlite
    # 신규 의존성 추가 방향으로 설계를 구체화하고 진행해줘" → "진행해줘"
    # — 결재함 승인/반려를 LangGraph 실행엔진(`execution_graph.py`)의
    # `interrupt()`에 연결) — 기존 `pipeline_runtime.run_pipeline_
    # stage_job()`으로 계속 실행 중인 run과, 새 그래프 엔진으로
    # 시작된 run을 구분해야 한다 — 서로 상태 전이 방식이 달라서(그래프
    # 엔진은 LangGraph 체크포인트가 "진실의 원천", 기존 엔진은 DB
    # 컬럼이 "진실의 원천") 승인 처리 시 반드시 맞는 엔진으로만
    # 재개해야 한다. 기본값 'legacy' — 기존 run은 전부 이 값, 그래프
    # 엔진으로 시작된 run만 `execution_graph.run_pipeline_graph()`가
    # 'graph'로 바꾼다(기존 동작 100% 무변화).
    _ensure_column(conn, "pipeline_runs", "engine", "TEXT NOT NULL DEFAULT 'legacy'")
    # 서버 재시작으로 중단된 실행 표시(2026-11-25, P1-6) — 이어서 실행할 수 있는 실패.
    _ensure_column(conn, "pipeline_runs", "interrupted_at", "TEXT")
    # 프로세스 성과(2026-11-27, AI Native 분석 1단계) — 예전 방식으로 이 업무 한 건에 걸리던 시간(분, 사람이 입력).
    _ensure_column(conn, "pipeline_templates", "baseline_minutes", "INTEGER")
    # 같은 화면이 단계 기록 → 결재 → 결정 시각(활동 기록)을 이어 찾는다. 기록이 쌓여도 느려지지 않게 색인을 둔다.
    conn.execute("CREATE INDEX IF NOT EXISTS idx_approvals_subject ON approvals(subject_type, subject_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_log_subject ON audit_log(subject_type, subject_id)")
    # 배운 점·변경 기록(2026-11-27, AI Native 분석 2단계 — `process_learning`).
    from hair_platform import process_learning
    process_learning.ensure_schema(conn)
    _sync_dev_crew_pipeline_integration(conn)
    # 사용자 요청(2026-10-29, "TF팀을 teams의 확장으로, Team 온보딩
    # 라이프사이클 도입 — 기존 활성 팀은 전부 자동 active 처리") —
    # 기존 DB에도 컬럼을 추가하고, 이미 활성/비활성이던 팀들을
    # 재검토 없이 새 status로 백필한다. `status='pending_onboarding'`
    # 조건이 "이미 한 번 백필됐거나 관리자가 그 뒤 직접 바꾼 행은
    # 다시 안 건드린다"는 멱등 가드 역할을 한다.
    _ensure_column(
        conn, "teams", "status", "TEXT NOT NULL DEFAULT 'pending_onboarding'"
    )
    _ensure_column(conn, "teams", "task_id", "INTEGER")
    _ensure_column(conn, "teams", "brief_id", "INTEGER")
    _ensure_column(conn, "teams", "completed_at", "TEXT")
    conn.execute(
        "UPDATE teams SET status = 'active' "
        "WHERE is_active = 1 AND status = 'pending_onboarding'"
    )
    conn.execute(
        "UPDATE teams SET status = 'deprecated' "
        "WHERE is_active = 0 AND status = 'pending_onboarding'"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_teams_task_id ON teams(task_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_teams_brief_id ON teams(brief_id)")

    # 사용자 요청(2026-11-17, "skill을 정해진 섹션 규격을 참조해
    # 구조화하고, 외부에서 마크다운 포맷의 Skill을 import하거나...
    # export, skill의 동작을... 수정하는 기능") — `description` 하나에
    # 요약·방법론·출력형식이 전부 뭉쳐 있던 것을, 외부 표준 Skill
    # 포맷(frontmatter + 섹션)과 호환되는 전용 필드로 나눈다. 전부
    # NULL 허용 — 기존 53개 스킬 전부 이 컬럼 없이 만들어졌으므로
    # 마이그레이션 시점엔 전부 NULL이고(`skill_markdown.py`의
    # export/import, `pool_registry.skill_knowledge_text()`가 모두
    # "없으면 생략" 원칙을 지킨다), 값이 있는 스킬만 더 자세한 지시를
    # 받는다 — 기존 동작에 회귀가 없다.
    _ensure_column(conn, "skill_pool", "output_format", "TEXT")
    _ensure_column(conn, "skill_pool", "examples", "TEXT")
    _ensure_column(conn, "skill_pool", "constraints", "TEXT")


# 위 마이그레이션이 지우는 대상 — 2026-10-26 "자율 개발 크루" 12명→6명
# 정리 전, "구현" 단계에 심어졌던 중복 클론 role_name들(지금의
# agent_pool에는 존재하지 않는 고아 참조).
_DEV_PIPELINE_STALE_IMPLEMENTATION_ROLES = (
    "개발자",
    "백엔드 개발자 2",
    "백엔드 개발자 3",
    "백엔드 개발자 4",
    "프론트엔드 개발자 2",
    "프론트엔드 개발자 3",
    "데이터/인프라 개발자 1",
    "데이터/인프라 개발자 2",
)
# 위 정리 전 `DEV_PIPELINE_DESCRIPTION`에 있던 문구 — 아래 함수가 DB에
# 이 문구가 그대로 남아 있을 때만 새 문구로 바꾼다(관리자가 이미 직접
# 수정했다면 더 이상 이 문자열과 일치하지 않으므로 건드리지 않는다).
_DEV_PIPELINE_STALE_DESCRIPTION = (
    "기획서 → 요구분석(계획 확정, 사람 승인 1회) → 설계(AA→DA) → 개발 계획 수립 → "
    "구현(매니저가 10명+UI기획자에게 실제 위임) → 코드 검증 → QA 기능테스트 → "
    "품질 관리 최종 판정까지, Gate A-G 기준으로 자동 반복하며 진행하는 정식 "
    "개발 파이프라인(BMAD-METHOD 대응 — 계획 확정 시점에만 사람이 개입한다)."
)

# 실측 발견(2026-11-06, park_member 로드러너 게임 데모 — "소프트웨어
# 개발 파이프라인"을 Task #3에 두 번 독립 실행했더니 둘 다 같은 지점에서
# 같은 방식으로 실패) — "구현" 단계의 매니저("개발 계획 수립가")에게
# 주는 지시문이 "앞 단계의 개발 계획에 따라 모듈을 구현하세요."처럼
# 모호해서, 매니저가 코워커에게 위임(Delegate work to coworker)을 한
# 번도 호출하지 않고 자신의 전문 분야(모듈 분해/업무 배정)대로 계획
# 문서만 다시 써서 제출했다. 지시문을 명시적으로 고쳐도(2026-11-06
# 1차 수정) 여전히 재현됐다 — 외부 조사 결과 CrewAI의 hierarchical
# (위임) 프로세스 자체가 커뮤니티에 널리 보고된 신뢰성 문제이고, BMAD-
# METHOD 공식 가이드도 "순차(sequential)로 하나씩 완성 후 다음으로"를
# 기본·권장 패턴으로 삼는다(병렬 위임은 BMAD 코어가 아니라 서드파티
# 애드온) — 그래서 이 단계 자체를 `parallel_delegated`(hierarchical)
# 에서 이미 "설계"/"QA 기능테스트"에 쓰이고 있는 `sequential_relay`로
# 전환한다. Delegate 도구 호출이 전혀 없어 위임 신뢰성 문제가 구조적
# 으로 사라진다. 아래 문구들은 이 전환 전 상태를 식별하기 위한
# 스테일(stale) 마커다 — 관리자가 이미 직접 수정했다면 더 이상 이
# 문자열과 일치하지 않으므로 건드리지 않는다(기존 description 교체와
# 같은 안전장치).
_DEV_PIPELINE_STALE_IMPLEMENTATION_INSTRUCTIONS = "앞 단계의 개발 계획에 따라 모듈을 구현하세요."
_DEV_PIPELINE_STALE_IMPLEMENTATION_INSTRUCTIONS_V2 = (
    "당신(매니저)은 이 구현 단계를 지휘하는 역할이며, 직접 코드를 작성하지 "
    "않습니다. 앞 단계의 개발 계획과 아래 [코워커별 참고 가이드]를 바탕으로, "
    "반드시 'Delegate work to coworker' 도구를 사용해 각 코워커에게 맡을 "
    "모듈을 구체적으로 지시하고 실제 코드를 작성하게 하세요. 모든 코워커에게 "
    "위임을 마친 뒤에는 그들이 돌려준 산출물을 모아 하나의 결과로 제출하세요 "
    "— 요약하거나 다시 쓰지 말고 받은 코드를 그대로 포함하세요."
)
_DEV_PIPELINE_STALE_PLANNING_INSTRUCTIONS = (
    "설계 산출물을 독립적으로 개발 가능한 모듈 단위로 쪼개고 선행조건·완료기준을 정하세요."
)


def _sync_dev_pipeline_implementation_stage_agents(conn: sqlite3.Connection) -> None:
    """사용자 요청(2026-10-28, "파이프라인 관리 — '#1 소프트웨어 개발
    파이프라인(software_dev)'을 현행화해줘") — `_seed_dev_pipeline()`은
    `pipeline_templates.name`이 이미 있으면 그냥 return한다(관리자가
    고친 내용을 재기동 때 덮어쓰지 않으려는 의도적 설계, 그 함수의
    docstring 참고). 그런데 그 설계 때문에 "자율 개발 크루"를 12명→6명
    으로 정리한 2026-10-26 커밋(`DEV_CREW_TEAM_MEMBER_SPECS` 변경)이
    `DEV_PIPELINE_STAGES`의 "구현" 단계 agents 목록까지 맞게 고쳤어도
    이미 한 번 심어진 `pipeline_stage_agents` 행에는 전혀 반영되지
    않았다(실측 확인 — "구현" 단계에 더 이상 agent_pool에 없는
    "백엔드 개발자 2/3/4" 등 8개 role_name이 고아로 남아 있었고, 정작
    지금 쓰이는 "백엔드 개발자"/"데이터/인프라 개발자"는 빠져 있었다).
    "파이프라인 관리" 화면은 이 테이블을 그대로 읽어 보여주므로
    (`pipeline_runtime.py`), 화면에 옛 12명 체제가 계속 떴다.

    고아 role_name만 지우고 현재 `DEV_PIPELINE_STAGES`에 있는데 빠진
    role만 추가한다 — 멱등(이미 현행화됐으면 두 쿼리 다 0건)이고,
    관리자가 "구현" 단계에 직접 추가한(위 고아 목록에 없는) 다른
    role_name은 건드리지 않는다."""
    stage_row = conn.execute(
        """
        SELECT ps.* FROM pipeline_stages ps
        JOIN pipeline_templates pt ON pt.id = ps.pipeline_template_id
        WHERE pt.name = ? AND ps.name = '구현'
        """,
        (reference_pipeline_seeds.DEV_PIPELINE_NAME,),
    ).fetchone()
    if stage_row is None:
        return
    stage_id = stage_row["id"]
    implementation_stage = next(
        s for s in reference_pipeline_seeds.DEV_PIPELINE_STAGES if s[0] == "구현"
    )

    # 2026-11-07 — "구현" 단계를 parallel_delegated(hierarchical)에서
    # sequential_relay로 전환(이 파일 상단 주석 참고). 이 단계가 아직
    # 옛 hierarchical 형태(mode/lead_role_name이 전환 전 그대로)일
    # 때만 안전하게 전체 교체한다 — 관리자가 이미 수동으로 손봤다면
    # (mode가 다른 값으로 바뀌어 있는 등) 건드리지 않는다.
    if (
        stage_row["mode"] == "parallel_delegated"
        and stage_row["lead_role_name"] == "개발 계획 수립가"
    ):
        conn.execute(
            "DELETE FROM pipeline_stage_agents WHERE stage_id = ?", (stage_id,)
        )
        for seq, (role_name, instructions) in enumerate(
            implementation_stage[6], start=1
        ):
            if (
                conn.execute(
                    "SELECT 1 FROM agent_pool WHERE role_name = ?", (role_name,)
                ).fetchone()
                is None
            ):
                continue
            conn.execute(
                "INSERT INTO pipeline_stage_agents (stage_id, role_name, seq_order, "
                "instructions) VALUES (?, ?, ?, ?)",
                (stage_id, role_name, seq, instructions),
            )
        conn.execute(
            "UPDATE pipeline_stages SET mode = ?, lead_role_name = NULL, "
            "instructions = ? WHERE id = ?",
            (implementation_stage[1], implementation_stage[3], stage_id),
        )
    else:
        # 고아 role_name만 지우고 빠진 role만 추가(기존 2026-10-28 로직,
        # 관리자가 직접 손본 뒤에도 안전하게 유지) — 전체 교체(위)가
        # 이미 일어났다면 이 분기는 멱등이라 아무 일도 안 한다.
        placeholders = ",".join("?" for _ in _DEV_PIPELINE_STALE_IMPLEMENTATION_ROLES)
        conn.execute(
            "DELETE FROM pipeline_stage_agents WHERE stage_id = ? "
            f"AND role_name IN ({placeholders})",
            (stage_id, *_DEV_PIPELINE_STALE_IMPLEMENTATION_ROLES),
        )
        current_roles = {
            r["role_name"]
            for r in conn.execute(
                "SELECT role_name FROM pipeline_stage_agents WHERE stage_id = ?",
                (stage_id,),
            ).fetchall()
        }
        for seq, (role_name, instructions) in enumerate(
            implementation_stage[6], start=1
        ):
            if role_name in current_roles:
                continue
            if (
                conn.execute(
                    "SELECT 1 FROM agent_pool WHERE role_name = ?", (role_name,)
                ).fetchone()
                is None
            ):
                continue
            conn.execute(
                "INSERT INTO pipeline_stage_agents (stage_id, role_name, seq_order, "
                "instructions) VALUES (?, ?, ?, ?)",
                (stage_id, role_name, seq, instructions),
            )
        conn.execute(
            "UPDATE pipeline_stages SET instructions = ? "
            "WHERE id = ? AND instructions IN (?, ?)",
            (
                implementation_stage[3],
                stage_id,
                _DEV_PIPELINE_STALE_IMPLEMENTATION_INSTRUCTIONS,
                _DEV_PIPELINE_STALE_IMPLEMENTATION_INSTRUCTIONS_V2,
            ),
        )

    conn.execute(
        "UPDATE pipeline_templates SET description = ? "
        "WHERE name = ? AND description = ?",
        (
            reference_pipeline_seeds.DEV_PIPELINE_DESCRIPTION,
            reference_pipeline_seeds.DEV_PIPELINE_NAME,
            _DEV_PIPELINE_STALE_DESCRIPTION,
        ),
    )

    # "개발 계획 수립" 단계 지시문도 요구사항-모듈 매핑을 명시하도록
    # 강화(이 파일 상단 주석 참고) — 같은 안전장치로 옛 문구가 그대로
    # 남아 있을 때만 교체한다.
    planning_stage = next(
        s for s in reference_pipeline_seeds.DEV_PIPELINE_STAGES if s[0] == "개발 계획 수립"
    )
    conn.execute(
        """
        UPDATE pipeline_stages SET instructions = ?
        WHERE id = (
            SELECT ps.id FROM pipeline_stages ps
            JOIN pipeline_templates pt ON pt.id = ps.pipeline_template_id
            WHERE pt.name = ? AND ps.name = '개발 계획 수립'
        ) AND instructions = ?
        """,
        (
            planning_stage[3],
            reference_pipeline_seeds.DEV_PIPELINE_NAME,
            _DEV_PIPELINE_STALE_PLANNING_INSTRUCTIONS,
        ),
    )

    # 사용자 요청(2026-11-08, "게임이 아닌 일상 사무 업무도 일반화") —
    # `checks_requirements_traceability` 컬럼은 이번에 처음 추가돼
    # 모든 기존 단계가 기본값 0이다. "코드 검증" 단계만 1로 맞춘다
    # (단계명과 무관한 플래그로 바꾼 이유는 pipeline_runtime.py 상단
    # 주석 참고) — 아직 이 플래그를 손볼 UI가 없어 관리자 커스터마이즈
    # 충돌 걱정 없이 매 기동마다 그냥 맞춰둔다.
    conn.execute(
        """
        UPDATE pipeline_stages SET checks_requirements_traceability = 1
        WHERE id = (
            SELECT ps.id FROM pipeline_stages ps
            JOIN pipeline_templates pt ON pt.id = ps.pipeline_template_id
            WHERE pt.name = ? AND ps.name = '코드 검증'
        )
        """,
        (reference_pipeline_seeds.DEV_PIPELINE_NAME,),
    )

    # 사용자 요청(2026-11-10, "결과물 출력 유형 다양화") —
    # `output_content_type` 컬럼은 이번에 처음 추가돼 모든 기존 단계가
    # 기본값 'text'다. "구현" 단계만 실제 코드 산출물이라 'code'로
    # 맞춘다(나머지는 기본값 유지 — 아직 렌더링 차이가 없어 굳이
    # 'markdown'으로 바꿀 이유가 없다). 아직 이 플래그를 손볼 UI가
    # 없어 매 기동마다 그냥 맞춰둔다(위 `checks_requirements_
    # traceability` 백필과 같은 관례).
    conn.execute(
        """
        UPDATE pipeline_stages SET output_content_type = 'code'
        WHERE id = (
            SELECT ps.id FROM pipeline_stages ps
            JOIN pipeline_templates pt ON pt.id = ps.pipeline_template_id
            WHERE pt.name = ? AND ps.name = '구현'
        )
        """,
        (reference_pipeline_seeds.DEV_PIPELINE_NAME,),
    )


# "코드 검증"/"QA 기능테스트"/"품질 관리 최종 판정" — 사용자 결정
# (2026-10-28, "자율 개발 크루" 재구성 중강도 Gate 선택)으로 이
# 3단계만 min_gate_pass_ratio를 0.0(항상 통과)에서 0.5로 올린다.
_DEV_PIPELINE_GATED_STAGE_NAMES = ("코드 검증", "QA 기능테스트", "품질 관리 최종 판정")
_DEV_PIPELINE_GATE_PASS_RATIO = 0.5

# 사용자 결정(2026-10-28, 같은 재구성)으로 "자율 개발 크루" 전 멤버를
# 로컬 Ollama에서 클라우드로 전환한다 — `llm.DEFAULT_MODELS["openai"]`
# 와 같은 값(db.py는 llm.py를 import하지 않는 저수준 모듈이라 값만
# 그대로 가져와 썼다 — 이 값이 바뀌면 여기도 같이 바꿔야 한다). 이
# 배포의 `.env`에는 OPENAI_API_KEY만 있고 ANTHROPIC_API_KEY가 없어
# (실측 확인, 2026-10-28) provider를 openai로 잡는다 — anthropic으로
# 잡으면 실제 호출 시점에 API 키 누락으로 조용히 실패한다. 나중에
# ANTHROPIC_API_KEY를 추가하면 관리자가 "AI 에이전트 관리"에서 직접
# anthropic으로 바꿀 수 있다(이 마이그레이션은 `llm_provider='ollama'`
# 인 행만 건드리므로 그 이후엔 다시 안 건드림).
_DEV_CREW_CLOUD_PROVIDER = "openai"
_DEV_CREW_CLOUD_MODEL = "gpt-4o-mini"

# "구현" 단계 6명뿐이던 시절의 옛 role_label → 새 "N단계: ..." 표기.
# 정확히 이 옛 문자열 그대로인 행만 바꾼다(관리자가 이미 고친 라벨은
# 보존).
_DEV_CREW_LEGACY_ROLE_LABEL_MIGRATIONS = (
    (
        "매니저(hierarchical 실행 시 지정)",
        "3단계: 개발 계획 수립 / 4단계: 매니저(hierarchical 위임 리드)",
    ),
    ("백엔드", "4단계: 구현(백엔드)"),
    ("프론트엔드", "4단계: 구현(프론트엔드)"),
    ("데이터/인프라", "4단계: 구현(데이터/인프라)"),
    ("풀스택(유동 배치)", "4단계: 구현(풀스택, 유동 배치)"),
    ("UI/UX", "4단계: 구현(UI/UX)"),
    # 사용자 요청(2026-11-19, "'에이전틱 실행엔진'에 맞춰... 정비해줘.
    # 정비시 기존에 설정된 hierarchical을 배제, sequential 방식으로
    # 해도 좋아") — 바로 위 마이그레이션이 만든 이 문구도 "구현"
    # 단계가 sequential_relay로 전환(2026-11-07)된 뒤로 더는 사실이
    # 아니다(이 역할은 "구현" 단계 참여자가 아니고, 위임을 지휘하는
    # 매니저 개념 자체가 그 단계에 없다) — 같은 체인으로 한 번 더
    # 교체한다.
    (
        "3단계: 개발 계획 수립 / 4단계: 매니저(hierarchical 위임 리드)",
        "3단계: 개발 계획 수립",
    ),
)


def _sync_dev_crew_pipeline_integration(conn: sqlite3.Connection) -> None:
    """사용자 요청(2026-10-28, "팀 관리의 '#3 자율 개발 크루'의 경우
    파이프라인을 참조해 실제 개발 품질을 보장하며 수행될 수 있도록
    재구성해줘") — 오늘 세션에서 "@자율 개발 크루"로 만든 Breakout
    게임이 "Gate 1/5 통과"에도 동작하지 않는 코드였던 근본원인 3가지를
    메운다: (1) 팀을 "소프트웨어 개발 파이프라인"(코드 검증/QA/품질
    관리 전용 단계 + Gate 기준 자동 재시도를 갖춘 엔진)에 연결하고,
    (2) 그 3단계 Gate를 실제로 의미 있게(0.5) 걸고, (3) 전 멤버를
    약한 로컬 Ollama에서 클라우드 모델로 바꾼다. 전부 멱등 — 이미
    적용됐거나 관리자가 그 이후 직접 다른 값으로 바꾼 행은 조건절이
    더 이상 일치하지 않아 건드리지 않는다(이 파일 전체에서 쓰는
    표준 패턴과 동일 — 마이그레이션 이력 테이블이 없는 이 코드베이스
    설계상, "관리자가 의도적으로 다시 되돌린 값"과 "아직 마이그레이션
    전인 옛 값"을 완벽히 구분할 수는 없다는 한계는 그대로 따른다)."""
    template_row = conn.execute(
        "SELECT id FROM pipeline_templates WHERE name = ?",
        (reference_pipeline_seeds.DEV_PIPELINE_NAME,),
    ).fetchone()
    if template_row is None:
        return
    template_id = template_row["id"]

    # (1) 팀 ↔ 파이프라인 연결
    conn.execute(
        "UPDATE teams SET pipeline_template_id = ? "
        "WHERE name = ? AND pipeline_template_id IS NULL",
        (template_id, reference_pipeline_seeds.DEV_CREW_TEAM_NAME),
    )
    newly_integrated = conn.execute("SELECT changes()").fetchone()[0] > 0

    # (2) Gate 임계값
    placeholders = ",".join("?" for _ in _DEV_PIPELINE_GATED_STAGE_NAMES)
    conn.execute(
        "UPDATE pipeline_stages SET min_gate_pass_ratio = ? "
        f"WHERE pipeline_template_id = ? AND name IN ({placeholders}) "
        "AND min_gate_pass_ratio = 0.0",
        (_DEV_PIPELINE_GATE_PASS_RATIO, template_id, *_DEV_PIPELINE_GATED_STAGE_NAMES),
    )

    team_row = conn.execute(
        "SELECT id FROM teams WHERE name = ?",
        (reference_pipeline_seeds.DEV_CREW_TEAM_NAME,),
    ).fetchone()

    # (3) 멤버 구성 — "구현" 단계 6명뿐이던 구성을 파이프라인 7단계
    # 전체가 참조하는 13개 역할로 넓힌다(사용자 요청, 2026-10-28,
    # "파이프라인의 에이전트 구성과 흐름을 참조해... 구성과 업무
    # 흐름을... 재구성") — "팀 관리" 화면만 봐도 이 팀이 요구분석부터
    # 품질 관리까지 전체를 수행한다는 게 보이게 한다. 이미 있는 멤버는
    # `UNIQUE(team_id, member_type, member_id)`에 걸려 INSERT OR
    # IGNORE가 건너뛴다 — 빠진 역할만 새로 추가된다. 아래 (4) 모델
    # 전환보다 **먼저** 해야 새로 추가되는 멤버도 같은 재기동에서 바로
    # 클라우드 모델로 전환된다(순서를 바꾸면 새 멤버는 다음 재기동까지
    # Ollama로 남는다).
    if team_row is not None:
        for (
            role_name,
            role_label,
        ) in reference_pipeline_seeds.DEV_CREW_TEAM_MEMBER_SPECS:
            agent_row = conn.execute(
                "SELECT id FROM agent_pool WHERE role_name = ?", (role_name,)
            ).fetchone()
            if agent_row is None:
                continue
            conn.execute(
                "INSERT OR IGNORE INTO team_members "
                "(team_id, member_type, member_id, role_label) "
                "VALUES (?, 'agent', ?, ?)",
                (team_row["id"], agent_row["id"], role_label),
            )
        # 기존 6명의 role_label도 새 "N단계: ..." 표기로 맞춘다 — 옛
        # 문구 그대로인 행만(관리자가 이미 직접 고친 라벨은 안 건드림).
        #
        # 사용자 요청(2026-11-19, "'에이전틱 실행엔진'에 맞춰... 정비
        # 해줘. 정비시 기존에 설정된 hierarchical을 배제, sequential
        # 방식으로 해도 좋아") — 바로 아래서 지워질 "4단계: 매니저
        # (hierarchical 위임 리드)" 문구가 **지금** 남아 있다는 건 이
        # 팀이 "구현" 단계 sequential_relay 전환(2026-11-07) 이후로
        # 한 번도 재검토되지 않았다는 신호다 — 그 경우에만 팀 자체의
        # hierarchical 설정(`default_process`/`default_manager_agent_
        # id`)도 같이 sequential + 신규 그래프 엔진으로 전환한다
        # (관리자가 이미 직접 들여다본 팀은 이 조건 자체가 다시 안
        # 맞아 건드리지 않음 — 다른 마이그레이션과 같은 안전장치).
        stale_manager_label = (
            "3단계: 개발 계획 수립 / 4단계: 매니저(hierarchical 위임 리드)"
        )
        has_stale_manager_label = (
            conn.execute(
                "SELECT 1 FROM team_members WHERE team_id = ? AND member_type = 'agent' "
                "AND role_label = ?",
                (team_row["id"], stale_manager_label),
            ).fetchone()
            is not None
        )
        for old_label, new_label in _DEV_CREW_LEGACY_ROLE_LABEL_MIGRATIONS:
            conn.execute(
                "UPDATE team_members SET role_label = ? "
                "WHERE team_id = ? AND member_type = 'agent' AND role_label = ?",
                (new_label, team_row["id"], old_label),
            )
        if has_stale_manager_label:
            conn.execute(
                "UPDATE teams SET default_process = 'sequential', "
                "default_manager_agent_id = NULL, use_graph_engine = 1 WHERE id = ?",
                (team_row["id"],),
            )

    # (4) 모델 전환 — "자율 개발 크루" 팀원 중 지금 Ollama인 agent만.
    # 하드코딩 점검 4번(2026-11-21): 예전엔 매 기동마다 돌아, admin이 이 팀원을
    # 다시 Ollama로 바꿔도 재시작할 때마다 OpenAI로 덮어썼다. 팀이 파이프라인에
    # **처음 연결되는 그 기동에서만**(위 (1)이 실제로 행을 바꿨을 때) 한 번 적용한다.
    if team_row is not None and newly_integrated:
        conn.execute(
            """
            UPDATE agent_pool SET llm_provider = ?, llm_model = ?
            WHERE llm_provider = 'ollama'
              AND id IN (
                  SELECT member_id FROM team_members
                  WHERE team_id = ? AND member_type = 'agent'
              )
            """,
            (_DEV_CREW_CLOUD_PROVIDER, _DEV_CREW_CLOUD_MODEL, team_row["id"]),
        )


def _backfill_show_in_conversation(conn: sqlite3.Connection) -> None:
    """`show_in_conversation` 컬럼이 막 추가된 직후 1회만 실행 —
    `augmentation_mode`가 `'tool'`/`'always'`이면서 연결된 도구 중
    `kind`가 MCP(`mcp_stdio`/`mcp_remote`)인 것이 있는 스킬(대화창 기본 AI
    경로에서 구조적으로 실시간 실행이 불가능한 것들 — `pool_registry.
    unsupported_realtime_skill_names()`와 같은 기준)을 0으로 끈다.

    기준 정정(2026-11-23) — 예전엔 `kind != 'native'`면 껐다. 2026-11-18부터
    `custom_python` 도구는 대화창에서도 함수 호출로 실제 실행되므로(`tool_calling.
    py`) 이제는 MCP만 끈다. 예전 기준으로 꺼진 기존 DB의 스킬은 `_show_custom_
    tool_skills_in_conversation()`이 한 번 되살린다."""
    for row in conn.execute(
        "SELECT id, default_tool_pool_ids FROM skill_pool "
        "WHERE augmentation_mode IN ('tool', 'always')"
    ).fetchall():
        tool_ids = json.loads(row["default_tool_pool_ids"])
        has_non_native = False
        for tool_id in tool_ids:
            trow = conn.execute(
                "SELECT kind FROM tool_pool WHERE id = ? AND is_active = 1", (tool_id,)
            ).fetchone()
            if trow is not None and trow["kind"] in ("mcp_stdio", "mcp_remote"):
                has_non_native = True
                break
        if has_non_native:
            conn.execute(
                "UPDATE skill_pool SET show_in_conversation = 0 WHERE id = ?",
                (row["id"],),
            )


# 사용자 요청(2026-11-11, "스킬과 도구를 SW개발업무와 일반업무로 구분하고
# 필터링 되도록 개선. 대화창에서는 일반업무가 표시되고, 개발업무는
# default로 표시되지 않도록 개선") — 레퍼런스 스킬/도구 51개+12개 전수를
# 직접 읽어 분류했다. 기준은 Agent가 아니라 스킬/도구 자체의 실제 내용
# (소프트웨어 코드/아키텍처/테스트/Gate 채점에 한정되는지, 조사·전략·
# 보고서·데이터분석처럼 도메인 무관 지식업무인지)이다 — PM/코드베이스
# 분석가처럼 일부 Agent가 두 도메인 파이프라인에 동시에 재사용되더라도
# 분류는 바뀌지 않는다. 예를 들어 "커버리지체크"/"문서작성"는
# 원래 개발 파이프라인에서 나왔지만 내용 자체가 도메인 무관이고 실제로
# 기획서/C-level 보고서 작성자가 이미 재사용 중이라 'general'로
# 분류했다. 반면 "승인루프"(HITL 승인 루프)는 사용자 재검토(2026-11-11,
# "SKL-NONE-023은 개발업무 Skill로 변경") 후 'sw_dev'로 옮겼다 — PM이
# 사업/제안서/실적보고 파이프라인에서도 이 스킬을 skill_tags로
# 들고 있지만, category는 "그 Agent가 어디서 쓰이는지"가 아니라
# "스킬 자체의 내용"으로 정한다는 원칙(본문 설명)은 그대로다.
_SW_DEV_CATEGORY_SKILL_NAMES = (
    # agent-evaluator/Harness Methodology 생태계 절차 스킬(18개) — 전부
    # CI/Gate/실험 로그 등 AI 에이전트 개발·평가 전용.
    "abtest-decision-workflow",
    "checklist-confidence-audit",
    "claims-audit-ci",
    "config-silent-noop-check",
    "eval-run-directory-hygiene",
    "gate-improvement-loop",
    "harness-autopilot",
    "harness-gate-ci",
    "liveguardrail-standard-config",
    "spec-driven-artifacts",
    "sync-drift-check",
    "tdd-ai-workflow-checklist",
    "threshold-realism-review",
    "unattended-session-recovery",
    "background-real-verification",
    "evidence-based-gate-fix",
    "honest-refuted-experiment-logging",
    "recommend-fix-workflow",
    # "소프트웨어 개발 파이프라인"/"자율 개발 크루" 전용(Spec·코드·아키텍처).
    "요구사항검증",
    "실패모드도출",
    "설계결정기록",
    "코드리뷰체크리스트",
    "커밋메시지작성",
    "장애회고",
    "승인루프",
    "진행관리조율",
    "아키텍처설계",
    "데이터모델설계",
    "모듈분해업무배분",
    "프론트엔드구현",
    "UIUX기획",
    "테스트계획설계",
    "품질인수기준",
    "데이터인프라구현",
    "테스트케이스도출",
    # 사용자 요청(2026-11-18, "SKL-NONE-021 스킬은 개발업무로
    # 분류해줘") — "문서작성"(SKL-NONE-021, README·설계 문서·
    # API 레퍼런스 작성)는 소프트웨어 개발 문서화 전용이라 일반업무가
    # 아닌 개발업무로 재분류.
    "문서작성",
    # 사용자 요청(2026-11-23, "TUL-PYEXEC-003, SKL-TOOL-007 은 일반업무가 아닌 개발업무로
    # 분류해") — 골든셋 완료조건 대비 커버리지 측정은 평가·검증 절차.
    "커버리지체크",
)
_SW_DEV_CATEGORY_TOOL_NAMES = (
    "Gate 조치 지식 조회(recommend_fix)",
    "Spec EARS 린터",
    "실패모드 기계적 도출",
    "테스트 케이스 기계적 도출",
    "골든셋 커버리지 체커",  # 2026-11-23 사용자 요청 — 위 "커버리지체크" 스킬과 같은 이유
)

# 2026-11-23 개발업무로 재분류한 항목 — 기존 DB에 한 번만 적용(`_reclassify_coverage_check_sw_dev`).
_RECLASSIFIED_SW_DEV_SKILLS_2026_11_23 = ("커버리지체크",)
_RECLASSIFIED_SW_DEV_TOOLS_2026_11_23 = ("골든셋 커버리지 체커",)


def _reclassify_coverage_check_sw_dev(conn: sqlite3.Connection) -> None:
    """2026-11-23 "TUL-PYEXEC-003, SKL-TOOL-007 은 일반업무가 아닌 개발업무로 분류해" — 이미
    `category`가 채워진 기존 DB는 위 이름 목록이 다시 적용되지 않으므로 한 번만 바꾼다. 지금도
    기본값(general)인 행만 바꾼다(admin이 이미 다른 분류로 정했으면 그대로)."""
    for table, names in (
        ("skill_pool", _RECLASSIFIED_SW_DEV_SKILLS_2026_11_23),
        ("tool_pool", _RECLASSIFIED_SW_DEV_TOOLS_2026_11_23),
    ):
        conn.execute(
            f"UPDATE {table} SET category = 'sw_dev' WHERE category = 'general' "
            f"AND name IN ({','.join('?' * len(names))})",
            names,
        )


def _backfill_skill_tool_category(conn: sqlite3.Connection) -> None:
    """`category` 컬럼이 막 추가된 직후 1회만 실행(`init_schema()`가
    `show_in_conversation` 백필과 같은 패턴으로 호출) — 나머지(rag·
    graphdb·웹검색류·3C4P SWOT·기획서/보고서/제안서/실적서 작성 가이드·
    데이터 분석기 등)는 컬럼 기본값 'general'을 그대로 둔다."""
    placeholders = ",".join("?" * len(_SW_DEV_CATEGORY_SKILL_NAMES))
    conn.execute(
        f"UPDATE skill_pool SET category = 'sw_dev' WHERE name IN ({placeholders})",
        _SW_DEV_CATEGORY_SKILL_NAMES,
    )
    placeholders = ",".join("?" * len(_SW_DEV_CATEGORY_TOOL_NAMES))
    conn.execute(
        f"UPDATE tool_pool SET category = 'sw_dev' WHERE name IN ({placeholders})",
        _SW_DEV_CATEGORY_TOOL_NAMES,
    )


def _backfill_skill_structured_fields(conn: sqlite3.Connection) -> None:
    """사용자 요청(2026-11-17, "기존 skill들을 개선된 기능에 맞춰
    최적의 품질을 낼 수 있도록 개선해줘") — `output_format`/`examples`/
    `constraints` 컬럼이 막 추가된 직후 1회만 실행(`category` 백필과
    같은 패턴). 이미 description에 암묵적으로 들어 있던 순서/형식·
    금지 문구를 구조화된 필드로 끌어올린 것이라 새 지시를 지어내지
    않는다 — agent-evaluator 절차 스킬 14개(harness-autopilot 등)처럼
    description 자체가 외부의 더 상세한 정의를 가리키는 짧은 포인터뿐인
    경우는, 여기서 근거 없이 내용을 지어내면 그 외부 정의와 어긋날
    위험이 있어 의도적으로 그대로 NULL로 남긴다(일부 스킬만 일부
    필드만 채우는 비대칭도 같은 이유 — 채울 근거가 있는 필드만 채움).
    각 필드는 `IS NULL`일 때만 채워, 관리자가 그 뒤 직접 수정한 값을
    덮어쓰지 않는다."""
    for name, output_format, examples, constraints in (
        ('내부지식검색', None, None, '검색 결과에 없는 내용을 추측해서 답하지 않는다. 답변에 참고한 과거 산출물의 출처(청크 ID 등 식별 정보)를 반드시 명시한다.'),
        ('조직배정관계조회', None, None, '그래프에 없는 관계를 추측해서 만들어내지 않는다. 답변의 출처(근거가 된 배정/관계 데이터)를 반드시 명시한다.'),
        ('gate-improvement-loop', None, None, '한 번에 한 가지만 바꾸고 재측정한다(여러 변경을 동시에 적용하지 않는다).'),
        ('background-real-verification', None, None, '아직 끝나지 않은 실행 결과를 추측해서 보고하지 않는다.'),
        ('evidence-based-gate-fix', None, None, 'SDK 소스 코드로 채점 공식을 확인하기 전에는 추측으로 임계값을 조정하지 않는다.'),
        ('honest-refuted-experiment-logging', None, None, 'refuted 결과를 지우거나 조용히 넘어가지 않는다 — 커밋·문서에 그대로 남긴다.'),
        ('일반웹검색', None, None, '검색된 범위에서만 답하고, 검색되지 않은 사실을 추측하지 않는다. 답변에 사용한 자료의 출처(제목·URL)를 반드시 명시한다.'),
        ('요구사항검증', 'EARS 표기 위반 문장 목록 + [NEEDS CLARIFICATION] 태그 존재 여부.', None, None),
        ('실패모드도출', 'EARS 요구사항 문장별 실패 모드 초안(트리거 + 실패 시 영향 질문).', None, None),
        ('커버리지체크', '완료조건 기준별 키워드 충족 여부 목록 + 전체 커버리지 비율(%).', None, None),
        ('설계결정기록', 'ADR 형식: 결정 → 배경 → 대안 → 결과.', '### 결정\n인증 토큰 저장소를 Redis로 변경\n\n### 배경\n기존 in-memory 세션은 서버 재시작 시 전부 로그아웃됨\n\n### 대안\nDB 세션 테이블(조회 비용 큼) vs Redis(선택)\n\n### 결과\n재시작에도 세션 유지, 운영 인프라에 Redis 의존성 추가', '구현을 시작하기 전에 작성한다(사후 기록이 아니다).'),
        ('코드리뷰체크리스트', '코딩 컨벤션/에러 처리/테스트 커버리지/가독성 4개 항목별 체크 결과.', None, None),
        ('커밋메시지작성', '요약(무엇을 바꿨는지) + 근거(왜 바꿨는지) 구조.', 'fix(payment): 환불 처리 시 중복 차감 버그 수정 — 재시도 시 같은 트랜잭션이 두 번 처리되고 있어 트랜잭션 ID로 멱등성을 보장하도록 수정', None),
        ('문서작성', 'README·설계 문서·API 레퍼런스 등 프로젝트 문서 포맷 — 용어 통일, 예시 포함, 간결한 구조.', None, None),
        ('장애회고', '원인 → 대응 → 재발 방지책 순서의 회고 문서.', None, '특정 개인을 비난하는 표현을 쓰지 않는다(blameless).'),
        ('승인루프', None, None, '반려 사유를 반영하지 않은 재제출을 하지 않는다. 같은 사유가 반복되면 방향 자체를 재검토한다.'),
        ('프레임워크템플릿생성', '지정한 프레임워크(3C/4P/SWOT)의 표준 분석 범주 + 각 범주를 채울 가이드 질문으로 구성된 빈 템플릿.', None, '분석 결과 자체를 대신 채우지 않는다 — 채워야 할 틀만 제공한다.'),
        ('발표자료구성', '슬라이드별 "제목 + 핵심 불릿" 형태의 목록(마크다운 헤더 단위로 나눈 아웃라인).', None, None),
        ('전략분석프레임워크', '3C(자사/고객/경쟁사) → 4P(제품/가격/유통/판촉) → SWOT(강점/약점/기회/위협) 순서의 섹션 구조. 각 범주를 빠짐없이 채운 표 또는 목록.', None, None),
        ('기획서작성', '배경 → 목표 → 실행계획(어떻게·누가·언제) → 예산 → 리스크와 대응방안 순서의 문서. 결론을 맨 앞에 둔다.', None, None),
        ('사업전략수립', '시장·경쟁 분석 → 목표 고객·포지셔닝 → 차별화 전략 → 실행 로드맵 → 성과지표(KPI) 순서의 문서.', None, None),
        ('경영보고서작성', '핵심 요약 → 결론 → 근거 → 권고(Action) 순서로 1페이지 분량에 압축한 문서.', None, None),
        ('테스트케이스도출', '요구사항 문장별 positive/negative/boundary 3종 케이스 골격(Given-When-Then).', None, None),
        ('진행관리조율', '단계별 진행 상태 + 담당 Agent + 블로커(있으면 원인과 다음 조치) 목록.', None, '스스로 산출물을 만들지 않는다 — 조율·보고만 한다.'),
        ('아키텍처설계', '모듈 경계 → 모듈 간 인터페이스 → 기술 스택 선정(대안과 비교) 순서의 설계 문서.', None, '요구사항/스펙에 없는 기능을 임의로 추가하지 않는다. 구현 난이도가 아니라 요구사항 충족을 기준으로 모듈을 나눈다.'),
        ('데이터모델설계', '엔티티·속성 목록 → 관계(1:1/1:N/N:M) → 정규화 수준 → 조회 패턴에 따른 인덱스/비정규화 검토 순서, ERD와 선택 근거 포함.', None, None),
        ('모듈분해업무배분', '모듈 목록(담당자·선행 조건·완료 기준 포함) + 모듈 간 의존관계.', None, '배분 결과에 담당자·선행 조건·완료 기준이 빠지면 안 된다.'),
        ('프론트엔드구현', None, None, '디자인에 없는 화면/동작을 임의로 추가하지 않는다. 접근성(키보드 조작·대비)과 반응형을 기본으로 고려한다.'),
        ('UIUX기획', '사용자 시나리오 → 화면 흐름도 → 화면별 구성 요소와 인터랙션 순서의 기획 문서.', None, '요구사항/스펙에 없는 화면·기능을 임의로 추가하지 않는다.'),
        ('테스트계획설계', '테스트 범위 → 테스트 전략(단위/통합/E2E) → 요구사항별 테스트 케이스(정상/예외/경계값) 순서의 계획 문서.', None, '케이스를 설계만 하고 직접 실행하지 않는다(QA 기능테스터의 역할과 구분).'),
        ('품질인수기준', 'Gate A-G 점수/테스트 커버리지/미해결 결함 심각도 종합 판정 + 부족한 항목 구체적 명시.', None, '개별 모듈이 통과했다는 것만으로 전체 인수 기준 충족을 선언하지 않는다.'),
        ('데이터인프라구현', None, None, '설계에 없는 테이블/컬럼을 임의로 추가하지 않는다. 스키마 변경은 마이그레이션 이력으로 남긴다.'),
        ('실시간웹검색', None, None, '검색된 범위에서만 답하고, 검색되지 않은 사실을 추측하지 않는다. 답변에 사용한 자료의 출처(제목·URL)를 반드시 명시한다.'),
        ('최신정보필수확인', None, None, '검색된 범위에서만 답하고, 검색되지 않은 사실을 추측하지 않는다. 답변에 사용한 자료의 출처(제목·URL)를 반드시 명시한다.'),
        ('데이터분석', '계산된 수치(합계/평균/증감률/표준편차)와 그 수치에 근거한 인사이트 문장을 함께 제시하는 문서.', None, None),
        ('제안서작성', '완전한 HTML 슬라이드 문서(<!DOCTYPE html>~</html>), <style> 인라인, 섹션마다 <section class="slide">로 구분, 16:9(aspect-ratio: 16/9) 비율, 표지 슬라이드 1장으로 시작. WCAG AA 명암비 준수. 슬라이드 네비게이션/전체화면 버튼은 직접 만들지 않음(플랫폼이 자동 주입).', None, None),
        ('실적서작성', '목표 대비/전기 대비 수치 비교표 → 달성·미달 원인 분석 → 차기 계획 순서의 문서. 수치 비교표가 본문의 핵심.', None, None),
        ('딥리서치', '주제별 하위 질문 → 각 하위 질문의 검색 결과 → 교차 검증 결과 → 종합 결론(출처 URL 포함) 순서의 구조화된 보고서.', None, '최소 3회 이상 검색하기 전에는 결론을 내지 않는다. 출처 URL을 생략하지 않는다.'),
        ('AI업계동향브리핑', '기업/분야별 뉴스 사실 목록 → 그로부터의 인사이트(이 조직의 개발/업무에 주는 시사점) 구분 제시.', None, '검색되지 않은 내용을 추측해서 지어내지 않는다. 단순 나열에 머물지 않는다. 뉴스 각 항목마다 출처(제목·URL)를 반드시 명시한다.'),
    ):
        # ('데이터시각화', ...)는 여기 추가하지 않는다 — 이 함수는
        # `output_format` 컬럼이 막 생긴 시점에만(`is_new_output_format_
        # column` 게이트) 1회 호출되므로, 이미 그 컬럼이 있는 기존 설치엔
        # 절대 실행되지 않는다. 그 스킬은 `_backfill_data_visualization_
        # feature()`가 매 기동마다(멱등) 직접 채운다.
        if output_format is not None:
            conn.execute(
                "UPDATE skill_pool SET output_format = ? "
                "WHERE name = ? AND output_format IS NULL",
                (output_format, name),
            )
        if examples is not None:
            conn.execute(
                "UPDATE skill_pool SET examples = ? "
                "WHERE name = ? AND examples IS NULL",
                (examples, name),
            )
        if constraints is not None:
            conn.execute(
                "UPDATE skill_pool SET constraints = ? "
                "WHERE name = ? AND constraints IS NULL",
                (constraints, name),
            )


def _apply_skill_catalog(conn: sqlite3.Connection) -> None:
    """사용자 요청(2026-11-20, "다른 스킬들의 경우도 '지침, 출력형식, 예시,
    제약사항'을 다듬어 스킬 품질을 개선해줘") — `skill_catalog.SKILL_GUIDES`
    를 기존 스킬에 적용한다. 필드 단위로, 비어 있거나 내장 기본값 그대로인
    필드만 바꾼다(`skill_catalog.is_replaceable`) — admin이 화면에서 고친
    필드는 보존한다. 매 기동마다 불러도 이미 적용된 필드는 그대로라 멱등."""
    from hair_platform import skill_catalog  # 순환 import 방지

    for row in conn.execute(
        "SELECT id, name, description, output_format, examples, constraints FROM skill_pool"
    ).fetchall():
        guide = skill_catalog.SKILL_GUIDES.get(row["name"])
        if guide is None:
            continue
        updates = {
            field: getattr(guide, field)
            for field in skill_catalog.FIELDS
            if getattr(guide, field) is not None
            and getattr(guide, field) != row[field]
            and skill_catalog.is_replaceable(row["name"], field, row[field])
        }
        if updates:
            assignments = ", ".join(f"{field} = ?" for field in updates)
            conn.execute(
                f"UPDATE skill_pool SET {assignments} WHERE id = ?",
                (*updates.values(), row["id"]),
            )


def _apply_skill_includes(conn: sqlite3.Connection) -> None:
    """`skill_pool.includes` 컬럼이 처음 생길 때 한 번만 — 기본 포함 관계
    (예: 제안서작성 → 프레젠테이션작성)를 넣는다. 이후 admin이 비우거나
    바꾼 값을 재기동이 되돌리지 않는다. 포함 대상 스킬이 없으면 건너뛴다."""
    from hair_platform import skill_catalog  # 순환 import 방지

    names = {r["name"] for r in conn.execute("SELECT name FROM skill_pool")}
    for name, includes in skill_catalog.SKILL_INCLUDES.items():
        valid = [n for n in includes if n in names and n != name]
        if name in names and valid:
            conn.execute(
                "UPDATE skill_pool SET includes = ? WHERE name = ?",
                (json.dumps(valid, ensure_ascii=False), name),
            )


def _seed_presentation_skill(conn: sqlite3.Connection) -> None:
    """사용자 요청(2026-11-20, "프레젠테이션작성 skill을 새로 추가해줘 …
    품질 높은 HTML 방식의 발표자료 생성") — `presentation_deck.py` 참고.
    없을 때만 만든다(`INSERT OR IGNORE`) — admin이 스킬 관리 화면에서 고친
    문구를 재기동이 덮어쓰지 않는다. 지식 주입형(`augmentation_mode=
    'none'`)이고, 앞 대화의 조사·분석 결과를 받아 정리하는 일이라 대화
    컨텍스트는 기본값(전체 + 첨부)을 쓴다."""
    from hair_platform import presentation_deck  # 순환 import 방지 — 가벼운 모듈

    conn.execute(
        "INSERT OR IGNORE INTO skill_pool (name, description, default_tool_pool_ids, "
        "augmentation_mode, show_in_conversation, category, output_format, examples, "
        "constraints) VALUES (?, ?, '[]', 'none', 1, 'general', ?, ?, ?)",
        (
            presentation_deck.SKILL_NAME,
            presentation_deck.SKILL_DESCRIPTION,
            presentation_deck.SKILL_OUTPUT_FORMAT,
            presentation_deck.SKILL_EXAMPLES,
            presentation_deck.SKILL_CONSTRAINTS,
        ),
    )


# 업무 문서 파이프라인 단계 배정(2026-11-21, "파이프라인 품질 점검") — (단계명, 기존 역할, 새 역할).
# 1단계(요구사항)는 단계명이 파이프라인마다 달라 seq_order=1로 찾는다.
_BUSINESS_STAGE_REASSIGNMENTS: tuple[tuple[str | None, str, str], ...] = (
    (None, "요구사항 분석가", "업무 요구사항 분석가"),
    ("내용 검증", "품질 관리자", "문서 검토자"),
    ("표현 검토", "콘텐츠 카피라이팅", "문서 교정자"),
)


# 기본 제공 MCP 도구 중 읽기 전용으로 확인한 것(검색·조회) — 그 밖의 MCP는 무엇을 하는지 모르므로 "쓸 수 있음"으로 둔다.
_READ_ONLY_BUILTIN_MCP = ("일반 웹 검색(Parallel Search)", "Gate 조치 지식 조회(recommend_fix)")


# 요구사항 정리·검토 역할 기본 추론 끔(2026-11-26 사용자 결정, 개선 계획 3-2 실측). 이전 기본 시드의 추론 설정 —
# 이 값 그대로이거나 추론 설정이 아예 없을 때만 끈다(admin이 바꾼 값은 그대로 둔다).
_LIGHT_ROLE_OLD_TUNING = {
    "업무 요구사항 분석가": {"reasoning": True, "max_reasoning_attempts": 2, "temperature": 0.3},
    "요구사항 분석가": {"reasoning": True, "max_reasoning_attempts": 2, "temperature": 0.4},
    "문서 검토자": {"temperature": 0.2},
    "재무 검토자": {"temperature": 0.2},
}


def _light_roles_reasoning_off(conn: sqlite3.Connection) -> None:
    for role, old in _LIGHT_ROLE_OLD_TUNING.items():
        row = conn.execute("SELECT id, tuning_config FROM agent_pool WHERE role_name = ?", (role,)).fetchone()
        if row is None:
            continue
        try:
            tuning = json.loads(row["tuning_config"] or "{}")
        except ValueError:
            continue
        if "reasoning" in tuning and tuning != old:
            continue  # admin이 바꾼 설정
        tuning["reasoning"] = False
        conn.execute("UPDATE agent_pool SET tuning_config = ? WHERE id = ?", (json.dumps(tuning, ensure_ascii=False), row["id"]))
    conn.commit()

def _mark_unknown_mcp_side_effects(conn: sqlite3.Connection) -> None:
    """기존 설치의 MCP 도구 — 기본 제공 검색·조회 외에는 외부로 쓸 수 있는 도구로 표시(2026-11-26, 개선 계획 1-3).
    admin이 도구 Pool에서 끌 수 있다. 한 번만 실행(`_run_once`)."""
    marks = ",".join("?" * len(_READ_ONLY_BUILTIN_MCP))
    conn.execute(
        f"UPDATE tool_pool SET side_effects = 1 WHERE kind IN ('mcp_stdio', 'mcp_remote') AND name NOT IN ({marks})",  # noqa: S608
        _READ_ONLY_BUILTIN_MCP,
    )
    conn.commit()

def _set_domain_required_tools(conn: sqlite3.Connection) -> None:
    """업무 영역 템플릿에서 계산·점검을 도구로 하도록 지시한 단계에 필수 도구를 건다(2026-11-26, 개선 계획 1-2).
    아직 지정이 없는(NULL) 단계만 — admin이 비우거나(빈 목록) 바꾼 값은 그대로 둔다. 새 설치·기존 설치 모두."""
    from hair_platform import domain_packs

    for spec in domain_packs.PIPELINES:
        for st in spec["stages"]:
            if not st.get("required_tools"):
                continue
            conn.execute(
                "UPDATE pipeline_stages SET required_tools = ? WHERE required_tools IS NULL AND name = ? AND "
                "pipeline_template_id = (SELECT id FROM pipeline_templates WHERE name = ?)",
                (json.dumps(st["required_tools"], ensure_ascii=False), st["name"], spec["name"]),
            )
    conn.commit()

def _reassign_business_pipeline_agents(conn: sqlite3.Connection) -> None:
    """사업/기획·제안서·실적 보고 파이프라인에서 SW 개발용·광고 카피 Agent가 맡던 단계를 업무
    문서용 Agent로 바꾼다. 단계 배정이 지금도 기존 역할 그대로일 때만 바꾼다(admin이 이미 다른
    Agent로 바꿨으면 그대로). 소프트웨어 개발 파이프라인은 건드리지 않는다."""
    names = (
        reference_pipeline_seeds.BUSINESS_PIPELINE_NAME,
        reference_pipeline_seeds.PROPOSAL_PIPELINE_NAME,
        reference_pipeline_seeds.PERFORMANCE_REPORT_PIPELINE_NAME,
    )
    for template_id, in conn.execute(
        f"SELECT id FROM pipeline_templates WHERE name IN ({','.join('?' * len(names))})", names
    ).fetchall():
        for stage_name, old_role, new_role in _BUSINESS_STAGE_REASSIGNMENTS:
            if stage_name is None:
                stage_ids = conn.execute(
                    "SELECT id FROM pipeline_stages WHERE pipeline_template_id = ? AND seq_order = 1",
                    (template_id,),
                ).fetchall()
            else:
                stage_ids = conn.execute(
                    "SELECT id FROM pipeline_stages WHERE pipeline_template_id = ? AND name = ?",
                    (template_id, stage_name),
                ).fetchall()
            for (stage_id,) in stage_ids:
                conn.execute(
                    "UPDATE pipeline_stage_agents SET role_name = ? WHERE stage_id = ? AND role_name = ?",
                    (new_role, stage_id, old_role),
                )


_FULL_GATE_IDS = ["A", "B", "C", "D", "E", "F", "G"]


def _review_pipeline_quality_settings(conn: sqlite3.Connection) -> None:
    """2026-11-21 "모든 파이프라인 점검" — 레퍼런스 파이프라인 설정 정비(한 번만). 지금 값이
    시드 기본값 그대로일 때만 바꾼다(admin이 이미 고친 단계는 그대로).

    1. 검증 단계 Gate: 기본 A-G 전체 → A(+요구사항 추적성). C·D·F·G는 파이프라인 경로에서
       내용과 무관하게 같은 값이라 검증 단계가 늘 기준 미달로 사람 검토에 넘어갔다.
    2. SW 개발: UI 기획자를 "구현" 릴레이 맨 앞에서 "설계" 단계 끝으로.
    3. 제안서: "작성" 단계 출력 형식 text → html(제안서 작성자는 덱을 낸다).
    4. 실적 보고: "데이터 분석" 지시문을 요청의 모든 지표를 분석하도록(매출만 분석했다)."""
    seeds = reference_pipeline_seeds
    for row in conn.execute(
        "SELECT id, gate_ids, checks_requirements_traceability FROM pipeline_stages "
        "WHERE min_gate_pass_ratio > 0"
    ).fetchall():
        if json.loads(row["gate_ids"] or "[]") != _FULL_GATE_IDS:
            continue
        gate_ids = (
            seeds.REVIEW_GATE_IDS_WITH_TRACEABILITY
            if row["checks_requirements_traceability"]
            else seeds.REVIEW_GATE_IDS
        )
        conn.execute(
            "UPDATE pipeline_stages SET gate_ids = ? WHERE id = ?",
            (json.dumps(gate_ids), row["id"]),
        )

    def stage_id(template_name: str, stage_name: str) -> int | None:
        found = conn.execute(
            "SELECT ps.id FROM pipeline_stages ps JOIN pipeline_templates pt "
            "ON pt.id = ps.pipeline_template_id WHERE pt.name = ? AND ps.name = ?",
            (template_name, stage_name),
        ).fetchone()
        return found["id"] if found else None

    impl_id = stage_id(seeds.DEV_PIPELINE_NAME, "구현")
    design_id = stage_id(seeds.DEV_PIPELINE_NAME, "설계")
    if impl_id is not None and design_id is not None:
        first = conn.execute(
            "SELECT id, role_name FROM pipeline_stage_agents WHERE stage_id = ? "
            "ORDER BY seq_order LIMIT 1",
            (impl_id,),
        ).fetchone()
        in_design = conn.execute(
            "SELECT 1 FROM pipeline_stage_agents WHERE stage_id = ? AND role_name = ?",
            (design_id, seeds.UI_PLANNER_ROLE),
        ).fetchone()
        if first is not None and first["role_name"] == seeds.UI_PLANNER_ROLE and not in_design:
            conn.execute("DELETE FROM pipeline_stage_agents WHERE id = ?", (first["id"],))
            next_seq = conn.execute(
                "SELECT COALESCE(MAX(seq_order), 0) + 1 FROM pipeline_stage_agents WHERE stage_id = ?",
                (design_id,),
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO pipeline_stage_agents (stage_id, role_name, seq_order, instructions) "
                "VALUES (?, ?, ?, ?)",
                (design_id, seeds.UI_PLANNER_ROLE, next_seq, seeds.UI_PLANNER_DESIGN_INSTRUCTION),
            )

    proposal_id = stage_id(seeds.PROPOSAL_PIPELINE_NAME, "작성")
    if proposal_id is not None:
        conn.execute(
            "UPDATE pipeline_stages SET output_content_type = 'html' "
            "WHERE id = ? AND output_content_type = 'text'",
            (proposal_id,),
        )

    analysis_id = stage_id(seeds.PERFORMANCE_REPORT_PIPELINE_NAME, "데이터 분석")
    if analysis_id is not None:
        conn.execute(
            "UPDATE pipeline_stages SET instructions = ? WHERE id = ? AND instructions = ?",
            (
                seeds.PERFORMANCE_DATA_ANALYSIS_INSTRUCTION,
                analysis_id,
                seeds.PERFORMANCE_DATA_ANALYSIS_INSTRUCTION_V1,
            ),
        )


def _require_traceability_to_pass(conn: sqlite3.Connection) -> None:
    """2026-11-21 "A안으로 진행해줘" — 요구사항 추적성을 재는 검증 단계는 Gate A와 추적성이 둘 다
    통과해야 통과(임계 0.5 → 1.0). 0.5면 Gate A 하나만 통과해도 넘어가, 제안서 실측에서 요구사항
    11/13인 내용 검증 단계가 그대로 통과했다. 지금 값이 직전 기본값(0.5, A+추적성)일 때만 바꾼다."""
    for row in conn.execute(
        "SELECT id, gate_ids FROM pipeline_stages WHERE min_gate_pass_ratio = 0.5"
    ).fetchall():
        if json.loads(row["gate_ids"] or "[]") == reference_pipeline_seeds.REVIEW_GATE_IDS_WITH_TRACEABILITY:
            conn.execute(
                "UPDATE pipeline_stages SET min_gate_pass_ratio = 1.0 WHERE id = ?", (row["id"],)
            )


def _single_author_runnable_implementation(conn: sqlite3.Connection) -> None:
    """2026-11-22 "모든 파이프라인 점검" C안 — SW 개발 파이프라인 "구현" 단계(한 번만).
    1. 4명 릴레이(데이터/인프라 → 백엔드 → 프론트엔드 → 풀스택)를 풀스택 개발자 단일 단계로 —
       배정이 시드 기본 릴레이 그대로일 때만(admin이 바꾼 구성은 그대로).
    2. 실행 가능성 Gate(`deliverable_runnable`)를 통과해야 넘어가게 — Gate 설정이 기본값(A-G 전체,
       판정 없음 0.0)일 때만. 실측: 실행 가능한 완성본 없이 코드 검증·QA·최종 판정을 통과했다."""
    seeds = reference_pipeline_seeds
    stage = conn.execute(
        "SELECT ps.* FROM pipeline_stages ps JOIN pipeline_templates pt "
        "ON pt.id = ps.pipeline_template_id WHERE pt.name = ? AND ps.name = '구현'",
        (seeds.DEV_PIPELINE_NAME,),
    ).fetchone()
    if stage is None:
        return
    roles = [
        r["role_name"]
        for r in conn.execute(
            "SELECT role_name FROM pipeline_stage_agents WHERE stage_id = ? ORDER BY seq_order",
            (stage["id"],),
        )
    ]
    if stage["mode"] == "sequential_relay" and roles == seeds.IMPLEMENTATION_RELAY_ROLES_V1:
        conn.execute(
            "DELETE FROM pipeline_stage_agents WHERE stage_id = ? AND role_name != ?",
            (stage["id"], seeds.IMPLEMENTATION_ROLE),
        )
        conn.execute(
            "UPDATE pipeline_stage_agents SET seq_order = 1, instructions = '' "
            "WHERE stage_id = ? AND role_name = ?",
            (stage["id"], seeds.IMPLEMENTATION_ROLE),
        )
        conn.execute(
            "UPDATE pipeline_stages SET mode = 'single', instructions = ? WHERE id = ?",
            (seeds.IMPLEMENTATION_INSTRUCTION, stage["id"]),
        )
    if (
        stage["min_gate_pass_ratio"] == 0
        and json.loads(stage["gate_ids"] or "[]") == _FULL_GATE_IDS
    ):
        conn.execute(
            "UPDATE pipeline_stages SET gate_ids = ?, min_gate_pass_ratio = 1.0 WHERE id = ?",
            (json.dumps(seeds.IMPLEMENTATION_GATE_IDS), stage["id"]),
        )


def _review_returns_to_primary_stage(conn: sqlite3.Connection) -> None:
    """2026-11-22 하드코딩·스텁 점검 4순위 — 요구사항 추적성을 재는 검증 단계가 기준 미달이면 그
    템플릿의 '최종 결과물 단계'(작성·구현)로 돌아가 검토 의견을 반영해 다시 쓰게 한다(한 번만,
    단계 이름이 아니라 플래그로 고른다). 이미 되돌릴 단계를 정해 둔 단계는 그대로."""
    for review in conn.execute(
        "SELECT id, pipeline_template_id, seq_order FROM pipeline_stages "
        "WHERE checks_requirements_traceability = 1 AND on_fail_return_stage_id IS NULL"
    ).fetchall():
        primary = conn.execute(
            "SELECT id FROM pipeline_stages WHERE pipeline_template_id = ? "
            "AND is_primary_deliverable = 1 AND seq_order < ? ORDER BY seq_order DESC LIMIT 1",
            (review["pipeline_template_id"], review["seq_order"]),
        ).fetchone()
        if primary is not None:
            conn.execute(
                "UPDATE pipeline_stages SET on_fail_return_stage_id = ? WHERE id = ?",
                (primary["id"], review["id"]),
            )


def _upgrade_chart_generator_tool(conn: sqlite3.Connection) -> None:
    """'차트 생성기' 소스를 2026-11-21 개정판("type" 별칭·빈 구간 null 허용)으로 바꾼다 —
    지금 소스가 직전 시드 원본(V1) 그대로일 때만(admin이 고친 코드는 건드리지 않는다)."""
    row = conn.execute(
        "SELECT id, config FROM tool_pool WHERE name = '차트 생성기' AND kind = 'custom_python'"
    ).fetchone()
    if row is None:
        return
    config = json.loads(row["config"] or "{}")
    if config.get("function_source") != reference_pipeline_seeds.CHART_GENERATOR_SOURCE_V1:
        return
    config["function_source"] = reference_pipeline_seeds.CHART_GENERATOR_SOURCE
    conn.execute(
        "UPDATE tool_pool SET config = ? WHERE id = ?",
        (json.dumps(config, ensure_ascii=False), row["id"]),
    )


def _add_tool_input_formats(conn: sqlite3.Connection) -> None:
    """레퍼런스 도구 설명에 입력 형식을 붙이고 '수치 데이터 분석기'를 관용 입력판으로 바꾼다
    (2026-11-22, `reference_pipeline_seeds.TOOL_INPUT_NOTES` 참고) — 설명·소스가 시드 원본
    그대로인 행만 바꾼다(admin이 고친 값은 건드리지 않는다)."""
    for name, old_description in reference_pipeline_seeds.TOOL_DESCRIPTIONS_V1.items():
        conn.execute(
            "UPDATE tool_pool SET description = ? WHERE name = ? AND description = ? "
            "AND kind = 'custom_python'",
            (
                f"{old_description} {reference_pipeline_seeds.TOOL_INPUT_NOTES[name]}",
                name,
                old_description,
            ),
        )
    row = conn.execute(
        "SELECT id, config FROM tool_pool WHERE name = '수치 데이터 분석기' AND kind = 'custom_python'"
    ).fetchone()
    if row is None:
        return
    config = json.loads(row["config"] or "{}")
    if config.get("function_source") != reference_pipeline_seeds.DATA_ANALYZER_SOURCE_V1:
        return
    config["function_source"] = reference_pipeline_seeds.DATA_ANALYZER_SOURCE
    conn.execute(
        "UPDATE tool_pool SET config = ? WHERE id = ?",
        (json.dumps(config, ensure_ascii=False), row["id"]),
    )


def _show_custom_tool_skills_in_conversation(conn: sqlite3.Connection) -> None:
    """`_backfill_show_in_conversation()`의 예전 기준(`kind != 'native'`)으로 대화창에서
    꺼졌던 스킬 중, 연결 도구가 native/custom_python뿐(MCP 없음)이고 custom_python이 하나
    이상인 것을 다시 켠다(2026-11-23 — 수치 데이터 분석기·차트 생성기 같은 계산 도구는
    대화창에서도 실제로 호출된다). 개발업무로 분류된 스킬은 켜도 여전히 "개발업무 스킬도
    보기"를 켠 사용자에게만 보인다(분류 축은 그대로)."""
    for row in conn.execute(
        "SELECT id, default_tool_pool_ids FROM skill_pool "
        "WHERE show_in_conversation = 0 AND augmentation_mode IN ('tool', 'always')"
    ).fetchall():
        kinds = set()
        for tool_id in json.loads(row["default_tool_pool_ids"] or "[]"):
            trow = conn.execute(
                "SELECT kind FROM tool_pool WHERE id = ? AND is_active = 1", (tool_id,)
            ).fetchone()
            if trow is not None:
                kinds.add(trow["kind"])
        if "custom_python" in kinds and kinds <= {"native", "custom_python"}:
            conn.execute(
                "UPDATE skill_pool SET show_in_conversation = 1 WHERE id = ?", (row["id"],)
            )


def _add_tool_input_schemas(conn: sqlite3.Connection) -> None:
    """레퍼런스 JSON 입력 도구 4개에 입력 스키마를 붙인다(2026-11-23, P1-7) — 소스가 지금 시드와
    같고 스키마가 아직 없는 행만(admin이 소스를 고쳐 입력 형식이 달라졌을 수 있는 도구는 그대로)."""
    current = {name: source for name, _d, source, _t in reference_pipeline_seeds.TOOL_SPECS}
    for name, schema in reference_pipeline_seeds.TOOL_INPUT_SCHEMAS.items():
        row = conn.execute(
            "SELECT id, config FROM tool_pool WHERE name = ? AND kind = 'custom_python'", (name,)
        ).fetchone()
        if row is None:
            continue
        config = json.loads(row["config"] or "{}")
        if "input_schema" in config or config.get("function_source") != current.get(name):
            continue
        config["input_schema"] = schema
        conn.execute(
            "UPDATE tool_pool SET config = ? WHERE id = ?",
            (json.dumps(config, ensure_ascii=False), row["id"]),
        )


def _run_once(conn: sqlite3.Connection, name: str, fn) -> bool:
    """이름 붙은 데이터 마이그레이션을 DB마다 한 번만 실행한다(2026-11-21). 지금까지는
    이력 테이블이 없어 "조건이 맞으면 매 기동마다" 돌리는 방식이라, admin이 의도적으로
    되돌린 값을 재시작 때 다시 덮어쓸 위험이 있었다(하드코딩 점검 4번과 같은 문제)."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "name TEXT PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT (datetime('now')))"
    )
    if conn.execute("SELECT 1 FROM schema_migrations WHERE name = ?", (name,)).fetchone():
        return False
    fn(conn)
    conn.execute("INSERT INTO schema_migrations (name) VALUES (?)", (name,))
    return True


def _apply_one_time_agent_skill_additions(conn: sqlite3.Connection) -> None:
    """`skill_catalog.ONE_TIME_AGENT_SKILL_ADDITIONS`의 연결을 마이그레이션마다 한 번만
    적용한다. 스킬이 실제로 등록돼 있을 때만 붙이고, 이미 있으면 그대로 둔다."""
    from hair_platform import skill_catalog

    def apply(changes: dict):
        def _fn(c: sqlite3.Connection) -> None:
            existing = {r[0] for r in c.execute("SELECT name FROM skill_pool")}
            for role_name, spec in changes.items():
                # 값이 목록이면 추가만, dict면 {"add": [...], "remove": [...]}(2026-11-21).
                add = spec if isinstance(spec, list) else spec.get("add", [])
                remove = set() if isinstance(spec, list) else set(spec.get("remove", []))
                wanted = [s for s in add if s in existing]
                for agent_id, raw in c.execute(
                    "SELECT id, skill_tags FROM agent_pool WHERE role_name = ?", (role_name,)
                ).fetchall():
                    tags = json.loads(raw or "[]")
                    kept = [s for s in tags if s not in remove]
                    merged = kept + [s for s in wanted if s not in kept]
                    if merged != tags:
                        c.execute(
                            "UPDATE agent_pool SET skill_tags = ? WHERE id = ?",
                            (json.dumps(merged, ensure_ascii=False), agent_id),
                        )

        return _fn

    for name, additions in skill_catalog.ONE_TIME_AGENT_SKILL_ADDITIONS.items():
        _run_once(conn, name, apply(additions))


def _seed_catalog_skills(conn: sqlite3.Connection) -> None:
    """사용자 요청(2026-11-21, "우선순위 1 스킬 7개 진행해줘") — `skill_catalog.
    NEW_SKILLS`의 스킬을 없을 때만 만들고(지침 등 네 필드는 바로 뒤
    `_apply_skill_catalog`가 채운다), 처음 만든 스킬만 `AGENT_SKILL_ADDITIONS`의
    기존 Agent에 한 번 연결한다(이미 있는 스킬은 admin이 연결을 바꿨을 수 있어
    건드리지 않는다). "스킬생성"은 연결 도구가 비어 있고 지식 주입형 그대로일
    때만 '스킬·도구 목록 조회' 도구를 붙여 tool 방식으로 바꾼다."""
    from hair_platform import pool_catalog, skill_catalog

    def tool_id(name: str) -> int | None:
        row = conn.execute("SELECT id FROM tool_pool WHERE name = ?", (name,)).fetchone()
        return row[0] if row else None

    created: set[str] = set()
    for name, meta in skill_catalog.NEW_SKILLS.items():
        tid = tool_id(meta["tool"]) if meta.get("tool") else None
        cur = conn.execute(
            "INSERT OR IGNORE INTO skill_pool (name, description, default_tool_pool_ids, "
            "augmentation_mode, show_in_conversation, category) VALUES (?, ?, ?, ?, 1, ?)",
            (
                name,
                skill_catalog.SKILL_GUIDES[name].description,
                json.dumps([tid] if tid else []),
                "tool" if tid else "none",
                meta["category"],
            ),
        )
        if cur.rowcount:
            created.add(name)
    if created:
        for role_name, additions in skill_catalog.AGENT_SKILL_ADDITIONS.items():
            wanted = [s for s in additions if s in created]
            if not wanted:
                continue
            for agent_id, raw in conn.execute(
                "SELECT id, skill_tags FROM agent_pool WHERE role_name = ?", (role_name,)
            ).fetchall():
                tags = json.loads(raw or "[]")
                merged = tags + [s for s in wanted if s not in tags]
                if merged != tags:
                    conn.execute(
                        "UPDATE agent_pool SET skill_tags = ? WHERE id = ?",
                        (json.dumps(merged, ensure_ascii=False), agent_id),
                    )
    lookup_id = tool_id(pool_catalog.TOOL_NAME)
    if lookup_id is not None:
        conn.execute(
            "UPDATE skill_pool SET augmentation_mode = 'tool', default_tool_pool_ids = ? "
            "WHERE name = '스킬생성' AND augmentation_mode = 'none' "
            "AND default_tool_pool_ids IN ('[]', '')",
            (json.dumps([lookup_id]),),
        )


def _backfill_data_visualization_feature(conn: sqlite3.Connection) -> None:
    """사용자 요청(2026-11-18, "excel 파일 등 수치가 담긴 데이터를
    입력받아, 실제 계산을 하고 그래프 출력 등이 가능하도록... 전담
    팀을 구성해") — `reference_pipeline_seeds.seed()`가 새 스킬
    '데이터시각화'/도구 '차트 생성기'는 INSERT OR IGNORE로
    매 기동마다 만들지만, 아래 두 가지는 "이미 존재하는 행을 수정"
    하는 작업이라 그 INSERT 전용 루프로는 못 한다 — 둘 다 멱등(이미
    반영돼 있으면 아무 것도 안 함)이라 매 기동 무조건 호출해도 안전:

    1. '데이터분석'와 같은 패턴으로 `output_format`을 채운다
       (`_backfill_skill_structured_fields()`는 컬럼이 막 생긴
       시점에만 1회 호출되는 게이트가 있어, 이미 그 컬럼이 있는
       기존 설치에선 영원히 안 불린다 — 이 함수가 대신 채운다).
    2. 기존 '데이터 분석가' Agent(`agent_pool`)의 `skill_tags`에
       '데이터시각화'를 추가한다 — `AGENT_SPECS`는 role_name이
       이미 있으면 skip(seed 자체가 "덮어쓰지 않음" 원칙)이라, 이미
       이 Agent가 있는 기존 설치는 `AGENT_SPECS`의 새 skill_tags가
       반영되지 않는다."""
    conn.execute(
        "UPDATE skill_pool SET output_format = "
        "'Chart.js 설정(JSON)을 그대로 담은 ```chart 코드펜스 — "
        "그래프를 말로 서술하지 않는다.' "
        "WHERE name = '데이터시각화' AND output_format IS NULL"
    )
    row = conn.execute(
        "SELECT id, skill_tags FROM agent_pool WHERE role_name = '데이터 분석가'"
    ).fetchone()
    if row is not None:
        tags = json.loads(row["skill_tags"])
        if "데이터시각화" not in tags:
            tags.append("데이터시각화")
            conn.execute(
                "UPDATE agent_pool SET skill_tags = ? WHERE id = ?",
                (json.dumps(tags, ensure_ascii=False), row["id"]),
            )



# 스킬명 변경(2026-11-20, 지식검색→내부지식검색·관계조회→조직배정관계조회)에 맞춘
# 네이티브 도구 이름 변경. 시드는 이름(UNIQUE)으로 멱등 처리하므로, 기존 DB의
# 도구 행을 먼저 새 이름으로 바꿔 두지 않으면 같은 도구가 새 이름으로 하나 더
# 생긴다(2026-11-21 발견 — 실제 hair.db에 id 3349·3350 중복 생성. 스킬은 여전히
# 원래 행을 가리켰다).
_NATIVE_TOOL_RENAMES: tuple[tuple[str, str], ...] = (
    ("VectorDB 지식검색", "VectorDB 내부지식검색"),
    ("GraphDB 관계조회", "GraphDB 조직배정관계조회"),
)


def _tool_is_referenced(conn: sqlite3.Connection, tool_id: int) -> bool:
    if conn.execute(
        "SELECT 1 FROM agent_tool_links WHERE tool_pool_id = ? LIMIT 1", (tool_id,)
    ).fetchone():
        return True
    for (raw,) in conn.execute("SELECT default_tool_pool_ids FROM skill_pool"):
        if tool_id in json.loads(raw or "[]"):
            return True
    return False


def _rename_native_tools(conn: sqlite3.Connection) -> None:
    """원래 행(id 유지 — 스킬·Agent 연결이 그대로 산다)의 이름을 새 이름으로
    바꾼다. 새 이름 행이 이미 있으면(위 중복 버그) 아무 데서도 쓰지 않을 때만
    지우고 바꾼다 — 누가 쓰고 있으면 건드리지 않는다."""
    for old, new in _NATIVE_TOOL_RENAMES:
        old_row = conn.execute("SELECT id FROM tool_pool WHERE name = ?", (old,)).fetchone()
        if old_row is None:
            continue
        new_row = conn.execute("SELECT id FROM tool_pool WHERE name = ?", (new,)).fetchone()
        if new_row is not None:
            if _tool_is_referenced(conn, new_row[0]):
                continue
            conn.execute("DELETE FROM tool_pool WHERE id = ?", (new_row[0],))
        conn.execute("UPDATE tool_pool SET name = ? WHERE id = ?", (new, old_row[0]))
