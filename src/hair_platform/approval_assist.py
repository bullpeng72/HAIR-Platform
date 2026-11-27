"""결재 도우미·위험 기반 결재(2026-11-27, AI Native 분석 3단계 — 9번 "결재 도우미", 4번 "위험 기반 결재").

결재 도우미 — 결재자가 품질 점검 결과·이전 판과의 차이·검토자/점검기 지적·사람 판단이 필요한 부분을 직접 찾아 모아야
했다. 결재 한 건을 한 화면으로 요약한다. LLM을 부르지 않는다(즉시·무료, 결과가 늘 같다).

위험 기반 결재 — 모든 최종 결재가 똑같이 사람을 거쳐, 일이 늘면 사람이 병목이 된다(사람 대기가 리드타임 대부분).
1) 결재마다 위험도(낮음·보통·높음)와 추천을 계산해 도우미에 보여 주고, 사람이 실제로 어떻게 결정했는지 함께 남긴다
   (`approval_recommendations`) — 추천이 사람 판단과 얼마나 맞는지 먼저 검증한다.
2) 검증이 끝난 파이프라인만(낮음 추천 사람 결정 20건 이상·그중 95% 이상 승인, 최근 90일) admin이 **낮은 위험 자동 승인**을
   켤 수 있다. 켜도 다음은 항상 사람에게 간다: 사람 판단 필수 업무, 자동 재시도 실패로 넘어온 단계, 외부 데이터로 쓰기 도구가
   막혔거나 점검기와 검토 판정이 다른 경우(`needs_human_for_gates`), 위험 보통·높음, 그리고 낮음 5건마다 1건(표본 검토).
   검증 조건이 나중에 깨지면(표본에서 반려가 늘면) 켜 둔 상태여도 자동 승인을 멈추고 사람에게 돌린다.
"""

from __future__ import annotations

import difflib
import json
import sqlite3

from hair_platform import audit

HIGH_RISK_DOMAINS = {"legal", "hr", "finance"}
AUTO_MIN_DECISIONS = 20
AUTO_MIN_APPROVAL_RATE = 0.95
AUTO_LOOKBACK_DAYS = 90
SAMPLE_EVERY = 5  # 자동 승인 대상 5건마다 1건은 사람이 본다

CHECK_LABELS = {
    "A": "목표 달성", "B": "행동 무결성", "C": "신뢰성", "D": "응답 시간", "E": "보안", "F": "협업", "G": "근거 제시",
    "numeric_grounding": "숫자 근거", "quote_grounding": "인용 근거", "term_consistency": "용어 일관성",
    "lexicon_check": "용어집", "code_syntax": "코드 문법", "test_execution": "테스트 실행",
    "browser_execution": "화면 실행", "deliverable_runnable": "실행 가능", "requirements_traceability": "요구사항 추적",
    "plan_coverage": "계획 대비 누락", "review_verdict": "검토자 판정", "required_tools": "필수 도구",
    "external_data": "외부 데이터", "checker_crosscheck": "점검기 대조", "delegation_check": "위임",
}
# 이 항목이 미달이면 내용이 사실과 다를 수 있어 위험도를 더 올린다.
_FACT_CHECKS = {"numeric_grounding", "quote_grounding", "term_consistency", "C", "G"}
_NOTE_KEYS = ("note", "summary", "detail", "reason", "message")

