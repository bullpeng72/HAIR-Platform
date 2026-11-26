"""점검기와 AI 검토의 판정 대조(2026-11-27, 개선 계획 2-4 후반).

규칙 기반 점검기(예: 채용 공고 점검기)와 AI 검토자는 서로 다른 눈이다. 검토자가 "승인"했는데 같은 결과물을 점검기에
다시 넣으면 차별 소지 문구나 빠진 필수 항목이 나온다면, 둘 중 하나가 틀렸다 — 어느 쪽이 맞는지는 사람이 정한다.

- 검토 단계(되돌아갈 작성 단계가 지정된 단계)에서, 작성 단계의 최신 통과 결과물에서 점검 대상 부분(`## 채용 공고` 등)만
  잘라 점검기를 **다시** 돌린다. 작성 Agent가 자기 손으로 부른 점검 결과를 믿지 않고 최종본으로 확인하기 위해서다
  (작성자는 점검 뒤에 문구를 또 고칠 수 있다). 결과물의 `## 점검 결과` 표에는 고치기 전 원래 문구가 들어 있어 전체를
  넣으면 그 문구가 다시 걸리므로, 대상 부분을 못 찾으면 대조하지 않는다.
- 대조 결과는 참고용 검사(통과 비율에 넣지 않음)로 남기고, **검토는 승인했는데 점검기가 문제를 찾은 경우**만 사람
  결재로 올린다. 검토가 "보완 요청"이면 어차피 다시 쓰므로 대조할 필요가 없다.
"""

from __future__ import annotations

import json
import re
import sqlite3

# 점검기 이름 → (점검할 결과물 부분의 소제목, 문제로 세는 결과 키)
CROSSCHECKS: dict[str, dict] = {
    "채용 공고 점검기": {"section": "채용 공고", "issue_keys": ("주의_문구", "빠진_필수_항목")},
}


def extract_section(text: str, heading: str) -> str | None:
    """`## <heading>`부터 같은 수준(또는 더 높은) 다음 소제목 전까지. 없으면 None."""
    lines = (text or "").splitlines()
    start, level = None, 0
    for i, line in enumerate(lines):
        m = re.match(r"^(#{1,3})\s*(.+?)\s*$", line)
        if m and m.group(2).strip().startswith(heading):
            start, level = i, len(m.group(1))
            break
    if start is None:
        return None
    body = []
    for line in lines[start + 1:]:
        m = re.match(r"^(#{1,6})\s", line)
        if m and len(m.group(1)) <= level:
            break
        body.append(line)
    section = "\n".join(body).strip()
    return section or None


def _tool_names(stage) -> list[str]:
    try:
        names = json.loads(stage["required_tools"] or "[]")
    except (ValueError, TypeError, IndexError, KeyError):
        return []
    return [n for n in names if isinstance(n, str)]


def _run_checker(conn: sqlite3.Connection, name: str, text: str) -> dict | None:
    from hair_platform import pool_registry

    row = conn.execute("SELECT * FROM tool_pool WHERE name = ? AND is_active = 1", (name,)).fetchone()
    if row is None:
        return None
    try:
        config = json.loads(row["config"] or "{}")
    except ValueError:
        return None
    source = config.get("function_source")
    if not source:
        return None
    raw = pool_registry._run_custom_python_tool(source, json.dumps({"text": text}, ensure_ascii=False),
                                                int(config.get("timeout_seconds") or 10))
    try:
        result = json.loads(raw)
    except (ValueError, TypeError):
        return None
    return result if isinstance(result, dict) else None


def crosscheck(conn: sqlite3.Connection, run_id: int, stage, verdict: dict | None) -> dict | None:
    """검토 단계의 대조 결과(참고용 Gate 항목) — 대조할 점검기·결과물이 없으면 None."""
    if not verdict or verdict.get("not_measured") or not verdict.get("passed"):
        return None  # 판정이 없거나 보완 요청 — 대조 불필요
    source_stage_id = stage["on_fail_return_stage_id"]
    if not source_stage_id:
        return None
    source_stage = conn.execute("SELECT * FROM pipeline_stages WHERE id = ?", (source_stage_id,)).fetchone()
    names = [n for n in dict.fromkeys(_tool_names(stage) + (_tool_names(source_stage) if source_stage else []))
             if n in CROSSCHECKS]
    if not names:
        return None
    out_row = conn.execute(
        "SELECT output FROM pipeline_run_stages WHERE pipeline_run_id = ? AND stage_id = ? "
        "AND status IN ('passed', 'approved', 'awaiting_approval') ORDER BY id DESC LIMIT 1",
        (run_id, source_stage_id),
    ).fetchone()
    if out_row is None or not out_row["output"]:
        return None
    findings, checked = [], []
    for name in names:
        spec = CROSSCHECKS[name]
        section = extract_section(out_row["output"], spec["section"])
        if section is None:
            continue
        result = _run_checker(conn, name, section)
        if result is None:
            continue
        checked.append(name)
        for key in spec["issue_keys"]:
            for item in result.get(key) or []:
                if isinstance(item, dict) and item.get("문구"):
                    text = str(item["문구"]) + " — " + str(item.get("확인 사항", ""))
                else:
                    text = str(item)
                findings.append({"checker": name, "kind": key.replace("_", " "), "text": text[:200]})
    if not checked:
        return None
    disagree = bool(findings)
    return {
        "advisory": True,
        "passed": not disagree,
        "status": "warn" if disagree else "pass",
        "score": 0.0 if disagree else 1.0,
        "not_measured": False,
        "disagree": disagree,
        "checkers": checked,
        "findings": findings[:20],
        "reason": ("검토는 '" + str(verdict.get("verdict") or "승인") + "'인데 점검기가 최종본에서 " + str(len(findings)) +
                   "건을 찾았습니다 — 어느 쪽이 맞는지 사람이 확인") if disagree else "점검기도 문제를 찾지 못함 — 판정 일치",
    }
