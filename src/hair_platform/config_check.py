"""기본 구성 점검 — 운영 DB가 지금 코드의 기본 구성(새로 설치하면 생기는 상태)과 어디가 다른지.

사용자 요청(2026-11-23, "P2 진행해줘" — P2-10 구성 관리·환경 간 불일치). 실측: 운영 DB의 "사업/기획
업무 처리 파이프라인" 2단계에 데이터 분석가가 빠져 있었다 — 시드가 "없는 행만 추가"(`INSERT OR
IGNORE`)라 이미 있던 단계의 참여자 변경은 기존 DB에 반영되지 않는다. 지금까지는 이런 어긋남을 알
방법이 없었다(이번에도 스킬·도구 맵을 다시 만들다 우연히 발견).

기준은 메모리 DB에 지금 코드의 시드를 그대로 깔아 만든다(정의를 따로 복제하지 않는다). 차이는 두 종류:
- **빠진 것**(자동 반영 가능): 단계의 참여 역할, Agent의 기본 스킬, 기본 스킬·도구 자체.
- **값이 다른 것**(표시만): 단계의 모드·판정 기준·사람 승인, 기본에 없는 단계. admin이 일부러 바꿨을
  수 있어 덮어쓰지 않는다 — 고치려면 해당 관리 화면에서 직접 고친다.
"""

from __future__ import annotations

import json
import sqlite3

from hair_platform import audit


def _reference() -> sqlite3.Connection:
    from hair_platform import db

    ref = db.get_connection(":memory:")
    db.init_schema(ref)
    return ref


def _stages(conn: sqlite3.Connection) -> dict[tuple[str, str], dict]:
    out: dict[tuple[str, str], dict] = {}
    for s in conn.execute(
        "SELECT s.*, t.name AS tpl FROM pipeline_stages s JOIN pipeline_templates t "
        "ON t.id = s.pipeline_template_id"
    ).fetchall():
        roles = conn.execute(
            "SELECT role_name, instructions FROM pipeline_stage_agents WHERE stage_id = ? ORDER BY seq_order, id",
            (s["id"],),
        ).fetchall()
        out[(s["tpl"], s["name"])] = {
            "id": s["id"], "seq": s["seq_order"], "mode": s["mode"], "lead": s["lead_role_name"],
            "gate_ids": json.loads(s["gate_ids"] or "[]"), "ratio": s["min_gate_pass_ratio"],
            "human": bool(s["requires_human_approval"]),
            "roles": [r["role_name"] for r in roles],
            "instructions": {r["role_name"]: r["instructions"] for r in roles},
        }
    return out


