"""결과물 → 코드 저장소 커밋(2026-11-24, P0-3 2단계 — "2단계 저장소의 문제 없다면 메인에 커밋").

점검을 모두 통과할 때만 main에 로컬 커밋하고(push 없음), 하나라도 걸리면 아무것도 쓰지 않는다.
"""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from hair_platform import code_repos, db, repo_commit
from hair_platform.auth import Role, create_user

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git 필요")

GOOD = """## 구현

주문 취소 함수를 추가했다.

```python
# services/order.py
def create_order(items):
    return {'items': items}


def cancel_order(order_id):
    return {'cancelled': order_id}
```

`services/refund.py`
```python
def refund(order_id):
    return order_id
```

```bash
pip install something
```
"""


def _git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True).stdout


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "shop-api"
    (root / "services").mkdir(parents=True)
    (root / "services" / "order.py").write_text("def create_order(items):\n    return {'items': items}\n")
    (root / ".gitignore").write_text("build/\n")
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "tester")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "initial")
    return root


@pytest.fixture
def env(repo):
    conn = db.get_connection(":memory:")
    db.init_schema(conn)
    admin_id = create_user(conn, "commit_admin", "pw", Role.ADMIN)
    code_repos.register_repository(conn, admin_id, "shop-api", str(repo))
    admin = conn.execute("SELECT * FROM users WHERE id = ?", (admin_id,)).fetchone()
    return conn, admin


def _deliverable(conn, admin, content, status="approved", test_execution=None):
    brief = conn.execute("INSERT INTO briefs (submitted_by, raw_text, stub_roles) VALUES (?, '주문 취소 기능 추가', '[]')",
                         (admin["id"],)).lastrowid
    task = conn.execute("INSERT INTO tasks (brief_id, role_name, required_skills) VALUES (?, '백엔드 개발자', '[]')",
                        (brief,)).lastrowid
    did = conn.execute(
        "INSERT INTO deliverables (task_id, author_type, author_id, content, content_type, status) "
        "VALUES (?, 'human', ?, ?, 'markdown', ?)", (task, admin["id"], content, status),
    ).lastrowid
    if test_execution is not None:
        conn.execute("INSERT INTO agent_evaluations (deliverable_id, gate_scores, tcr) VALUES (?, ?, 100)",
                     (did, json.dumps({"test_execution": test_execution})))
    conn.commit()
    return did


def test_extract_uses_only_explicit_paths():
    files, skipped = repo_commit.extract_files(GOOD)
    assert [f["path"] for f in files] == ["services/order.py", "services/refund.py"]
    assert files[0]["content"].startswith("def create_order")  # 경로 주석 줄은 빼고 쓴다
    assert skipped == ["pip install something"]  # 경로 없는 블록은 쓰지 않는다


def test_preview_writes_nothing_and_commit_lands_on_main(env, repo):
    conn, admin = env
    did = _deliverable(conn, admin, GOOD)
    head = _git(repo, "rev-parse", "HEAD").strip()
    p = repo_commit.plan(conn, did, "shop-api")
    assert p["ok"], p["checks"]
    assert {f["path"]: f["action"] for f in p["files"]} == {"services/order.py": "modify", "services/refund.py": "new"}
    assert _git(repo, "rev-parse", "HEAD").strip() == head and not (repo / "services" / "refund.py").exists()

    out = repo_commit.commit(conn, did, "shop-api", admin)
    assert out["branch"] == "main" and out["pushed"] is False
    log = _git(repo, "log", "-1", "--format=%an|%B")
    assert log.startswith("HAIR-Platform|HAIR 결과물 #") and f"HAIR-Deliverable: #{did}" in log
    assert "HAIR-Committed-By: commit_admin" in log
    assert "cancel_order" in _git(repo, "show", "HEAD:services/order.py")
    assert _git(repo, "status", "--porcelain").strip() == ""
    assert repo_commit.history(conn, did)[0]["commit_sha"] == out["commit"]
    assert conn.execute("SELECT COUNT(*) FROM audit_log WHERE action = 'deliverable_committed_to_repository'").fetchone()[0] == 1


