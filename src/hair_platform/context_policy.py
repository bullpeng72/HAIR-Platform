"""대화 응답의 컨텍스트 정책 — "이전 대화를 얼마나 넣을지" × "첨부 자료를 쓸지".

사용자 요청(2026-11-20, "대화의 경우 일반적으로 컨텍스트로 '이전 대화 10개
+ 요약'을 입력으로 사용하거나, 이전 대화 내용을 사용하지 않는 두가지
유형으로 구분하려고 해 ... 각 대화마다 이전 대화 컨텍스트 입력 옵션을 두는게
좋을지 더 나은 방법이 있을지" → 제안 C안 "스킬별 기본값 + 메시지 단위
덮어쓰기" 승인).

대화마다 on/off를 두지 않는 이유: 한 대화 안에서도 스킬이 계속 바뀐다
(실측: 대화 #26에 딥리서치·전략분석·제안서작성·내부지식검색이 섞여 있었다) —
내부지식검색 한 번 하려고 대화 옵션을 껐다 켜야 하고, 잊으면 이전 답변이
섞여 출처가 흐려진다. 그래서 정책을 **스킬**에 두고(admin이 조정), 예외만
메시지 단위로 덮어쓴다.

두 축:
- `history`: 'full'(최근 10건 + 요약 — 기존 동작) | 'recent'(직전 1~2턴만)
  | 'none'(현재 질문만)
- `attachments`: 이 대화에 색인된 첨부자료 메모리를 쓸지

`none`인데 질문이 앞 대화를 가리키면("그거", "위 내용", "더 자세히" …)
`recent`로 자동 상향한다(`auto_upgraded`) — 추가 LLM 호출 없는 규칙 기반.
"""

from __future__ import annotations

import json
import re
import sqlite3

HISTORY_MODES = ("full", "recent", "none")
_HISTORY_RANK = {"none": 0, "recent": 1, "full": 2}

DEFAULT_POLICY: dict = {"history": "full", "attachments": True}

# 앞 대화를 가리키는 표현 — 문장 어디에 있든 잡는다. 너무 넓으면 내부지식검색이
# 매번 recent로 올라가 버리므로, 지시어·후속 요청에 해당하는 것만 둔다.
_FOLLOWUP_RE = re.compile(
    r"(이거|그거|저거|이것|그것|저것|이건|그건|이걸|그걸|위의?\s|위\s?내용|위에서|"
    r"앞에서|앞서|앞의|방금|아까|이전\s?(답변|내용|대화)|더\s?자세히|자세히\s?설명|"
    r"계속|이어서|다시\s?(설명|정리|작성)|그럼|그러면)"
)


def is_followup(text: str) -> bool:
    return bool(_FOLLOWUP_RE.search(text or ""))


def _policy_from_row(row: sqlite3.Row | None) -> dict:
    if row is None:
        return dict(DEFAULT_POLICY)
    history = row["history_mode"] if row["history_mode"] in HISTORY_MODES else "full"
    attachments = row["use_attachments"]
    return {
        "history": history,
        "attachments": True if attachments is None else bool(attachments),
    }


def for_skills(conn: sqlite3.Connection, skills: list[str]) -> dict:
    """스킬 목록의 정책을 합친다 — 하나라도 더 많은 맥락이 필요하면 그쪽을
    따른다(가장 포괄적인 값). 스킬이 없으면 기본(full + 첨부)."""
    if not skills:
        return dict(DEFAULT_POLICY)
    placeholders = ",".join("?" * len(skills))
    rows = conn.execute(
        f"SELECT history_mode, use_attachments FROM skill_pool WHERE name IN ({placeholders})",
        tuple(skills),
    ).fetchall()
    if not rows:
        return dict(DEFAULT_POLICY)
    policies = [_policy_from_row(r) for r in rows]
    return {
        "history": max((p["history"] for p in policies), key=_HISTORY_RANK.__getitem__),
        "attachments": any(p["attachments"] for p in policies),
    }


def for_agent(conn: sqlite3.Connection, agent_id: int) -> dict:
    """Agent `@멘션`용 — Agent에 덮어쓰기 값이 있으면 그것, 없으면 그
    Agent 스킬들의 가장 포괄적인 값."""
    row = conn.execute(
        "SELECT skill_tags, history_mode, use_attachments FROM agent_pool WHERE id = ?",
        (agent_id,),
    ).fetchone()
    if row is None:
        return dict(DEFAULT_POLICY)
    policy = for_skills(conn, json.loads(row["skill_tags"] or "[]"))
    if row["history_mode"] in HISTORY_MODES:
        policy["history"] = row["history_mode"]
    if row["use_attachments"] is not None:
        policy["attachments"] = bool(row["use_attachments"])
    return policy


def finalize(base: dict, override: str | None, question: str) -> dict:
    """메시지 단위 덮어쓰기와 후속 질문 자동 상향을 적용한 최종 정책.
    `source`는 화면 배지/감사용 — 'skill'(기본값) | 'override'(사용자가
    고름) | 'auto'(후속 질문이라 자동 상향)."""
    policy = {**base, "source": "skill", "auto_upgraded": False}
    if override in HISTORY_MODES:
        policy["history"] = override
        policy["source"] = "override"
    elif policy["history"] == "none" and is_followup(question):
        policy["history"] = "recent"
        policy["source"] = "auto"
        policy["auto_upgraded"] = True
    return policy


def latest_human_message(conn: sqlite3.Connection, conversation_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT id, content, context_override FROM conversation_messages "
        "WHERE conversation_id = ? AND author_type = 'human' ORDER BY id DESC LIMIT 1",
        (conversation_id,),
    ).fetchone()


def retrieval_query(
    conn: sqlite3.Connection, conversation_id: int, question: str, policy: dict
) -> str:
    """지식/첨부 검색어 — 기본은 현재 질문. 앞 대화를 참조하는 후속 질문이고
    이력을 쓰는 정책이면 직전 사용자 질문을 앞에 붙인다("그거 더 자세히"
    만으로는 무엇을 찾을지 알 수 없다)."""
    if policy["history"] == "none" or not is_followup(question):
        return question
    prev = conn.execute(
        "SELECT content FROM conversation_messages WHERE conversation_id = ? "
        "AND author_type = 'human' ORDER BY id DESC LIMIT 1 OFFSET 1",
        (conversation_id,),
    ).fetchone()
    return f"{prev['content']}\n{question}" if prev else question
