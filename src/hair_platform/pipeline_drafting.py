"""업무 설명으로 파이프라인 초안 만들기(2026-11-27, AI Native 분석 3단계 — 3번 "자연어 프로세스 설계").

새 파이프라인은 "파이프라인 관리"에서 단계·모드·역할·품질 기준·재시도·되돌아갈 단계·필수 도구를 손으로 다 채워야 해서,
플랫폼 내부 개념을 아는 사람만 만들 수 있었다. 여기서는 업무를 글로 설명하면(예시 결과물을 붙여도 됨) 플랫폼 기본
LLM이 단계 초안을 제안하고, 사람이 고친 뒤 저장한다.

- 초안은 **등록된 Agent 역할·도구만** 쓴다. LLM이 지어낸 역할은 `missing_role`로 표시해 사람이 고르게 하고(저장 거부),
  모르는 도구·품질 기준·형식은 버린다.
- 마지막 단계는 항상 사람 승인으로 둔다(안전 원칙 — 사람이 끄고 싶으면 저장 뒤 파이프라인 관리에서).
- LLM을 못 쓰거나 응답이 깨지면 규칙 초안(작성 → 검토(실패 시 작성으로) → 최종 확인)으로 대신한다 — 예외를 올리지 않는다.
- 설명과 비슷한 기존 파이프라인을 함께 알려 준다(새로 만들기 전에 그걸 쓰도록).
- `draft()`는 DB에 아무것도 쓰지 않는다. 저장은 `create_from_draft()`.
"""

from __future__ import annotations

import json
import re
import sqlite3

from hair_platform import llm, pool_catalog

MIN_STAGES, MAX_STAGES = 2, 8
GATES = ["A", "B", "C", "D", "E", "F", "G"]
OUTPUT_TYPES = ("text", "markdown", "html", "code")
MODES = ("single", "sequential_relay")
DOMAINS = ("general", "software_dev", "business_planning", "proposal_writing", "performance_reporting",
           "finance", "sales", "legal", "hr")
_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)

_SYSTEM = (
    "당신은 업무 프로세스 설계자입니다. 사용자가 설명한 업무를 AI Agent들이 단계별로 처리하는 파이프라인으로 설계하세요.\n"
    "규칙:\n"
    "- 단계는 보통 3~6개(최대 8개). 각 단계는 글로 된 결과물 하나를 만들고 다음 단계가 그것을 이어받습니다.\n"
    "- AI는 메일 발송·결제·시스템 등록 같은 외부 실행을 하지 못합니다. '발송'·'전달'·'요청' 같은 단계는 만들지 말고, 마지막 결과를 사람이 승인한 뒤 실행한다고 보세요. 업무가 끝난 뒤의 일(피드백 수집 등)도 넣지 마세요.\n"
    "- 역할(roles)은 반드시 아래 '등록된 Agent 역할' 목록의 이름을 그대로 쓰세요. 맞는 역할이 없으면 가장 가까운 것을 고르세요.\n"
    "- 작성한 결과를 다른 관점에서 점검하는 '검토' 단계를 두고, 검토에서 기준 미달이면 되돌아갈 작성 단계를 return_to(0부터 센 단계 번호)로 지정하세요.\n"
    "- 사람이 판단해야 하는 곳(법적 효력·대외 발송·금액 확정·인사 결정 등)에 requires_human_approval=true를 두고 why_human에 이유를 적으세요. 마지막 단계는 사람 승인입니다.\n"
    "- 숫자 계산·데이터 조회처럼 도구가 필요한 단계에만 required_tools를 아래 '등록된 도구' 이름으로 넣으세요.\n"
    "- instructions는 그 단계가 할 일을 구체적으로(무엇을 입력으로 받아 어떤 형식으로 무엇을 내놓는지) 2~4문장으로.\n"
    "반드시 JSON 객체 하나만 출력하세요:\n"
    '{"name": "...", "domain": "general|software_dev|business_planning|proposal_writing|performance_reporting|finance|sales|legal|hr", '
    '"description": "...", "stages": [{"name": "...", "mode": "single|sequential_relay", "roles": ["..."], '
    '"instructions": "...", "output_content_type": "text|markdown|html|code", "gate_ids": ["A",...], '
    '"requires_human_approval": false, "why_human": "", "required_tools": [], "return_to": null}]}'
)