@pytest.mark.parametrize("case", ["not_approved", "dirty", "branch", "failed_tests", "syntax", "escape", "secret", "ignored"])
def test_any_failed_check_writes_nothing(env, repo, case):
    conn, admin = env
    content, status, te = GOOD, "approved", None
    if case == "not_approved":
        status = "pending_review"
    elif case == "dirty":
        (repo / "services" / "order.py").write_text("# 사람이 작업 중\n")
    elif case == "branch":
        _git(repo, "checkout", "-q", "-b", "feature")
    elif case == "failed_tests":
        te = {"score": 0.5, "status": "fail", "passed": False, "not_measured": False, "total": 2, "passed_count": 1}
    elif case == "syntax":
        content = "```python\n# services/bad.py\ndef broken(:\n```"
    elif case == "escape":
        content = "```python\n# ../outside.py\nx = 1\n```"
    elif case == "secret":
        content = "```\n# certs/server.pem\n-----BEGIN PRIVATE KEY-----\n```"  # 경로로 인식된 뒤 비밀 파일로 거부
    elif case == "ignored":
        content = "```python\n# build/out.py\nx = 1\n```"
    did = _deliverable(conn, admin, content, status=status, test_execution=te)
    head = _git(repo, "rev-parse", "HEAD").strip()
    assert repo_commit.plan(conn, did, "shop-api")["ok"] is False
    with pytest.raises(repo_commit.CommitError, match="커밋하지 않았습니다"):
        repo_commit.commit(conn, did, "shop-api", admin)
    assert _git(repo, "rev-parse", "HEAD").strip() == head
    assert not (repo / "services" / "refund.py").exists()
    assert not (repo.parent / "outside.py").exists()


def test_failed_commit_hook_rolls_back(env, repo):
    conn, admin = env
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\necho 'lint failed' >&2\nexit 1\n")
    hook.chmod(0o755)
    did = _deliverable(conn, admin, GOOD)
    head = _git(repo, "rev-parse", "HEAD").strip()
    with pytest.raises(repo_commit.CommitError, match="되돌렸습니다"):
        repo_commit.commit(conn, did, "shop-api", admin)
    assert _git(repo, "rev-parse", "HEAD").strip() == head
    assert _git(repo, "status", "--porcelain").strip() == ""  # 수정 파일 복원·새 파일 삭제
    assert "cancel_order" not in (repo / "services" / "order.py").read_text()


def test_passing_test_record_is_reported(env):
    conn, admin = env
    te = {"score": 1.0, "status": "pass", "passed": True, "not_measured": False, "total": 3, "passed_count": 3}
    did = _deliverable(conn, admin, GOOD, test_execution=te)
    p = repo_commit.plan(conn, did, "shop-api")
    assert p["ok"] and any(c["name"] == "테스트 실행 기록" and "3/3" in c["detail"] for c in p["checks"])


def test_api_admin_only_and_round_trip(client_and_conn, repo):
    client, conn = client_and_conn
    admin = conn.execute("SELECT * FROM users WHERE username = 'admin1'").fetchone()
    code_repos.register_repository(conn, admin["id"], "shop-api", str(repo))
    did = _deliverable(conn, admin, GOOD)
    create_user(conn, "commit_mgr", "pw", Role.MANAGER)
    assert client.post("/login", json={"username": "commit_mgr", "password": "pw"}).status_code == 200
    assert client.post(f"/api/deliverables/{did}/repo-commit/preview", json={"repository": "shop-api"}).status_code == 403
    assert client.post("/login", json={"username": "admin1", "password": "adminpass"}).status_code == 200
    prev = client.post(f"/api/deliverables/{did}/repo-commit/preview", json={"repository": "shop-api"})
    assert prev.status_code == 200 and prev.json()["ok"]
    done = client.post(f"/api/deliverables/{did}/repo-commit", json={"repository": "shop-api", "message": "주문 취소 추가"})
    assert done.status_code == 200, done.text
    assert _git(repo, "log", "-1", "--format=%s").strip() == "주문 취소 추가"
    again = client.post(f"/api/deliverables/{did}/repo-commit", json={"repository": "shop-api"})
    assert again.status_code == 400 and "변경이 없습니다" in again.json()["detail"]
    assert client.get(f"/api/deliverables/{did}/repo-commits").json()[0]["repository"] == "shop-api"


# PR 모드(2026-11-25, P1-8) — 브랜치 커밋 → push → PR.
def _with_origin(repo, tmp_path, url="https://github.com/acme/shop-api.git"):
    """원격 주소는 GitHub로 두고 실제 push는 로컬 bare 저장소로 가게 한다(pushInsteadOf — 네트워크 없이)."""
    bare = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", str(bare))
    _git(repo, "remote", "add", "origin", url)
    _git(repo, "config", f"url.{bare}.pushInsteadOf", url)
    return bare