SCHEMA = """
CREATE TABLE IF NOT EXISTS approval_recommendations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_stage_id INTEGER NOT NULL UNIQUE,
    template_id INTEGER NOT NULL,
    stage_id INTEGER NOT NULL,
    risk_level TEXT NOT NULL,
    risk_score INTEGER NOT NULL,
    recommended TEXT NOT NULL,
    decided TEXT,
    auto INTEGER NOT NULL DEFAULT 0,
    sampled INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    decided_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_approval_recs_template ON approval_recommendations(template_id, created_at);
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


# ---------------------------------------------------------------- 점검 결과 요약

def _note_of(v: dict) -> str:
    for k in _NOTE_KEYS:
        if isinstance(v.get(k), str) and v[k].strip():
            return v[k].strip()[:200]
    for k in ("issues", "suspects", "missing", "sources", "blocked_tools"):
        items = v.get(k)
        if isinstance(items, list) and items:
            parts = [i if isinstance(i, str) else json.dumps(i, ensure_ascii=False) for i in items[:3]]
            return (", ".join(parts))[:200]
    return ""


def summarize_checks(gate_scores: dict | None) -> list[dict]:
    """점검 항목마다 {key, label, status(ok·warn·fail·not_measured), note}."""
    out = []
    for key, v in (gate_scores or {}).items():
        if key.startswith("_") or not isinstance(v, dict):
            continue
        if v.get("not_measured"):
            status = "not_measured"
        elif v.get("advisory"):
            status = "warn" if v.get("status") == "warn" or v.get("blocked") or v.get("disagree") else "ok"
        elif v.get("passed") is False:
            status = "fail"
        elif v.get("conditional") or v.get("status") == "warn":
            status = "warn"
        else:
            status = "ok"
        if key == "checker_crosscheck" and v.get("disagree"):
            status = "warn"
        out.append({"key": key, "label": CHECK_LABELS.get(key, key), "status": status, "note": _note_of(v)})
    order = {"fail": 0, "warn": 1, "ok": 2, "not_measured": 3}
    out.sort(key=lambda c: (order[c["status"]], c["label"]))
    return out


def _diff(prev: str | None, cur: str | None) -> dict | None:
    if prev is None or cur is None:
        return None
    a, b = prev.splitlines(), cur.splitlines()
    added = removed = 0
    sample: list[str] = []
    for line in difflib.unified_diff(a, b, lineterm="", n=0):
        if line.startswith(("+++", "---", "@@")):
            continue
        if line.startswith("+"):
            added += 1
        elif line.startswith("-"):
            removed += 1
        if len(sample) < 12 and line[:1] in "+-" and line[1:].strip():
            sample.append(line[:160])
    return {"added": added, "removed": removed, "sample": sample,
            "similarity": round(difflib.SequenceMatcher(None, prev[:20000], cur[:20000]).ratio(), 2)}


# ---------------------------------------------------------------- 위험도

def _stage_history(conn: sqlite3.Connection, stage_id: int) -> dict:
    """이 단계의 최근 90일 사람 결정 — 반려율."""
    try:
        r = conn.execute(
            "SELECT COUNT(*) AS n, SUM(CASE WHEN decided = 'rejected' THEN 1 ELSE 0 END) AS rej "
            "FROM approval_recommendations WHERE stage_id = ? AND auto = 0 AND decided IS NOT NULL "
            "AND created_at >= datetime('now', ?)", (stage_id, f"-{AUTO_LOOKBACK_DAYS} days")).fetchone()
    except sqlite3.OperationalError:
        return {"decisions": 0, "rejections": 0}
    return {"decisions": r["n"] or 0, "rejections": r["rej"] or 0}


def assess(conn: sqlite3.Connection, *, domain: str | None, stage_id: int | None, gate_scores: dict | None,
           escalated: bool, requires_judgment: bool, after_human_rejection: bool) -> dict:
    """위험도 점수와 근거. 0~1 낮음, 2~4 보통, 5 이상 높음."""
    score = 0
    factors: list[str] = []
    checks = summarize_checks(gate_scores)
    fails = [c for c in checks if c["status"] == "fail"]
    warns = [c for c in checks if c["status"] == "warn"]
    if requires_judgment:
        score += 5
        factors.append("사람 판단 필수로 지정된 업무")
    if escalated:
        score += 3
        factors.append("자동 재시도로 기준을 맞추지 못해 넘어온 결과")
    if fails:
        score += min(4, 2 * len(fails))
        factors.append("미달 점검: " + ", ".join(c["label"] for c in fails[:4]))
        if any(c["key"] in _FACT_CHECKS for c in fails):
            score += 1
            factors.append("숫자·인용·사실 근거가 확인되지 않은 부분")
    gs = gate_scores or {}
    if (gs.get("external_data") or {}).get("blocked"):
        score += 3
        factors.append("외부 데이터를 읽은 뒤 쓰기 도구가 막힘")
    elif isinstance(gs.get("external_data"), dict) and not gs["external_data"].get("not_measured"):
        score += 1
        factors.append("외부 데이터(사내 DB·메일 등)를 읽어 작성")
    if (gs.get("checker_crosscheck") or {}).get("disagree"):
        score += 3
        factors.append("점검기와 검토자 판정이 다름")
    elif warns:
        score += 1
        factors.append("확인할 점: " + ", ".join(c["label"] for c in warns[:3]))
    if domain in HIGH_RISK_DOMAINS:
        score += 2
        factors.append({"legal": "법무", "hr": "인사", "finance": "재무"}[domain] + " 업무")
    if after_human_rejection:
        score += 1
        factors.append("사람이 한 번 반려한 뒤 다시 쓴 결과")
    if stage_id is not None:
        h = _stage_history(conn, stage_id)
        if h["decisions"] >= 5 and h["rejections"] / h["decisions"] >= 0.2:
            score += 2
            factors.append(f"이 단계는 최근 사람 결정 {h['decisions']}건 중 {h['rejections']}건 반려")
        elif h["decisions"] >= 10 and h["rejections"] == 0:
            score = max(0, score - 1)
            factors.append(f"이 단계는 최근 사람 결정 {h['decisions']}건 모두 승인")
    level = "low" if score <= 1 else ("medium" if score <= 4 else "high")
    advice = {"low": "승인 권장 — 지적된 문제가 없습니다. 내용을 훑어보고 결정하세요.",
              "medium": "내용 확인 후 결정 — 아래 확인할 점을 먼저 보세요.",
              "high": "신중 검토 — 아래 근거를 하나씩 확인하세요."}[level]
    return {"level": level, "score": score, "factors": factors or ["지적된 문제 없음"],
            "recommended": "approve" if level == "low" else "review", "advice": advice}


# ---------------------------------------------------------------- 결재 한 건 요약

def _template_of_stage(conn: sqlite3.Connection, stage_id: int):
    return conn.execute(
        "SELECT t.id, t.name, t.domain FROM pipeline_stages s JOIN pipeline_templates t ON t.id = s.pipeline_template_id "
        "WHERE s.id = ?", (stage_id,)).fetchone()


def _past_rejections(conn: sqlite3.Connection, stage_id: int, exclude_run_stage: int, limit: int = 3) -> list[str]:
    rows = conn.execute(
        "SELECT decision_reason FROM pipeline_run_stages WHERE stage_id = ? AND status = 'rejected' "
        "AND decision_reason IS NOT NULL AND TRIM(decision_reason) != '' AND id != ? ORDER BY id DESC LIMIT ?",
        (stage_id, exclude_run_stage, limit)).fetchall()
    return [r[0][:200] for r in rows]


def _active_lessons(conn: sqlite3.Connection, stage_id: int) -> list[str]:
    try:
        return [r[0] for r in conn.execute(
            "SELECT text FROM stage_lessons WHERE stage_id = ? AND status = 'active' ORDER BY id", (stage_id,))]
    except sqlite3.OperationalError:
        return []


def brief_for_run_stage(conn: sqlite3.Connection, run_stage_id: int) -> dict:
    rs = conn.execute("SELECT * FROM pipeline_run_stages WHERE id = ?", (run_stage_id,)).fetchone()
    if rs is None:
        raise LookupError("pipeline_run_stage_not_found")
    run = conn.execute("SELECT * FROM pipeline_runs WHERE id = ?", (rs["pipeline_run_id"],)).fetchone()
    stage = conn.execute("SELECT * FROM pipeline_stages WHERE id = ?", (rs["stage_id"],)).fetchone()
    tpl = _template_of_stage(conn, rs["stage_id"])
    task = conn.execute("SELECT requires_human_judgment FROM tasks WHERE id = ?", (run["task_id"],)).fetchone()
    total = conn.execute("SELECT COUNT(*) FROM pipeline_stages WHERE pipeline_template_id = ?",
                         (stage["pipeline_template_id"],)).fetchone()[0]
    try:
        gate_scores = json.loads(rs["gate_scores"]) if rs["gate_scores"] else {}
    except ValueError:
        gate_scores = {}
    prev = conn.execute(
        "SELECT output, status, decision_reason, attempt FROM pipeline_run_stages WHERE pipeline_run_id = ? "
        "AND stage_id = ? AND id < ? AND output IS NOT NULL ORDER BY id DESC LIMIT 1",
        (rs["pipeline_run_id"], rs["stage_id"], rs["id"])).fetchone()
    after_rejection = prev is not None and prev["status"] == "rejected"
    requires_judgment = bool(task and task["requires_human_judgment"])
    risk = assess(conn, domain=tpl["domain"] if tpl else None, stage_id=rs["stage_id"], gate_scores=gate_scores,
                  escalated=rs["status"] == "escalated", requires_judgment=requires_judgment,
                  after_human_rejection=after_rejection)
    checks = summarize_checks(gate_scores)
    attention = []
    if rs["status"] == "escalated":
        attention.append(f"자동 재시도 {rs['attempt']}번으로 기준을 맞추지 못해 사람에게 넘어왔습니다 — 승인하면 이대로 다음 단계로, 반려하면 다시 작성합니다.")
    if requires_judgment:
        attention.append("이 업무는 '사람 판단 필수'로 지정돼 있습니다.")
    for c in checks:
        if c["status"] in ("fail", "warn"):
            attention.append(f"{c['label']}: " + (c["note"] or ("미달" if c["status"] == "fail" else "확인 필요")))
    verdict = gate_scores.get("review_verdict") or {}
    if isinstance(verdict, dict) and verdict.get("note"):
        attention.append("검토자 의견: " + str(verdict["note"])[:200])
    return {
        "supported": True,
        "kind": "pipeline_run_stage",
        "title": f"{tpl['name'] if tpl else ''} — {stage['seq_order']}/{total}단계 {stage['name']}",
        "context": {
            "template_id": tpl["id"] if tpl else None, "template_name": tpl["name"] if tpl else None,
            "stage_name": stage["name"], "seq_order": stage["seq_order"], "stage_count": total,
            "attempt": rs["attempt"], "max_retry": stage["max_retry"], "status": rs["status"],
            "waiting_since": rs["created_at"], "run_id": run["id"], "task_id": run["task_id"],
            "requires_human_approval": bool(stage["requires_human_approval"]),
        },
        "risk": risk,
        "checks": checks,
        "checks_summary": _checks_summary(checks),
        "attention": attention,
        "changes": ({**(_diff(prev["output"], rs["output"]) or {}), "against": f"{prev['attempt']}차 시도",
                     "previous_rejection_reason": prev["decision_reason"] if after_rejection else None}
                    if prev is not None else None),
        "past_rejections": _past_rejections(conn, rs["stage_id"], rs["id"]),
        "active_lessons": _active_lessons(conn, rs["stage_id"]),
    }


def brief_for_deliverable(conn: sqlite3.Connection, deliverable_id: int) -> dict:
    d = conn.execute("SELECT * FROM deliverables WHERE id = ?", (deliverable_id,)).fetchone()
    if d is None:
        raise LookupError("deliverable_not_found")
    ev = conn.execute("SELECT gate_scores FROM agent_evaluations WHERE deliverable_id = ? ORDER BY id DESC LIMIT 1",
                      (deliverable_id,)).fetchone()
    try:
        gate_scores = json.loads(ev["gate_scores"]) if ev else {}
    except ValueError:
        gate_scores = {}
    task = conn.execute("SELECT role_name, requires_human_judgment FROM tasks WHERE id = ?", (d["task_id"],)).fetchone()
    prev = conn.execute("SELECT content FROM deliverables WHERE id = ?", (d["revises_id"],)).fetchone() if d["revises_id"] else None
    requires_judgment = bool(task and task["requires_human_judgment"])
    risk = assess(conn, domain=None, stage_id=None, gate_scores=gate_scores, escalated=False,
                  requires_judgment=requires_judgment, after_human_rejection=prev is not None)
    checks = summarize_checks(gate_scores)
    attention = [f"{c['label']}: " + (c["note"] or ("미달" if c["status"] == "fail" else "확인 필요"))
                 for c in checks if c["status"] in ("fail", "warn")]
    if requires_judgment:
        attention.insert(0, "이 업무는 '사람 판단 필수'로 지정돼 있습니다.")
    if not gate_scores:
        attention.append("자동 품질 점검 기록이 없는 결과물입니다(사람이 작성했거나 점검 전) — 내용을 직접 확인하세요.")
    return {
        "supported": True, "kind": "deliverable",
        "title": f"결과물 #{d['id']} — {task['role_name'] if task else ''}",
        "context": {"task_id": d["task_id"], "author_type": d["author_type"], "created_at": d["created_at"],
                    "status": d["status"], "revises_id": d["revises_id"]},
        "risk": risk, "checks": checks, "checks_summary": _checks_summary(checks), "attention": attention,
        "changes": ({**(_diff(prev["content"], d["content"]) or {}), "against": f"이전 판 #{d['revises_id']}",
                     "previous_rejection_reason": None} if prev is not None else None),
        "past_rejections": [], "active_lessons": [],
    }


def _checks_summary(checks: list[dict]) -> dict:
    measured = [c for c in checks if c["status"] != "not_measured"]
    return {"measured": len(measured), "ok": sum(1 for c in measured if c["status"] == "ok"),
            "warn": sum(1 for c in measured if c["status"] == "warn"),
            "fail": sum(1 for c in measured if c["status"] == "fail")}


def brief_for_approval(conn: sqlite3.Connection, approval_id: int) -> dict:
    a = conn.execute("SELECT * FROM approvals WHERE id = ?", (approval_id,)).fetchone()
    if a is None:
        raise LookupError("approval_not_found")
    if a["subject_type"] == "pipeline_run_stage":
        out = brief_for_run_stage(conn, a["subject_id"])
    elif a["subject_type"] == "deliverable":
        out = brief_for_deliverable(conn, a["subject_id"])
    else:
        return {"supported": False, "kind": a["subject_type"], "approval_id": approval_id}
    out["approval_id"] = approval_id
    return out


# ---------------------------------------------------------------- 추천 대 실제 결정

def record_decision(conn: sqlite3.Connection, run_stage_id: int, approved: bool) -> None:
    """파이프라인 단계 결재의 최종 결정을 그 시점 추천과 함께 남긴다(커밋은 호출자). 같은 단계 기록은 한 줄."""
    try:
        b = brief_for_run_stage(conn, run_stage_id)
        rs = conn.execute("SELECT stage_id FROM pipeline_run_stages WHERE id = ?", (run_stage_id,)).fetchone()
        conn.execute(
            "INSERT INTO approval_recommendations (run_stage_id, template_id, stage_id, risk_level, risk_score, "
            "recommended, decided, decided_at) VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now')) "
            "ON CONFLICT(run_stage_id) DO UPDATE SET decided = excluded.decided, decided_at = excluded.decided_at",
            (run_stage_id, b["context"]["template_id"], rs["stage_id"], b["risk"]["level"], b["risk"]["score"],
             b["risk"]["recommended"], "approved" if approved else "rejected"),
        )
    except (sqlite3.OperationalError, LookupError):
        pass


def agreement(conn: sqlite3.Connection, template_id: int) -> dict:
    """최근 90일 추천 대 사람 결정 — 위험도별 건수·승인율, 자동 승인 수, 자동 승인을 켤 수 있는지."""
    try:
        rows = conn.execute(
            "SELECT risk_level, decided, auto, sampled FROM approval_recommendations WHERE template_id = ? "
            "AND created_at >= datetime('now', ?)", (template_id, f"-{AUTO_LOOKBACK_DAYS} days")).fetchall()
    except sqlite3.OperationalError:
        rows = []
    by = {}
    for lvl in ("low", "medium", "high"):
        human = [r for r in rows if r["risk_level"] == lvl and not r["auto"] and r["decided"]]
        appr = sum(1 for r in human if r["decided"] == "approved")
        by[lvl] = {"decisions": len(human), "approved": appr, "approval_rate": (appr / len(human)) if human else None}
    low = by["low"]
    eligible = low["decisions"] >= AUTO_MIN_DECISIONS and (low["approval_rate"] or 0) >= AUTO_MIN_APPROVAL_RATE
    enabled = bool(_policy_on(conn, template_id))
    return {
        "by_level": by, "auto_approved": sum(1 for r in rows if r["auto"]),
        "eligible": eligible, "enabled": enabled, "active": enabled and eligible,
        "requirement": {"min_decisions": AUTO_MIN_DECISIONS, "min_approval_rate": AUTO_MIN_APPROVAL_RATE,
                        "lookback_days": AUTO_LOOKBACK_DAYS, "sample_every": SAMPLE_EVERY},
    }


# ---------------------------------------------------------------- 낮은 위험 자동 승인

def _policy_on(conn: sqlite3.Connection, template_id: int) -> int:
    try:
        r = conn.execute("SELECT auto_approve_low_risk FROM pipeline_templates WHERE id = ?", (template_id,)).fetchone()
    except sqlite3.OperationalError:
        return 0
    return int(r[0] or 0) if r else 0


def set_policy(conn: sqlite3.Connection, actor_id: int, template_id: int, enabled: bool) -> dict:
    from hair_platform import process_learning

    if conn.execute("SELECT 1 FROM pipeline_templates WHERE id = ?", (template_id,)).fetchone() is None:
        raise LookupError("pipeline_template_not_found")
    before = bool(_policy_on(conn, template_id))
    if enabled and not agreement(conn, template_id)["eligible"]:
        raise ValueError("auto_approval_not_eligible")
    if before == enabled:
        return agreement(conn, template_id)
    conn.execute("UPDATE pipeline_templates SET auto_approve_low_risk = ? WHERE id = ?", (1 if enabled else 0, template_id))
    process_learning.record_change(conn, actor_id, template_id, None, "approval_policy",
                                   "결재 정책 — 낮은 위험 자동 승인 " + ("켬" if enabled else "끔"),
                                   {"auto_approve_low_risk": before}, {"auto_approve_low_risk": enabled})
    audit.record(conn, actor_id, "approval_policy_changed", "pipeline_template", template_id,
                 before={"auto_approve_low_risk": before}, after={"auto_approve_low_risk": enabled})
    conn.commit()
    return agreement(conn, template_id)


def auto_approval_check(conn: sqlite3.Connection, stage, task_requires_judgment: bool, gate_scores: dict,
                        needs_human_for_gates: bool) -> dict | None:
    """사람 승인 단계를 통과한 결과를 자동 승인해도 되면 위험도 결과를, 아니면 None.
    사람 승인이 '단계 설정' 때문일 때만 대상이다(업무의 사람 판단 필수·Gate 사유는 항상 사람)."""
    if not stage["requires_human_approval"] or task_requires_judgment or needs_human_for_gates:
        return None
    template_id = stage["pipeline_template_id"]
    if not _policy_on(conn, template_id):
        return None
    if not agreement(conn, template_id)["eligible"]:
        return None
    tpl = conn.execute("SELECT domain FROM pipeline_templates WHERE id = ?", (template_id,)).fetchone()
    risk = assess(conn, domain=tpl["domain"] if tpl else None, stage_id=stage["id"], gate_scores=gate_scores,
                  escalated=False, requires_judgment=False, after_human_rejection=False)
    if risk["level"] != "low":
        return None
    seen = conn.execute(
        "SELECT COUNT(*) FROM approval_recommendations WHERE template_id = ? AND risk_level = 'low' AND (auto = 1 OR sampled = 1)",
        (template_id,)).fetchone()[0]
    if (seen + 1) % SAMPLE_EVERY == 0:
        return {**risk, "sample": True}
    return risk


def mark_sampled(conn: sqlite3.Connection, stage, run_stage_id: int, risk: dict) -> None:
    """표본 검토로 사람에게 보낸 낮은 위험 건(커밋은 호출자) — 결정은 결재 때 채워진다."""
    conn.execute(
        "INSERT OR IGNORE INTO approval_recommendations (run_stage_id, template_id, stage_id, risk_level, risk_score, "
        "recommended, sampled) VALUES (?, ?, ?, 'low', ?, 'approve', 1)",
        (run_stage_id, stage["pipeline_template_id"], stage["id"], risk["score"]))


def record_auto_approval(conn: sqlite3.Connection, actor_id: int, stage, run_stage_id: int, risk: dict) -> None:
    """자동 승인 기록(커밋은 호출자) — 감사 기록과 추천 대 결정 표에 남는다."""
    conn.execute(
        "INSERT OR IGNORE INTO approval_recommendations (run_stage_id, template_id, stage_id, risk_level, risk_score, "
        "recommended, decided, auto, decided_at) VALUES (?, ?, ?, 'low', ?, 'approve', 'approved', 1, datetime('now'))",
        (run_stage_id, stage["pipeline_template_id"], stage["id"], risk["score"]))
    audit.record(conn, actor_id, "pipeline_stage_auto_approved", "pipeline_run_stage", run_stage_id,
                 after={"risk_score": risk["score"], "factors": risk["factors"]})