def check(conn: sqlite3.Connection) -> list[dict]:
    """차이 목록 — 각 항목: id(적용할 때 쓰는 키)·kind·target·detail·fixable."""
    ref = _reference()
    items: list[dict] = []
    live_tpls = {r[0] for r in conn.execute("SELECT name FROM pipeline_templates")}
    live_stages, ref_stages = _stages(conn), _stages(ref)
    for (tpl, stage), r in sorted(ref_stages.items(), key=lambda kv: (kv[0][0], kv[1]["seq"])):
        if tpl not in live_tpls:
            continue  # 템플릿 자체를 admin이 지웠으면 손대지 않는다
        live = live_stages.get((tpl, stage))
        target = f"{tpl} · {r['seq']}. {stage}"
        if live is None:
            items.append({"id": f"stage_missing:{tpl}:{stage}", "kind": "stage_missing", "target": target,
                          "detail": "기본 구성에 있는 단계가 없습니다", "fixable": False})
            continue
        # 모드 업그레이드(2026-11-25 — 사업/기획 "자료 조사·분석"이 순차 릴레이 → 계획 분담). 설치된 단계가 아직
        # 예전 기본(순차 릴레이, 기본에 없는 역할을 admin이 더하지 않음)이면 반영 가능으로 낸다 — 모드·리드를 바꾸고
        # 리드 역할을 넣는다. admin이 바꾼 단계(다른 모드·추가 역할)는 예전처럼 차이만 보여 준다.
        upgrade = (
            live["mode"] == "sequential_relay" and r["mode"] == "plan_merge" and r["lead"]
            and set(live["roles"]) <= set(r["roles"])
        )
        if upgrade:
            items.append({"id": f"stage_mode:{tpl}:{stage}", "kind": "stage_mode_upgrade", "target": target,
                          "detail": f"모드를 계획 분담(리드({r['lead']})가 팀원별 하위 작업을 나누고 결과를 합침)으로 바꿉니다 "
                          f"(현재: 순차 릴레이 {' → '.join(live['roles'])}) — 실측 29% 빠르고 결과물이 한 편으로 정리됨",
                          "fixable": True})
        for role in r["roles"]:
            if upgrade and role == r["lead"]:
                continue  # 리드는 모드 업그레이드 항목이 함께 넣는다
            if role not in live["roles"]:
                items.append({"id": f"stage_role:{tpl}:{stage}:{role}", "kind": "stage_role_missing",
                              "target": target, "detail": f"참여 역할 「{role}」이 빠져 있습니다 "
                              f"(기본: {' → '.join(r['roles'])} / 현재: {' → '.join(live['roles']) or '없음'})",
                              "fixable": True})
        diffs = []
        if live["mode"] != r["mode"] and not upgrade:
            diffs.append(f"모드 {live['mode']}(기본 {r['mode']})")
        if sorted(live["gate_ids"]) != sorted(r["gate_ids"]) or abs((live["ratio"] or 0) - (r["ratio"] or 0)) > 1e-9:
            diffs.append(f"판정 {live['gate_ids']}·{live['ratio']}(기본 {r['gate_ids']}·{r['ratio']})")
        if live["human"] != r["human"]:
            diffs.append(f"사람 승인 {'필요' if live['human'] else '불필요'}(기본 {'필요' if r['human'] else '불필요'})")
        if diffs:
            items.append({"id": f"stage_setting:{tpl}:{stage}", "kind": "stage_setting_differs", "target": target,
                          "detail": "; ".join(diffs), "fixable": False})
    live_agents = {r["role_name"]: json.loads(r["skill_tags"]) for r in conn.execute("SELECT role_name, skill_tags FROM agent_pool")}
    for row in ref.execute("SELECT role_name, skill_tags FROM agent_pool ORDER BY id"):
        role, ref_skills = row["role_name"], json.loads(row["skill_tags"])
        if role not in live_agents:
            continue  # Agent를 admin이 지웠으면 손대지 않는다
        missing = [s for s in ref_skills if s not in live_agents[role]]
        if missing:
            items.append({"id": f"agent_skills:{role}", "kind": "agent_skills_missing", "target": f"Agent 「{role}」",
                          "detail": f"기본 스킬 {', '.join(missing)}이(가) 없습니다", "fixable": True,
                          "missing": missing})
    live_skills = {r[0] for r in conn.execute("SELECT name FROM skill_pool")}
    for (name,) in ref.execute("SELECT name FROM skill_pool ORDER BY name"):
        if name not in live_skills:
            items.append({"id": f"skill:{name}", "kind": "skill_missing", "target": f"스킬 「{name}」",
                          "detail": "기본 스킬이 없습니다", "fixable": True})
    live_tools = {r[0] for r in conn.execute("SELECT name FROM tool_pool")}
    for (name,) in ref.execute("SELECT name FROM tool_pool ORDER BY name"):
        if name not in live_tools:
            items.append({"id": f"tool:{name}", "kind": "tool_missing", "target": f"도구 「{name}」",
                          "detail": "기본 도구가 없습니다", "fixable": True})
    ref.close()
    return items


def _apply_stage_role(conn, ref, tpl: str, stage: str, role: str) -> None:
    live = _stages(conn)[(tpl, stage)]
    r = _stages(ref)[(tpl, stage)]
    conn.execute(
        "INSERT INTO pipeline_stage_agents (stage_id, role_name, seq_order, instructions) VALUES (?, ?, 0, ?)",
        (live["id"], role, r["instructions"].get(role, "")),
    )
    # 순서: 기본 구성의 순서를 따르고, 기본에 없는 현재 역할은 원래 순서대로 뒤에 둔다.
    current = live["roles"] + [role]
    ordered = [x for x in r["roles"] if x in current] + [x for x in live["roles"] if x not in r["roles"]]
    for seq, name in enumerate(ordered, 1):
        conn.execute("UPDATE pipeline_stage_agents SET seq_order = ? WHERE stage_id = ? AND role_name = ?",
                     (seq, live["id"], name))


