"""결과물 부분 재작성 제안 — "이 부분만 다시 써 줘"와 AI 수정안 수락·거절(2026-11-24, P1-6).

원래 분석: 결과물 본문 직접 수정·댓글 재호출은 있었지만 부분 수정 요청과 수정안을 고르는 흐름이 없었다.
기존 "AI에게 요청"(`deliverables.suggest_content_fix`)은 **문서 전체**를 다시 써 편집창에 채운다 — 모델이
요청하지 않은 곳까지 바꿀 수 있고, 무엇이 바뀌었는지 비교가 없고, 무엇을 제안·결정했는지 남지 않는다.

이 모듈은
1. 바꿀 부분을 고른다 — 마크다운 제목으로 나눈 **섹션** 또는 붙여 넣은 **정확한 원문 구간**(한 번만 나와야 함).
2. AI는 **그 부분만** 다시 쓴다. 나머지는 서버가 원문 그대로 이어 붙이므로 바뀔 수 없다.
3. 수정안은 저장만 되고 결과물은 바뀌지 않는다 — 사람이 비교를 보고 **수락**하면 기존 편집 경로
   (`deliverables.update_deliverable_content` — 권한·수정 이력·감사로그)로 반영되고, **거절**하면 기록만 남는다.
4. 제안 뒤 결과물이 바뀌었으면(다른 수정이 먼저 반영) 원래 구간을 다시 찾아 그 자리에만 반영하고, 찾을 수
   없으면 반영하지 않는다(덮어쓰기 방지).
권한은 결과물 편집과 같다(admin/manager 항상, 그 외엔 본인이 쓴 draft만).
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3

from hair_platform import audit

MAX_TARGET_CHARS = 12000
CONTEXT_CHARS = 12000
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")


class ProposalError(ValueError):
    """사람에게 그대로 보여 줄 수 있는 사유."""


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS deliverable_edit_proposals ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " deliverable_id INTEGER NOT NULL REFERENCES deliverables(id) ON DELETE CASCADE,"
        " target_label TEXT NOT NULL,"
        " target_start INTEGER NOT NULL,"
        " target_end INTEGER NOT NULL,"
        " original_text TEXT NOT NULL,"
        " proposed_text TEXT NOT NULL,"
        " instruction TEXT NOT NULL,"
        " base_hash TEXT NOT NULL,"
        " llm_provider TEXT, llm_model TEXT,"
        " warnings TEXT NOT NULL DEFAULT '[]',"
        " status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'accepted', 'rejected')),"
        " created_by INTEGER REFERENCES users(id),"
        " created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,"
        " decided_by INTEGER REFERENCES users(id),"
        " decided_at TEXT)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_edit_proposals_deliverable ON deliverable_edit_proposals(deliverable_id)"
    )


def _hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


# ------------------------------------------------------------------- sections


def sections(content: str) -> list[dict]:
    """마크다운 제목(코드 블록 밖)으로 나눈 섹션 — 제목 앞 글은 '(머리말)'. 제목이 없으면 문서 전체 하나."""
    text = content or ""
    starts: list[tuple[int, str]] = []
    in_fence = False
    pos = 0
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
        elif not in_fence:
            m = _HEADING_RE.match(stripped)
            if m:
                starts.append((pos, m.group(2).strip()))
        pos += len(line)
    if not starts:
        return [{"index": 0, "title": "(문서 전체)", "start": 0, "end": len(text), "chars": len(text)}]
    out: list[dict] = []
    if starts[0][0] > 0 and text[: starts[0][0]].strip():
        out.append({"title": "(머리말)", "start": 0, "end": starts[0][0]})
    for i, (start, title) in enumerate(starts):
        end = starts[i + 1][0] if i + 1 < len(starts) else len(text)
        out.append({"title": title, "start": start, "end": end})
    for i, s in enumerate(out):
        s["index"] = i
        s["chars"] = s["end"] - s["start"]
    return out


def _locate(content: str, section_index: int | None, selected_text: str | None) -> tuple[int, int, str]:
    if selected_text and selected_text.strip():
        needle = selected_text.strip()
        count = content.count(needle)
        if count == 0:
            raise ProposalError("붙여 넣은 부분을 결과물에서 찾지 못했습니다 — 결과물의 글을 그대로 복사해 넣으세요")
        if count > 1:
            raise ProposalError("붙여 넣은 부분이 결과물에 여러 번 나옵니다 — 앞뒤 글을 조금 더 넣어 한 곳으로 좁혀 주세요")
        start = content.index(needle)
        return start, start + len(needle), "선택한 부분: " + needle[:40].replace("\n", " ") + ("…" if len(needle) > 40 else "")
    secs = sections(content)
    if section_index is None or not 0 <= section_index < len(secs):
        raise ProposalError("바꿀 섹션을 고르거나 바꿀 부분을 붙여 넣으세요")
    s = secs[section_index]
    return s["start"], s["end"], f"섹션: {s['title']}"


# ------------------------------------------------------------------- permission


def _deliverable_for_edit(conn: sqlite3.Connection, deliverable_id: int, actor_id: int, role: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM deliverables WHERE id = ?", (deliverable_id,)).fetchone()
    if row is None:
        raise LookupError("deliverable_not_found")
    if role not in ("admin", "manager") and not (
        row["author_type"] == "human" and row["author_id"] == actor_id and row["status"] == "draft"
    ):
        raise PermissionError("not_allowed")
    return row


# ---------------------------------------------------------------------- propose

SYSTEM = (
    "너는 문서의 한 부분만 고쳐 쓰는 편집자다. [고칠 부분]만 [요청]대로 다시 쓰고, 그 부분을 대신할 글만 "
    "출력한다 — 문서의 다른 부분, 설명, 머리말·맺음말, 코드 펜스로 감싸기를 덧붙이지 않는다. [고칠 부분]이 "
    "제목으로 시작하면 같은 수준의 제목을 유지한다(요청이 제목을 바꾸라는 것이 아니면 제목 글도 그대로). "
    "마크다운 표·목록·코드 블록 형식을 유지한다. [문서 전체]는 앞뒤 맥락과 용어·수치를 맞추는 데만 쓴다. "
    "원문에 없는 수치나 사실을 새로 지어내지 않는다."
)


def _clean_proposal(original: str, proposed: str) -> str:
    from hair_platform import model_output

    text = model_output.clean(proposed) or (proposed or "").strip()
    # 모델이 통째로 펜스로 감싸 돌려주면(원문은 펜스가 아닌데) 벗긴다.
    fenced = re.match(r"^```[^\n]*\n(.*?)\n?```\s*$", text, re.DOTALL)
    if fenced and not original.lstrip().startswith("```"):
        text = fenced.group(1)
    # 원문 구간의 끝 줄바꿈을 유지해 다음 섹션이 붙지 않게 한다.
    trailing = original[len(original.rstrip("\n")):]
    return text.rstrip("\n") + trailing


def propose(conn: sqlite3.Connection, deliverable_id: int, actor_id: int, role: str, instruction: str,
            section_index: int | None = None, selected_text: str | None = None,
            provider: str | None = None, model: str | None = None) -> dict:
    from hair_platform import llm

    instruction = (instruction or "").strip()
    if not instruction:
        raise ProposalError("어떻게 고칠지 요청을 입력하세요")
    row = _deliverable_for_edit(conn, deliverable_id, actor_id, role)
    content = row["content"] or ""
    start, end, label = _locate(content, section_index, selected_text)
    original = content[start:end]
    if len(original) > MAX_TARGET_CHARS:
        raise ProposalError(f"고칠 부분이 너무 깁니다({len(original):,}자) — 더 작은 섹션이나 구간을 고르세요")
    resolved_provider, resolved_model = llm.resolve_provider_and_model(provider, model)
    context = content if len(content) <= CONTEXT_CHARS else content[: CONTEXT_CHARS // 2] + "\n…(중략)…\n" + content[-CONTEXT_CHARS // 2:]
    prompt = f"[문서 전체]\n{context}\n\n[고칠 부분]\n{original}\n\n[요청]\n{instruction}"
    proposed = _clean_proposal(original, llm.call_llm(resolved_provider, resolved_model, SYSTEM, prompt))
    if not proposed.strip():
        raise ProposalError("AI가 빈 수정안을 돌려줬습니다 — 요청을 바꿔 다시 시도하세요")
    warnings = _warnings(original, proposed, content, instruction)
    cur = conn.execute(
        "INSERT INTO deliverable_edit_proposals (deliverable_id, target_label, target_start, target_end, original_text, "
        "proposed_text, instruction, base_hash, llm_provider, llm_model, created_by, warnings) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (deliverable_id, label, start, end, original, proposed, instruction, _hash(content),
         resolved_provider, resolved_model, actor_id, json.dumps(warnings, ensure_ascii=False)),
    )
    proposal_id = cur.lastrowid
    assert proposal_id is not None
    audit.record(conn, actor_id, "deliverable_edit_proposed", "deliverables", deliverable_id,
                 after={"proposal_id": proposal_id, "target": label, "instruction": instruction[:200]})
    conn.commit()
    return get(conn, proposal_id)


def _warnings(original: str, proposed: str, content: str, instruction: str) -> list[str]:
    """수락 전에 사람이 확인할 점 — 반영 여부는 사람이 정한다(실측: 로컬 모델이 고쳐 쓰며 "순현금흐름"을
    "순현나름"으로, "좌우하며"를 "우우하며"로 옮겼다). 요청 글도 낱말 근거에 넣는다(새 수치·용어가 거기서 온다)."""
    from hair_platform import language_qa, text_checks

    out = [f"깨진 낱말로 보임: '{g['found']}'(원래 '{g['expected']}')"
           for g in text_checks.garbled_terms(original, proposed, content + "\n" + instruction)]
    foreign = language_qa.find_issue_segments(proposed) if language_qa.is_mostly_korean(content) else []
    out += [f"한자·외국 문자 섞임: {seg[:60]}" for seg in foreign[:3]]
    return out


def get(conn: sqlite3.Connection, proposal_id: int) -> dict:
    row = conn.execute(
        "SELECT p.*, u.username AS created_by_name, d.username AS decided_by_name FROM deliverable_edit_proposals p "
        "LEFT JOIN users u ON u.id = p.created_by LEFT JOIN users d ON d.id = p.decided_by WHERE p.id = ?",
        (proposal_id,),
    ).fetchone()
    if row is None:
        raise LookupError("proposal_not_found")
    out = dict(row)
    out["warnings"] = json.loads(out.get("warnings") or "[]")
    return out


def list_for(conn: sqlite3.Connection, deliverable_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT id FROM deliverable_edit_proposals WHERE deliverable_id = ? ORDER BY id DESC LIMIT 30",
        (deliverable_id,),
    ).fetchall()
    return [get(conn, r["id"]) for r in rows]


# ----------------------------------------------------------------------- decide


def decide(conn: sqlite3.Connection, proposal_id: int, actor_id: int, role: str, accept: bool) -> dict:
    from hair_platform import deliverables

    p = get(conn, proposal_id)
    if p["status"] != "pending":
        raise ProposalError("이미 처리된 수정안입니다")
    row = _deliverable_for_edit(conn, p["deliverable_id"], actor_id, role)
    if not accept:
        conn.execute(
            "UPDATE deliverable_edit_proposals SET status = 'rejected', decided_by = ?, decided_at = datetime('now') "
            "WHERE id = ?", (actor_id, proposal_id),
        )
        audit.record(conn, actor_id, "deliverable_edit_rejected", "deliverables", p["deliverable_id"],
                     after={"proposal_id": proposal_id})
        conn.commit()
        return {"proposal": get(conn, proposal_id), "deliverable": None}

    content = row["content"] or ""
    if _hash(content) == p["base_hash"]:
        start, end = p["target_start"], p["target_end"]
    else:
        # 제안 뒤 결과물이 바뀌었다 — 원래 구간이 정확히 한 번 남아 있을 때만 그 자리에 반영한다.
        if content.count(p["original_text"]) != 1:
            raise ProposalError("제안 뒤 결과물이 바뀌어 고칠 부분을 찾을 수 없습니다 — 수정안을 다시 만드세요")
        start = content.index(p["original_text"])
        end = start + len(p["original_text"])
    new_content = content[:start] + p["proposed_text"] + content[end:]
    updated = deliverables.update_deliverable_content(conn, p["deliverable_id"], actor_id, role, new_content)
    conn.execute(
        "UPDATE deliverable_edit_proposals SET status = 'accepted', decided_by = ?, decided_at = datetime('now') "
        "WHERE id = ?", (actor_id, proposal_id),
    )
    audit.record(conn, actor_id, "deliverable_edit_accepted", "deliverables", p["deliverable_id"],
                 after={"proposal_id": proposal_id, "target": p["target_label"]})
    conn.commit()
    return {"proposal": get(conn, proposal_id), "deliverable": updated}
