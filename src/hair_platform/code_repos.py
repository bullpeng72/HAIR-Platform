"""코드 저장소 읽기 연동(2026-11-24, P0-3 "개발업무 결과를 실제로 검증하지 않음 — 저장소 연동 없음"
→ "go ahead", 1단계: 읽기 전용).

지금까지 코드베이스 분석가·개발 Agent는 사람이 붙여 준 코드 텍스트만 볼 수 있었다. 관리자가 서버에
있는 Git 저장소(로컬 클론)를 등록하면, Agent가 도구 하나(`code_repository`)로 파일 목록·파일 내용·
코드 검색·최근 커밋을 직접 읽는다.

**안전 경계(읽기 전용 1단계)**
- 등록은 admin만. 경로는 실제 Git 작업 트리의 최상위로 정규화해 저장한다.
- **커밋된·추적 중인 파일만** 읽는다(`git ls-files`·`git show HEAD:<path>`·`git grep <rev>`) —
  작업 트리의 미커밋 변경, `.gitignore`로 빠진 `.env` 같은 파일은 애초에 보이지 않는다.
- 추적 중이어도 비밀 파일로 보이는 이름(`.env*`·`*.pem`·`*.key`·`id_rsa*`·`credentials*` 등)은
  내용을 돌려주지 않는다.
- 경로는 저장소 안의 상대 경로만 받는다(`..`·절대 경로 거부) — git 객체에서 읽으므로 심볼릭 링크로
  저장소 밖을 읽을 수도 없다.
- 출력은 잘라서 돌려준다(긴 파일은 줄 범위로 나눠 읽게 안내).
- 쓰기(브랜치·커밋·PR)는 이 단계에 없다.
"""

from __future__ import annotations

import fnmatch
import os
import re
import sqlite3
import subprocess
from pathlib import PurePosixPath

from hair_platform import audit

NATIVE_ID = "code_repository_read"
TOOL_NAME = "코드 저장소 조회"
SKILL_NAME = "코드저장소조회"

_GIT_TIMEOUT_SECONDS = 20
MAX_FILE_LINES = 400
MAX_OUTPUT_CHARS = 12000
MAX_LIST_ENTRIES = 300
MAX_SEARCH_HITS = 60
MAX_COMMITS = 30

_SECRET_NAME_PATTERNS = (
    ".env", ".env.*", "*.pem", "*.key", "*.p12", "*.pfx", "id_rsa*", "id_ed25519*", "id_ecdsa*",
    "credentials*", "*secret*", "*.keystore", ".npmrc", ".pypirc", ".netrc", "*.tfstate",
)
_NAME_RE = re.compile(r"^[\w가-힣.\-]{1,60}$")


class RepoError(ValueError):
    """사용자·Agent에게 그대로 보여줄 수 있는 오류(경로·저장소 이름 문제 등)."""


# ---------------------------------------------------------------------------- git


def _git(root: str, *args: str) -> str:
    try:
        proc = subprocess.run(
            ["git", "-C", root, *args],
            capture_output=True, text=True, timeout=_GIT_TIMEOUT_SECONDS, check=False,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0"},
        )
    except FileNotFoundError as exc:
        raise RepoError("git_not_installed") from exc
    except subprocess.TimeoutExpired as exc:
        raise RepoError("git_timeout") from exc
    if proc.returncode != 0:
        raise RepoError((proc.stderr or "git_failed").strip().splitlines()[0][:300])
    return proc.stdout


def _toplevel(path: str) -> str:
    if not path or not os.path.isdir(path):
        raise RepoError("path_not_found")
    root = _git(path, "rev-parse", "--show-toplevel").strip()
    if not root:
        raise RepoError("not_a_git_repository")
    _git(root, "rev-parse", "--verify", "HEAD")  # 커밋이 하나도 없으면 읽을 게 없다
    return root


def _is_secret(path: str) -> bool:
    name = PurePosixPath(path).name.lower()
    return any(fnmatch.fnmatch(name, pat) for pat in _SECRET_NAME_PATTERNS)


def _clean_relpath(path: str | None) -> str:
    raw = (path or "").strip()
    # 절대 경로는 앞의 "/"를 떼기 전에 거부한다("/etc/passwd"가 저장소 안 "etc/passwd"로 바뀌지 않게).
    if raw.startswith(("/", "~")) or "\\" in raw or re.match(r"^[A-Za-z]:", raw):
        raise RepoError("path_must_be_relative_inside_repository")
    p = raw.strip("/")
    if p in ("", "."):
        return ""
    if any(part == ".." for part in p.split("/")):
        raise RepoError("path_must_be_relative_inside_repository")
    return p


def _cap(text: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    return text if len(text) <= limit else text[:limit] + f"\n…(출력이 길어 {limit:,}자에서 잘랐습니다)"


# ------------------------------------------------------------------- registry CRUD


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS code_repositories ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " name TEXT NOT NULL UNIQUE,"
        " path TEXT NOT NULL,"
        " description TEXT NOT NULL DEFAULT '',"
        " is_active INTEGER NOT NULL DEFAULT 1,"
        " created_by INTEGER REFERENCES users(id),"
        " created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
    )


