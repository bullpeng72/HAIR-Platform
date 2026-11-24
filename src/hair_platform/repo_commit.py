"""결과물 → 코드 저장소 커밋(2026-11-24, P0-3 2단계 — "2단계 저장소의 문제 없다면 메인에 커밋").

승인된 결과물에 든 코드 블록을 등록 저장소의 **main(또는 master) 브랜치에 로컬 커밋**한다. push·PR은
하지 않는다. 아래 점검을 하나라도 통과하지 못하면 아무것도 쓰지 않는다("문제 없다면").

점검
1. 결과물이 결재를 마쳤다(`approved`).
2. 저장소가 켜져 있고, 지금 체크아웃된 브랜치가 main/master이며, 작업 트리가 깨끗하다(미커밋 변경 없음).
3. 커밋할 파일을 **명시된 경로**로만 고른다 — 코드 블록 첫 줄의 경로 주석(`# src/app.py`, `// web/a.ts`,
   `<!-- index.html -->`) 또는 블록 바로 앞 줄의 경로 표기(`` `src/app.py` ``, `**src/app.py**`,
   `### src/app.py`, `파일: src/app.py`). 경로 없는 블록은 쓰지 않고 목록으로 알린다(추측하지 않는다).
4. 경로는 저장소 안 상대 경로만, `.git/`·비밀 파일 이름(`code_repos`와 같은 기준)·`.gitignore`로 무시되는
   경로·추적 안 되는 기존 파일 덮어쓰기는 거부. 같은 경로가 두 번 나오면 거부(어느 쪽인지 모호).
5. `.py` 파일은 문법 검사(`compile`)를 통과해야 한다.
6. 이 결과물의 테스트 실행 기록(`test_execution` — 결과물에 든 테스트를 Docker에서 실제로 돌린 결과)이
   실패였으면 거부한다(기록이 없거나 측정 안 됨이면 그 사실만 알린다).

커밋은 작성자 "HAIR-Platform"으로 하고 메시지에 결과물·업무 번호와 승인·실행한 사람(아이디)을 남긴다.
쓰는 중 실패하면 쓴 파일을 되돌린다(새 파일 삭제, 수정 파일은 HEAD 내용으로).
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import textwrap
from pathlib import Path

from hair_platform import audit, code_repos

MAX_FILES = 40
MAX_FILE_BYTES = 400_000
MAIN_BRANCHES = ("main", "master")
COMMIT_AUTHOR = ("HAIR-Platform", "hair-platform@localhost")

_FENCE_RE = re.compile(r"^```[^\n]*\n(.*?)^```[ \t]*$", re.DOTALL | re.MULTILINE)
_PATH_TOKEN = r"[\w.\-]+(?:/[\w.\-]+)*\.[A-Za-z0-9]{1,10}"
_FIRST_LINE_PATH_RE = re.compile(
    r"^\s*(?:#|//|--|;|<!--|/\*)\s*(?:(?:file(?:name)?|path|파일)\s*[:：]\s*)?(" + _PATH_TOKEN + r")\s*(?:-->|\*/)?\s*$",
    re.IGNORECASE,
)
_BEFORE_LINE_PATH_RE = re.compile(
    r"^\s*(?:#{1,6}\s*|[-*]\s*)?(?:(?:file(?:name)?|path|파일|경로)\s*[:：]\s*)?(?:\*\*|`)?(" + _PATH_TOKEN
    + r")(?:\*\*|`)?\s*:?\s*$",
    re.IGNORECASE,
)


class CommitError(ValueError):
    """사람에게 그대로 보여 줄 수 있는 거부 사유."""


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS deliverable_repo_commits ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " deliverable_id INTEGER NOT NULL REFERENCES deliverables(id) ON DELETE CASCADE,"
        " repository_id INTEGER NOT NULL REFERENCES code_repositories(id),"
        " commit_sha TEXT NOT NULL,"
        " branch TEXT NOT NULL,"
        " files TEXT NOT NULL,"
        " committed_by INTEGER REFERENCES users(id),"
        " created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
    )


# ------------------------------------------------------------------ extraction


def extract_files(content: str) -> tuple[list[dict], list[str]]:
    """(경로가 명시된 파일 목록, 경로가 없어 건너뛴 블록의 첫 줄 목록)."""
    files: list[dict] = []
    skipped: list[str] = []
    text = content or ""
    for m in _FENCE_RE.finditer(text):
        body = m.group(1)
        lines = body.splitlines()
        path = None
        first = lines[0] if lines else ""
        fm = _FIRST_LINE_PATH_RE.match(first)
        if fm:
            path = fm.group(1)
            body = "\n".join(lines[1:]) + ("\n" if body.endswith("\n") else "")
        else:
            before = text[: m.start()].rstrip("\n").splitlines()
            prev = before[-1] if before else ""
            bm = _BEFORE_LINE_PATH_RE.match(prev)
            if bm:
                path = bm.group(1)
        if path:
            files.append({"path": path[2:] if path.startswith("./") else path, "content": body})
        else:
            skipped.append((first.strip() or "(빈 블록)")[:80])
    return files, skipped


# ---------------------------------------------------------------------- checks


def _repo(conn: sqlite3.Connection, repo_name: str) -> dict:
    row = conn.execute(
        "SELECT id, name, path, is_active FROM code_repositories WHERE name = ?", (repo_name,)
    ).fetchone()
    if row is None:
        raise CommitError(f"'{repo_name}' 저장소가 없습니다")
    if not row["is_active"]:
        raise CommitError(f"'{repo_name}' 저장소가 꺼져 있습니다")
    return dict(row)


def _deliverable(conn: sqlite3.Connection, deliverable_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM deliverables WHERE id = ?", (deliverable_id,)).fetchone()
    if row is None:
        raise CommitError("결과물을 찾을 수 없습니다")
    return row


def _test_execution(conn: sqlite3.Connection, deliverable_id: int) -> dict | None:
    row = conn.execute(
        "SELECT gate_scores FROM agent_evaluations WHERE deliverable_id = ? ORDER BY id DESC LIMIT 1",
        (deliverable_id,),
    ).fetchone()
    if row is None:
        return None
    return json.loads(row["gate_scores"] or "{}").get("test_execution")


def plan(conn: sqlite3.Connection, deliverable_id: int, repo_name: str) -> dict:
    """커밋 미리보기 — 아무것도 쓰지 않는다. `ok`가 참일 때만 `commit()`이 진행된다."""
    d = _deliverable(conn, deliverable_id)
    repo = _repo(conn, repo_name)
    root = repo["path"]
    checks: list[dict] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    check("결재 완료", d["status"] == "approved", f"결과물 상태: {d['status']}")

    branch = ""
    try:
        branch = code_repos._git(root, "rev-parse", "--abbrev-ref", "HEAD").strip()
        dirty = code_repos._git(root, "status", "--porcelain", "--untracked-files=no").strip()
    except code_repos.RepoError as exc:
        check("저장소 상태", False, str(exc))
        dirty = "?"
    check("main 브랜치", branch in MAIN_BRANCHES, f"지금 브랜치: {branch or '알 수 없음'}")
    check("작업 트리 깨끗함", not dirty, "미커밋 변경이 있으면 섞일 수 있어 커밋하지 않습니다" if dirty else "")

    files, skipped = extract_files(d["content"])
    check("커밋할 파일", 0 < len(files) <= MAX_FILES,
          f"경로가 명시된 코드 블록 {len(files)}개" + (f"(최대 {MAX_FILES}개)" if len(files) > MAX_FILES else ""))

    seen: set[str] = set()
    planned: list[dict] = []
    for f in files[:MAX_FILES]:
        problems: list[str] = []
        try:
            rel = code_repos._clean_relpath(f["path"])
        except code_repos.RepoError:
            rel, problems = f["path"], ["저장소 밖 경로"]
        if rel in seen:
            problems.append("같은 경로가 두 번 나옴")
        seen.add(rel)
        if rel == ".git" or rel.startswith(".git/") or "/.git/" in f"/{rel}":
            problems.append(".git 경로")
        if code_repos._is_secret(rel):
            problems.append("비밀 파일 이름")
        size = len(f["content"].encode("utf-8"))
        if size > MAX_FILE_BYTES:
            problems.append(f"너무 큼({size:,}바이트)")
        action = "new"
        if not problems:
            tracked = _is_tracked(root, rel)
            exists = os.path.lexists(os.path.join(root, rel))
            if tracked:
                action = "modify"
            elif exists:
                problems.append("추적되지 않는 기존 파일(덮어쓰지 않음)")
            if _is_ignored(root, rel):
                problems.append(".gitignore로 무시되는 경로")
        if rel.endswith(".py") and not problems:
            try:
                compile(f["content"], rel, "exec")
            except SyntaxError as exc:
                problems.append(f"Python 문법 오류 {exc.lineno}줄: {exc.msg}")
        planned.append({"path": rel, "action": action, "lines": f["content"].count("\n") + 1,
                        "problems": problems})
    bad = [p for p in planned if p["problems"]]
    check("파일 점검", not bad and bool(planned),
          "; ".join(f"{p['path']}: {', '.join(p['problems'])}" for p in bad[:5]))

    te = _test_execution(conn, deliverable_id)
    if te and not te.get("not_measured"):
        check("테스트 실행 기록", bool(te.get("passed")),
              f"{te.get('passed_count', 0)}/{te.get('total', 0)} 통과" + ("" if te.get("passed") else " — 실패한 테스트가 있어 커밋하지 않습니다"))
    else:
        checks.append({"name": "테스트 실행 기록", "ok": True, "info": True,
                       "detail": (te or {}).get("reason") or "이 결과물에는 실행한 테스트 기록이 없습니다"})

    return {
        "deliverable_id": deliverable_id, "repository": repo["name"], "branch": branch,
        "files": planned, "skipped_blocks": skipped, "checks": checks,
        "ok": all(c["ok"] for c in checks),
    }


def _is_tracked(root: str, rel: str) -> bool:
    try:
        return bool(code_repos._git(root, "ls-files", "--", rel).strip())
    except code_repos.RepoError:
        return False


def _is_ignored(root: str, rel: str) -> bool:
    try:
        return bool(code_repos._git(root, "check-ignore", "-q", "--no-index", "--", rel) is not None)
    except code_repos.RepoError:
        return False  # check-ignore는 무시되지 않으면 1로 끝난다


# ---------------------------------------------------------------------- commit


def commit(conn: sqlite3.Connection, deliverable_id: int, repo_name: str, actor: sqlite3.Row,
           message: str | None = None) -> dict:
    """점검을 다시 한 뒤(미리보기와 실제 사이의 변화 대비) 통과하면 main에 커밋한다. push는 하지 않는다."""
    p = plan(conn, deliverable_id, repo_name)
    if not p["ok"]:
        failed = [f"{c['name']}({c['detail']})" if c["detail"] else c["name"] for c in p["checks"] if not c["ok"]]
        raise CommitError("커밋하지 않았습니다 — 통과하지 못한 점검: " + "; ".join(failed))
    repo = _repo(conn, repo_name)
    root = repo["path"]
    d = _deliverable(conn, deliverable_id)
    contents = {f["path"]: f["content"] for f in extract_files(d["content"])[0]}
    written: list[dict] = []
    try:
        for f in p["files"]:
            target = Path(root, f["path"])
            created_dirs = [str(par) for par in reversed(target.parents) if not par.exists()]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(contents[f["path"]], encoding="utf-8")
            written.append({**f, "created_dirs": created_dirs})
        code_repos._git(root, "add", "--", *[f["path"] for f in p["files"]])
        if not code_repos._git(root, "diff", "--cached", "--name-only").strip():
            raise CommitError("저장소의 내용과 같아 커밋할 변경이 없습니다")
        task = conn.execute(
            "SELECT t.id, b.raw_text FROM tasks t JOIN briefs b ON b.id = t.brief_id WHERE t.id = ?",
            (d["task_id"],),
        ).fetchone()
        # 기본 제목은 업무 요청 첫 줄을 단어 경계에서 줄인다(실측: 60자에서 잘라 "…그 구"처럼 끊겼다).
        first_line = task["raw_text"].strip().splitlines()[0] if task and task["raw_text"].strip() else ""
        summary = (message or "").strip() or (
            f"HAIR 결과물 #{deliverable_id} 반영"
            + (f" — {textwrap.shorten(first_line, width=60, placeholder='…')}" if first_line else "")
        )
        body = (
            f"{summary}\n\n"
            f"HAIR-Deliverable: #{deliverable_id}\n"
            f"HAIR-Task: #{d['task_id']}\n"
            f"HAIR-Committed-By: {actor['username']}\n"
        )
        name, email = COMMIT_AUTHOR
        code_repos._git(root, "-c", f"user.name={name}", "-c", f"user.email={email}",
                        "-c", "commit.gpgsign=false", "commit", "-q", "-m", body)
        sha = code_repos._git(root, "rev-parse", "HEAD").strip()
    except Exception as exc:
        _rollback(root, written)
        if isinstance(exc, CommitError):
            raise
        raise CommitError(f"커밋 중 실패해 되돌렸습니다: {exc}") from exc

    files_json = json.dumps([{"path": f["path"], "action": f["action"]} for f in p["files"]], ensure_ascii=False)
    conn.execute(
        "INSERT INTO deliverable_repo_commits (deliverable_id, repository_id, commit_sha, branch, files, committed_by) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (deliverable_id, repo["id"], sha, p["branch"], files_json, actor["id"]),
    )
    audit.record(conn, actor["id"], "deliverable_committed_to_repository", "deliverables", deliverable_id,
                 after={"repository": repo["name"], "branch": p["branch"], "commit": sha,
                        "files": [f["path"] for f in p["files"]]})
    conn.commit()
    return {"commit": sha, "short": sha[:7], "branch": p["branch"], "repository": repo["name"],
            "files": [{"path": f["path"], "action": f["action"]} for f in p["files"]], "pushed": False}


def _rollback(root: str, written: list[dict]) -> None:
    try:
        code_repos._git(root, "reset", "-q", "--", *[w["path"] for w in written]) if written else None
    except code_repos.RepoError:
        pass
    for w in written:
        target = Path(root, w["path"])
        try:
            if w["action"] == "modify":
                code_repos._git(root, "checkout", "HEAD", "--", w["path"])
            elif target.exists():
                target.unlink()
        except (OSError, code_repos.RepoError):
            pass
        for d in reversed(w.get("created_dirs", [])):
            try:
                os.rmdir(d)
            except OSError:
                pass


def history(conn: sqlite3.Connection, deliverable_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT c.commit_sha, c.branch, c.files, c.created_at, r.name AS repository, u.username "
        "FROM deliverable_repo_commits c JOIN code_repositories r ON r.id = c.repository_id "
        "LEFT JOIN users u ON u.id = c.committed_by WHERE c.deliverable_id = ? ORDER BY c.id DESC",
        (deliverable_id,),
    ).fetchall()
    return [{**dict(r), "files": json.loads(r["files"])} for r in rows]