def _roles(conn: sqlite3.Connection) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT role_name, COALESCE(goal, '') AS goal FROM agent_pool WHERE status = 'active' ORDER BY role_name")]


def _tools(conn: sqlite3.Connection) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT name, COALESCE(description, '') AS description FROM tool_pool WHERE is_active = 1 ORDER BY name")]


def similar_templates(conn: sqlite3.Connection, text: str, limit: int = 3) -> list[dict]:
    """설명과 이름·설명이 겹치는 기존 파이프라인(LLM 없이, 낱말 어간 겹침)."""
    q = pool_catalog._stems(text)
    if not q:
        return []
    out = []
    for r in conn.execute("SELECT id, name, description, domain FROM pipeline_templates WHERE is_active = 1"):
        own = pool_catalog._stems(f"{r['name']} {r['description'] or ''}")
        score = len(q & own)
        if score >= 2:
            out.append({"template_id": r["id"], "name": r["name"], "domain": r["domain"], "score": score})
    out.sort(key=lambda x: -x["score"])
    return out[:limit]


def _rule_draft(conn: sqlite3.Connection, description: str) -> dict:
    """LLM 없이 만드는 기본 초안 — 작성 → 검토(미달이면 작성으로) → 최종 확인(사람 승인)."""
    suggested = pool_catalog.suggest_roles(conn, description, limit=3)
    roles = [r["role_name"] for r in _roles(conn)]
    writer = suggested[0] if suggested else (roles[0] if roles else "")
    reviewer = suggested[1] if len(suggested) > 1 else writer
    first = re.split(r"[.,·\n]", description.strip())[0].strip() or "새 업무"
    title = first[:24].rstrip() + ("…" if len(first) > 24 else "")
    return {
        "name": f"{title} 파이프라인",
        "domain": "general",
        "description": description.strip()[:500],
        "stages": [
            {"name": "초안 작성", "mode": "single", "roles": [writer],
             "instructions": "업무 요청과 첨부 자료를 읽고 요구사항을 빠짐없이 반영한 초안을 작성하세요. 근거가 된 자료를 함께 적으세요.",
             "output_content_type": "markdown", "gate_ids": GATES, "requires_human_approval": False, "why_human": "",
             "required_tools": [], "return_to": None},
            {"name": "검토", "mode": "single", "roles": [reviewer],
             "instructions": "앞 단계 초안을 요청 원문과 대조해 빠진 요구사항·틀린 숫자·근거 없는 주장을 찾아 고친 최종본을 내세요. 고친 곳을 끝에 목록으로 적으세요.",
             "output_content_type": "markdown", "gate_ids": GATES, "requires_human_approval": False, "why_human": "",
             "required_tools": [], "return_to": 0},
            {"name": "최종 확인", "mode": "single", "roles": [reviewer],
             "instructions": "검토를 마친 결과를 받는 사람이 바로 쓸 수 있는 형태로 정리하세요. 결정이 필요한 부분은 맨 앞에 따로 적으세요.",
             "output_content_type": "markdown", "gate_ids": GATES, "requires_human_approval": True,
             "why_human": "최종 결과를 내보내기 전 사람이 확인합니다.", "required_tools": [], "return_to": None},
        ],
    }