def list_repositories(conn: sqlite3.Connection, include_inactive: bool = False) -> list[dict]:
    sql = "SELECT id, name, path, description, is_active, created_at FROM code_repositories"
    if not include_inactive:
        sql += " WHERE is_active = 1"
    return [dict(r) for r in conn.execute(sql + " ORDER BY name")]


def register_repository(conn: sqlite3.Connection, actor_id: int, name: str, path: str,
                        description: str = "") -> dict:
    name = (name or "").strip()
    if not _NAME_RE.match(name):
        raise RepoError("invalid_name")  # 글자·숫자·._- 60자 이내(Agent가 이 이름으로 부른다)
    root = _toplevel(os.path.expanduser((path or "").strip()))
    if conn.execute("SELECT 1 FROM code_repositories WHERE name = ?", (name,)).fetchone():
        raise RepoError("name_already_exists")
    cur = conn.execute(
        "INSERT INTO code_repositories (name, path, description, created_by) VALUES (?, ?, ?, ?)",
        (name, root, (description or "").strip(), actor_id),
    )
    audit.record(conn, actor_id, "code_repository_registered", "code_repositories", cur.lastrowid,
                 after={"name": name, "path": root})
    conn.commit()
    return {"id": cur.lastrowid, "name": name, "path": root, "description": description or ""}


def set_repository_active(conn: sqlite3.Connection, actor_id: int, repo_id: int, active: bool) -> None:
    row = conn.execute("SELECT name FROM code_repositories WHERE id = ?", (repo_id,)).fetchone()
    if row is None:
        raise RepoError("repository_not_found")
    conn.execute("UPDATE code_repositories SET is_active = ? WHERE id = ?", (1 if active else 0, repo_id))
    audit.record(conn, actor_id, "code_repository_active_changed", "code_repositories", repo_id,
                 after={"name": row["name"], "is_active": bool(active)})
    conn.commit()


def _repo_root(conn: sqlite3.Connection, repo: str | None) -> tuple[str, str]:
    repos = list_repositories(conn)
    if not repos:
        raise RepoError("등록된 코드 저장소가 없습니다 — 관리자가 '연동 설정'에서 저장소를 등록해야 합니다.")
    name = (repo or "").strip()
    if not name:
        if len(repos) == 1:
            return repos[0]["name"], repos[0]["path"]
        raise RepoError("저장소가 여러 개입니다 — repo에 이름을 넣으세요: " + ", ".join(r["name"] for r in repos))
    for r in repos:
        if r["name"] == name:
            return r["name"], r["path"]
    raise RepoError(f"'{name}' 저장소가 없습니다. 등록된 저장소: " + ", ".join(r["name"] for r in repos))


# ---------------------------------------------------------------- read operations


def describe_repositories(conn: sqlite3.Connection) -> str:
    repos = list_repositories(conn)
    if not repos:
        return "등록된 코드 저장소가 없습니다 — 관리자가 '연동 설정'에서 저장소를 등록해야 합니다."
    lines = ["등록된 코드 저장소(이름 | 설명 | 현재 커밋):"]
    for r in repos:
        try:
            head = _git(r["path"], "log", "-1", "--format=%h %ad %s", "--date=short").strip()
        except RepoError as exc:
            head = f"(읽기 실패: {exc})"
        lines.append(f"- {r['name']} | {r['description'] or '-'} | {head}")
    return "\n".join(lines)


def list_files(conn: sqlite3.Connection, repo: str | None, path: str = "", pattern: str = "") -> str:
    name, root = _repo_root(conn, repo)
    sub = _clean_relpath(path)
    args = ["ls-files", "--", sub] if sub else ["ls-files"]
    files = [f for f in _git(root, *args).splitlines() if f]
    if pattern:
        files = [f for f in files if fnmatch.fnmatch(f, pattern) or fnmatch.fnmatch(PurePosixPath(f).name, pattern)]
    total = len(files)
    shown = files[:MAX_LIST_ENTRIES]
    head = f"[{name}] {sub or '(루트)'} — 추적 중인 파일 {total:,}개" + (f" 중 처음 {len(shown)}개" if total > len(shown) else "")
    return _cap(head + "\n" + "\n".join(shown) if shown else head + " — 해당 파일 없음")


