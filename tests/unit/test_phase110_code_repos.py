"""코드 저장소 읽기 연동(2026-11-24, P0-3 1단계 — "go ahead").

관리자가 등록한 Git 저장소를 개발 Agent가 `code_repository` 도구로 읽는다. 읽기 전용, 커밋된·추적 중인
파일만, 비밀 파일 이름은 내용 거부, 저장소 밖 경로 거부.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from hair_platform import code_repos, db, tool_calling
from hair_platform.auth import Role, create_user

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git 필요")


def _git(root, *args):
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "shop-api"
    (root / "services").mkdir(parents=True)
    (root / "services" / "order.py").write_text(
        "def create_order(items):\n    return {'items': items}\n\n\ndef cancel_order(order_id):\n    return order_id\n"
    )
    (root / "api.py").write_text("from services.order import cancel_order\n\nROUTES = {'cancel': cancel_order}\n")
    (root / ".gitignore").write_text("local.env\n")
    (root / "local.env").write_text("DB_PASSWORD=ignored-secret\n")
    (root / "deploy.pem").write_text("-----BEGIN PRIVATE KEY-----\ntracked-secret\n")
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "tester")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "initial order service")
    # 커밋하지 않은 변경 — 도구에 보이면 안 된다
    (root / "services" / "order.py").write_text("UNCOMMITTED = True\n")
    (root / "draft.py").write_text("untracked = 1\n")
    return root


@pytest.fixture
def conn():
    c = db.get_connection(":memory:")
    db.init_schema(c)
    return c


def _admin(conn):
    return create_user(conn, "repo_admin", "pw", Role.ADMIN)


def test_register_normalizes_to_toplevel_and_validates(conn, repo, tmp_path):
    admin = _admin(conn)
    reg = code_repos.register_repository(conn, admin, "shop-api", str(repo / "services"), "주문 API")
    assert reg["path"] == str(repo.resolve()) or reg["path"].endswith("shop-api")
    with pytest.raises(code_repos.RepoError, match="name_already_exists"):
        code_repos.register_repository(conn, admin, "shop-api", str(repo))
    with pytest.raises(code_repos.RepoError, match="invalid_name"):
        code_repos.register_repository(conn, admin, "bad name!", str(repo))
    with pytest.raises(code_repos.RepoError, match="path_not_found"):
        code_repos.register_repository(conn, admin, "x", str(tmp_path / "nope"))
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(code_repos.RepoError):
        code_repos.register_repository(conn, admin, "plain", str(plain))
    assert conn.execute("SELECT COUNT(*) FROM audit_log WHERE action = 'code_repository_registered'").fetchone()[0] == 1


def test_reads_only_committed_tracked_content(conn, repo):
    code_repos.register_repository(conn, _admin(conn), "shop-api", str(repo))
    listing = code_repos.run_action(conn, "list")
    assert "services/order.py" in listing and "api.py" in listing
    assert "draft.py" not in listing and "local.env" not in listing  # 미추적·무시 파일 없음
    content = code_repos.run_action(conn, "read", path="services/order.py")
    assert "def cancel_order" in content and "UNCOMMITTED" not in content  # 커밋된 내용만
    assert "    5  def cancel_order" in content  # 줄 번호
    hits = code_repos.run_action(conn, "search", query="CANCEL_ORDER")  # 대소문자 무시
    assert "services/order.py:5:" in hits and "api.py:1:" in hits
    assert "initial order service" in code_repos.run_action(conn, "log")
    assert "services/order.py" in code_repos.run_action(conn, "list", query="*.py", path="services")


def test_secret_and_escaping_paths_refused(conn, repo):
    code_repos.register_repository(conn, _admin(conn), "shop-api", str(repo))
    assert "읽지 않습니다" in code_repos.run_action(conn, "read", path="deploy.pem")  # 추적 중이어도 거부
    hits = code_repos.run_action(conn, "search", query="tracked-secret")
    assert "deploy.pem" not in hits and "일치하는 줄이 없습니다" in hits  # 검색 결과에서도 비밀 파일 제외
    assert "path_must_be_relative" in code_repos.run_action(conn, "read", path="../etc/passwd")
    assert "path_must_be_relative" in code_repos.run_action(conn, "read", path="/etc/passwd")
    assert "실패" in code_repos.run_action(conn, "read", path="local.env.bak")  # 없는/미추적 파일


def test_line_window_and_unknown_repo_messages(conn, repo):
    admin = _admin(conn)
    code_repos.register_repository(conn, admin, "shop-api", str(repo))
    out = code_repos.read_file(conn, "shop-api", "services/order.py", start_line=4, max_lines=2)
    assert "4~5줄" in out and "start_line=6" in out
    assert "없습니다" in code_repos.run_action(conn, "read", repo="other", path="api.py")
    code_repos.register_repository(conn, admin, "second", str(repo))
    assert "여러 개" in code_repos.run_action(conn, "read", path="api.py")  # 둘 이상이면 이름 필요


def test_inactive_repository_is_hidden(conn, repo):
    admin = _admin(conn)
    reg = code_repos.register_repository(conn, admin, "shop-api", str(repo))
    code_repos.set_repository_active(conn, admin, reg["id"], False)
    assert "등록된 코드 저장소가 없습니다" in code_repos.run_action(conn, "repos")


def test_seed_links_tool_skill_and_dev_agents(conn):
    tool = conn.execute("SELECT kind, config, category FROM tool_pool WHERE name = ?", (code_repos.TOOL_NAME,)).fetchone()
    assert tool["kind"] == "native" and code_repos.NATIVE_ID in tool["config"] and tool["category"] == "sw_dev"
    skill = conn.execute("SELECT augmentation_mode, category FROM skill_pool WHERE name = ?", (code_repos.SKILL_NAME,)).fetchone()
    assert skill["augmentation_mode"] == "tool" and skill["category"] == "sw_dev"
    roles = {r[0] for r in conn.execute("SELECT role_name FROM agent_pool WHERE skill_tags LIKE ?", (f"%{code_repos.SKILL_NAME}%",))}
    assert {"코드베이스 분석가", "백엔드 개발자", "코드 검증가"} <= roles


def test_single_agent_path_can_call_repository_tool(conn, repo):
    pytest.importorskip("crewai")
    code_repos.register_repository(conn, _admin(conn), "shop-api", str(repo))
    tools = tool_calling.resolve_callable_tools(conn, [code_repos.SKILL_NAME])
    assert [t.name for t in tools] == ["code_repository"]
    out = tools[0].run(action="search", query="cancel_order")
    assert "services/order.py:5:" in out


def test_repository_api_is_admin_only(client_and_conn, repo):
    client, conn = client_and_conn
    create_user(conn, "repo_mgr", "pw", Role.MANAGER)
    assert client.post("/login", json={"username": "repo_mgr", "password": "pw"}).status_code == 200
    assert client.get("/api/code-repositories").status_code == 403
    assert client.post("/login", json={"username": "admin1", "password": "adminpass"}).status_code == 200
    resp = client.post("/api/code-repositories", json={"name": "shop-api", "path": str(repo)})
    assert resp.status_code == 201, resp.text
    repo_id = resp.json()["id"]
    bad = client.post("/api/code-repositories", json={"name": "x", "path": "/no/such/dir"})
    assert bad.status_code == 400 and "경로" in bad.json()["detail"]
    assert client.patch(f"/api/code-repositories/{repo_id}", json={"is_active": False}).json()["is_active"] is False
    assert client.get("/api/code-repositories").json()[0]["is_active"] == 0