def _sanitize(conn: sqlite3.Connection, raw: dict, description: str) -> dict:
    """LLM 초안 정제 — 모르는 역할은 표시, 모르는 도구·기준·형식은 버림, 단계 수·되돌아갈 단계·마지막 사람 승인 보정."""
    role_names = {r["role_name"] for r in _roles(conn)}
    tool_names = {t["name"] for t in _tools(conn)}
    raw_in = raw.get("stages")
    stages_in: list = raw_in if isinstance(raw_in, list) else []
    stages = []
    for s in stages_in[:MAX_STAGES]:
        if not isinstance(s, dict) or not str(s.get("name") or "").strip():
            continue
        roles = [str(r).strip() for r in (s.get("roles") or []) if str(r).strip()] if isinstance(s.get("roles"), list) else []
        roles = roles[:4] or [""]
        mode = s.get("mode") if s.get("mode") in MODES else ("sequential_relay" if len(roles) > 1 else "single")
        if mode == "single":
            roles = roles[:1]
        gates = [g for g in (s.get("gate_ids") or []) if g in GATES] if isinstance(s.get("gate_ids"), list) else []
        tools = [t for t in (s.get("required_tools") or []) if t in tool_names] if isinstance(s.get("required_tools"), list) else []
        stages.append({
            "name": str(s["name"]).strip()[:60],
            "mode": mode,
            "roles": roles,
            "missing_roles": [r for r in roles if r not in role_names],
            "instructions": str(s.get("instructions") or "").strip()[:2000],
            "output_content_type": s.get("output_content_type") if s.get("output_content_type") in OUTPUT_TYPES else "markdown",
            "gate_ids": gates or list(GATES),
            "requires_human_approval": bool(s.get("requires_human_approval")),
            "why_human": str(s.get("why_human") or "").strip()[:200],
            "required_tools": tools,
            "return_to": s.get("return_to") if isinstance(s.get("return_to"), int) else None,
        })
    if len(stages) < MIN_STAGES:
        raise ValueError("too_few_stages")
    for i, s in enumerate(stages):
        rt = s["return_to"]
        if rt is not None and not (0 <= rt < i):  # 앞 단계로만 되돌아간다
            s["return_to"] = None
        # 검토 단계인데 되돌아갈 곳이 없으면 바로 앞 단계(작성)로 — 같은 검토를 되풀이하지 않고 작성자가 고치게.
        if s["return_to"] is None and i > 0 and any(k in s["name"] for k in ("검토", "점검", "검증", "리뷰")):
            s["return_to"] = i - 1
        if s["requires_human_approval"] and not s["why_human"]:
            s["why_human"] = "이 단계 결과는 사람이 확인합니다."
    if not stages[-1]["requires_human_approval"]:
        stages[-1]["requires_human_approval"] = True
        stages[-1]["why_human"] = stages[-1]["why_human"] or "최종 결과를 내보내기 전 사람이 확인합니다(기본 안전 원칙)."
    return {
        "name": str(raw.get("name") or "").strip()[:80] or f"{description.strip()[:30]} 파이프라인",
        "domain": raw.get("domain") if raw.get("domain") in DOMAINS else "general",
        "description": str(raw.get("description") or description).strip()[:500],
        "stages": stages,
    }