def test_pr_mode_commits_on_branch_pushes_and_opens_pr(env, repo, tmp_path, monkeypatch):
    from unittest.mock import MagicMock, patch

    conn, admin = env
    bare = _with_origin(repo, tmp_path)
    did = _deliverable(conn, admin, GOOD)
    main_head = _git(repo, "rev-parse", "main").strip()
    monkeypatch.setenv("HAIR_GITHUB_TOKEN", "ghp_test_not_real")
    resp = MagicMock(status_code=201)
    resp.json.return_value = {"html_url": "https://github.com/acme/shop-api/pull/7", "number": 7}
    with patch("httpx.post", return_value=resp) as post:
        out = repo_commit.commit(conn, did, "shop-api", admin, mode="pr")
    assert out["branch"] == f"hair/deliverable-{did}" and out["base_branch"] == "main" and out["pushed"] is True
    assert out["pr"] == {"url": "https://github.com/acme/shop-api/pull/7", "created": True, "number": 7}
    sent = post.call_args.kwargs["json"]
    assert sent["head"] == out["branch"] and sent["base"] == "main" and f"HAIR-Deliverable: #{did}" in sent["body"]
    assert post.call_args.args[0] == "https://api.github.com/repos/acme/shop-api/pulls"
    # main은 그대로, 작업 트리는 main으로 돌아와 깨끗하다
    assert _git(repo, "rev-parse", "main").strip() == main_head
    assert _git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip() == "main"
    assert _git(repo, "status", "--porcelain").strip() == ""
    # 원격(bare)에 브랜치가 올라갔고 그 브랜치에 커밋이 있다
    assert out["commit"] == _git(bare, "rev-parse", out["branch"]).strip()
    assert repo_commit.history(conn, did)[0]["pr_url"] == "https://github.com/acme/shop-api/pull/7"
    # 같은 결과물을 다시 올리면 브랜치 이름이 겹치지 않는다
    with patch("httpx.post", return_value=resp):
        again = repo_commit.commit(conn, _deliverable(conn, admin, GOOD.replace("cancel_order", "cancel_order_v2")),
                                   "shop-api", admin, mode="pr")
    assert again["branch"].startswith("hair/deliverable-")


def test_pr_mode_without_token_pushes_and_returns_compare_link(env, repo, tmp_path, monkeypatch):
    conn, admin = env
    _with_origin(repo, tmp_path)
    monkeypatch.delenv("HAIR_GITHUB_TOKEN", raising=False)
    did = _deliverable(conn, admin, GOOD)
    out = repo_commit.commit(conn, did, "shop-api", admin, mode="pr")
    assert out["pushed"] is True and out["pr"]["created"] is False
    assert out["pr"]["url"] == f"https://github.com/acme/shop-api/compare/main...hair/deliverable-{did}?expand=1"


def test_pr_mode_without_origin_keeps_local_branch_and_says_so(env, repo):
    conn, admin = env
    did = _deliverable(conn, admin, GOOD)
    out = repo_commit.commit(conn, did, "shop-api", admin, mode="pr")
    assert out["pushed"] is False and "origin" in out["push_error"] and out["pr"] is None
    assert _git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip() == "main"
    assert out["commit"] == _git(repo, "rev-parse", out["branch"]).strip()  # 로컬 브랜치에 커밋은 남아 있다


def test_pr_mode_failure_returns_to_main_and_deletes_branch(env, repo):
    conn, admin = env
    did = _deliverable(conn, admin, GOOD)
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)
    with pytest.raises(repo_commit.CommitError):
        repo_commit.commit(conn, did, "shop-api", admin, mode="pr")
    assert _git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip() == "main"
    assert f"hair/deliverable-{did}" not in _git(repo, "branch", "--list")
    assert _git(repo, "status", "--porcelain").strip() == ""


def test_github_remote_parsing():
    assert repo_commit.github_repo("git@github.com:acme/shop-api.git") == ("acme", "shop-api")
    assert repo_commit.github_repo("https://github.com/acme/shop-api") == ("acme", "shop-api")
    assert repo_commit.github_repo("https://gitlab.com/acme/shop-api.git") is None