def apply(conn: sqlite3.Connection, actor_id: int, item_ids: list[str]) -> dict:
    """고를 수 있는(fixable) 항목만 반영한다. 반환: 반영한 항목 id 목록과 건너뛴 항목."""
    wanted = set(item_ids)
    items = {i["id"]: i for i in check(conn)}
    ref = _reference()
    applied, skipped = [], []
    # 도구 → 스킬 → 나머지 순서로 반영한다 — 스킬이 가리킬 도구가 먼저 있어야 한다.
    order = {"tool": 0, "skill": 1}
    for item_id in sorted(item_ids, key=lambda i: order.get(i.split(":", 1)[0], 2)):
        item = items.get(item_id)
        if item is None or not item["fixable"] or item_id not in wanted:
            skipped.append(item_id)
            continue
        kind = item["kind"]
        if kind == "stage_role_missing":
            _, tpl, stage, role = item_id.split(":", 3)
            _apply_stage_role(conn, ref, tpl, stage, role)
        elif kind == "stage_mode_upgrade":
            _, tpl, stage = item_id.split(":", 2)
            r = _stages(ref)[(tpl, stage)]
            live = _stages(conn)[(tpl, stage)]
            if r["lead"] not in live["roles"]:
                _apply_stage_role(conn, ref, tpl, stage, r["lead"])
            conn.execute("UPDATE pipeline_stages SET mode = ?, lead_role_name = ? WHERE id = ?",
                         (r["mode"], r["lead"], live["id"]))
        elif kind == "agent_skills_missing":
            role = item_id.split(":", 1)[1]
            row = conn.execute("SELECT id, skill_tags FROM agent_pool WHERE role_name = ?", (role,)).fetchone()
            tags = json.loads(row["skill_tags"]) + item["missing"]
            conn.execute("UPDATE agent_pool SET skill_tags = ? WHERE id = ?",
                         (json.dumps(tags, ensure_ascii=False), row["id"]))
        elif kind == "tool_missing":
            name = item_id.split(":", 1)[1]
            src = ref.execute("SELECT * FROM tool_pool WHERE name = ?", (name,)).fetchone()
            conn.execute("INSERT INTO tool_pool (name, description, kind, config, category) VALUES (?, ?, ?, ?, ?)",
                         (src["name"], src["description"], src["kind"], src["config"], src["category"]))
        elif kind == "skill_missing":
            name = item_id.split(":", 1)[1]
            src = ref.execute("SELECT * FROM skill_pool WHERE name = ?", (name,)).fetchone()
            ref_tool_names = [ref.execute("SELECT name FROM tool_pool WHERE id = ?", (i,)).fetchone()[0]
                              for i in json.loads(src["default_tool_pool_ids"])]
            live_ids = [r[0] for r in (conn.execute("SELECT id FROM tool_pool WHERE name = ?", (n,)).fetchone()
                                       for n in ref_tool_names) if r]
            conn.execute(
                "INSERT INTO skill_pool (name, description, default_tool_pool_ids, augmentation_mode, "
                "show_in_conversation, category, output_format, examples, constraints, history_mode, "
                "use_attachments, includes) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (src["name"], src["description"], json.dumps(live_ids), src["augmentation_mode"],
                 src["show_in_conversation"], src["category"], src["output_format"], src["examples"],
                 src["constraints"], src["history_mode"], src["use_attachments"], src["includes"]),
            )
        applied.append(item_id)
        audit.record(conn, actor_id, "config_check_applied", "config_check", None,
                     after={"item": item_id, "detail": item["detail"]})
    conn.commit()
    ref.close()
    return {"applied": applied, "skipped": skipped}