def draft(conn: sqlite3.Connection, description: str, example_output: str | None = None) -> dict:
    """{"draft", "source": "llm"|"rule", "note", "similar"} — DB에 쓰지 않는다. 설명이 너무 짧으면 ValueError."""
    description = (description or "").strip()
    if len(description) < 10:
        raise ValueError("description_too_short")
    similar = similar_templates(conn, description)
    roles, tools = _roles(conn), _tools(conn)
    note = ""
    try:
        provider, model = llm.resolve_provider_and_model(None, None, conn=conn)
        prompt = (
            "## 업무 설명\n" + description[:4000]
            + ("\n\n## 예시 결과물(이런 결과가 나와야 함)\n" + example_output.strip()[:3000] if example_output and example_output.strip() else "")
            + "\n\n## 등록된 Agent 역할\n" + "\n".join(f"- {r['role_name']}: {r['goal'][:80]}" for r in roles[:80])
            + "\n\n## 등록된 도구\n" + ("\n".join(f"- {t['name']}: {t['description'][:80]}" for t in tools[:60]) or "(없음)")
        )
        raw_text = llm.call_llm(provider, model, _SYSTEM, prompt)
        m = _JSON_RE.search(raw_text or "")
        if not m:
            raise ValueError("no_json")
        result = _sanitize(conn, json.loads(m.group(0)), description)
        source = "llm"
    except Exception as exc:  # noqa: BLE001 — 실패하면 규칙 초안으로(예외를 올리지 않는다)
        result = _rule_draft(conn, description)
        source = "rule"
        note = "AI 초안을 만들지 못해 기본 초안(작성 → 검토 → 최종 확인)을 보여 드립니다" + (
            f" — {type(exc).__name__}" if not isinstance(exc, ValueError) else "") + "."
        for s in result["stages"]:
            s["missing_roles"] = [r for r in s["roles"] if r not in {x["role_name"] for x in roles}]
    return {"draft": result, "source": source, "note": note, "similar": similar,
            "available_roles": [r["role_name"] for r in roles], "available_tools": [t["name"] for t in tools]}


def create_from_draft(conn: sqlite3.Connection, actor_id: int, d: dict) -> int:
    """사람이 고친 초안으로 파이프라인을 만든다 — 모든 역할이 등록돼 있어야 한다."""
    from hair_platform import pipeline_runtime as pr
    from hair_platform import process_learning

    role_names = {r["role_name"] for r in _roles(conn)}
    tool_names = {t["name"] for t in _tools(conn)}
    raw_stages = d.get("stages")
    stages: list[dict] = [x for x in raw_stages if isinstance(x, dict)] if isinstance(raw_stages, list) else []
    if not (MIN_STAGES <= len(stages) <= MAX_STAGES):
        raise ValueError("stage_count_out_of_range")
    for s in stages:
        roles = [r for r in (s.get("roles") or []) if r]
        if not roles or any(r not in role_names for r in roles):
            raise ValueError("unknown_role")
        if s.get("mode", "single") not in MODES or (s.get("mode", "single") == "single" and len(roles) != 1):
            raise ValueError("invalid_mode")
        if any(t not in tool_names for t in s.get("required_tools") or []):
            raise ValueError("unknown_tool")
        if not str(s.get("name") or "").strip():
            raise ValueError("empty_stage_name")
    tid = pr.create_pipeline_template(conn, actor_id, str(d.get("name") or "").strip(), str(d.get("description") or ""),
                                      str(d.get("domain")) if d.get("domain") in DOMAINS else "general")
    stage_ids: list[int] = []
    for s in stages:
        stage_ids.append(pr.add_stage(
            conn, actor_id, tid, str(s["name"]).strip(), s.get("mode", "single"),
            [(r, "") for r in s["roles"]],
            instructions=str(s.get("instructions") or ""),
            gate_ids=[g for g in (s.get("gate_ids") or GATES) if g in GATES] or GATES,
            min_gate_pass_ratio=0.5,
            requires_human_approval=bool(s.get("requires_human_approval")),
            output_content_type=str(s.get("output_content_type")) if s.get("output_content_type") in OUTPUT_TYPES else "markdown",
        ))
    for i, s in enumerate(stages):
        fields: dict = {}
        rt = s.get("return_to")
        if isinstance(rt, int) and 0 <= rt < i:
            fields["on_fail_return_stage_id"] = stage_ids[rt]
        if s.get("required_tools"):
            fields["required_tools"] = list(s["required_tools"])
        if fields:
            pr.update_stage(conn, actor_id, stage_ids[i], **fields)
    process_learning.record_change(conn, actor_id, tid, None, "template_created",
                                   f"파이프라인 생성(업무 설명 초안에서) — 단계 {len(stages)}개", None,
                                   {"stages": [s["name"] for s in stages]})
    conn.commit()
    return tid