def read_file(conn: sqlite3.Connection, repo: str | None, path: str, start_line: int = 1,
              max_lines: int = MAX_FILE_LINES) -> str:
    name, root = _repo_root(conn, repo)
    rel = _clean_relpath(path)
    if not rel:
        raise RepoError("path_required")
    if _is_secret(rel):
        return f"[{name}] {rel} — 비밀 정보가 담길 수 있는 파일이라 내용을 읽지 않습니다."
    tracked = _git(root, "ls-files", "--error-unmatch", "--", rel) if rel else ""
    if not tracked.strip():
        raise RepoError("file_not_tracked")
    content = _git(root, "show", f"HEAD:{rel}")
    if "\x00" in content[:4000]:
        return f"[{name}] {rel} — 바이너리 파일이라 내용을 표시하지 않습니다."
    lines = content.splitlines()
    start = max(1, int(start_line or 1))
    count = max(1, min(int(max_lines or MAX_FILE_LINES), MAX_FILE_LINES))
    chunk = lines[start - 1 : start - 1 + count]
    end = start + len(chunk) - 1
    numbered = "\n".join(f"{i:>5}  {line}" for i, line in enumerate(chunk, start))
    more = f"\n…(전체 {len(lines):,}줄 — 이어서 읽으려면 start_line={end + 1})" if end < len(lines) else ""
    return _cap(f"[{name}] {rel} (커밋된 내용, {start}~{end}줄 / 전체 {len(lines):,}줄)\n{numbered}{more}")


def search_code(conn: sqlite3.Connection, repo: str | None, query: str, path: str = "") -> str:
    name, root = _repo_root(conn, repo)
    q = (query or "").strip()
    if not q:
        raise RepoError("query_required")
    sub = _clean_relpath(path)
    args = ["grep", "-n", "-I", "-F", "-i", "-e", q, "HEAD"]
    if sub:
        args += ["--", sub]
    try:
        out = _git(root, *args)
    except RepoError:
        out = ""  # 일치 없음이면 git grep이 1을 돌려준다
    hits = []
    for line in out.splitlines():
        # 형식: HEAD:경로:줄:내용
        _, _, rest = line.partition(":")
        file_path = rest.split(":", 1)[0]
        if _is_secret(file_path):
            continue
        hits.append(rest[:300])
    if not hits:
        return f"[{name}] '{q}' — 일치하는 줄이 없습니다."
    shown = hits[:MAX_SEARCH_HITS]
    more = f"\n…(일치 {len(hits):,}건 중 {len(shown)}건만 표시 — path로 범위를 좁히세요)" if len(hits) > len(shown) else ""
    return _cap(f"[{name}] '{q}' 검색 결과(경로:줄:내용)\n" + "\n".join(shown) + more)


def recent_commits(conn: sqlite3.Connection, repo: str | None, path: str = "", limit: int = 10) -> str:
    name, root = _repo_root(conn, repo)
    sub = _clean_relpath(path)
    n = max(1, min(int(limit or 10), MAX_COMMITS))
    args = ["log", f"-{n}", "--format=%h | %ad | %an | %s", "--date=short"]
    if sub:
        args += ["--", sub]
    out = _git(root, *args).strip()
    return _cap(f"[{name}] 최근 커밋{(' — ' + sub) if sub else ''}\n{out or '(커밋 없음)'}")


ACTIONS = {
    "repos": "등록된 저장소 목록",
    "list": "파일 목록(path로 폴더, pattern으로 *.py 같은 이름 필터)",
    "read": "파일 내용(path 필수, start_line부터 최대 400줄)",
    "search": "코드 검색(query 필수, 대소문자 무시, path로 범위 제한)",
    "log": "최근 커밋(path로 특정 파일·폴더 이력)",
}


def run_action(conn: sqlite3.Connection, action: str, repo: str = "", path: str = "", query: str = "",
               start_line: int = 1) -> str:
    """도구 진입점 — 오류도 문자열로 돌려준다(Agent가 다음 호출을 고칠 수 있게)."""
    act = (action or "").strip().lower()
    try:
        if act in ("repos", "repositories", ""):
            return describe_repositories(conn)
        if act in ("list", "ls", "files"):
            return list_files(conn, repo, path, query)
        if act in ("read", "cat", "open"):
            return read_file(conn, repo, path, start_line)
        if act in ("search", "grep", "find"):
            return search_code(conn, repo, query, path)
        if act in ("log", "commits", "history"):
            return recent_commits(conn, repo, path)
        return "알 수 없는 action입니다. 가능한 값: " + "; ".join(f"{k}={v}" for k, v in ACTIONS.items())
    except RepoError as exc:
        return f"코드 저장소 조회 실패: {exc}"


def as_crewai_tool(conn: sqlite3.Connection):
    from crewai.tools import tool  # 지연 import — 안 쓰면 crewai 설치가 필요 없다.

    # 한글 함수명은 crewai가 ASCII로 정규화하며 비워 버린다(graphquery.py 참고).
    @tool("code_repository")
    def code_repository(action: str = "repos", repo: str = "", path: str = "", query: str = "",
                        start_line: int = 1) -> str:
        """등록된 Git 코드 저장소를 읽는다(커밋된 파일만, 읽기 전용).
        action: repos(저장소 목록) | list(파일 목록 — path=폴더, query=*.py 같은 이름 필터) |
        read(파일 내용 — path 필수, start_line부터 400줄) | search(코드 검색 — query 필수, path로 범위) |
        log(최근 커밋 — path로 특정 파일 이력). repo는 저장소 이름(하나뿐이면 생략 가능).
        추측하지 말고 먼저 list/search로 위치를 찾은 뒤 read로 실제 코드를 확인하라."""
        return run_action(conn, action, repo, path, query, start_line)

    return code_repository
