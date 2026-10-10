"""Phase 33 — 통합 CLI(`hair`), 사용자 요청(2026-10-10, "실행 명령어가
너무 길고 어려워. 명령어를 짧고 명료하게 정리하고, 현행화 해줘.
명령어에 대한 도움말(--help 도움말 포함)을 가독성 높게 구현해줘").

`hair serve`/`hair bootstrap-admin`/`hair eval` 세 서브커맨드로
`uvicorn ... --factory --reload`/`hair-platform-bootstrap-admin
--db-path ... --username ... --password ...`/`python -m
hair_platform.eval_wiring`를 대체한다. 이 테스트는 (1) `--help`가
전부 깨끗하게 종료되는지, (2) `bootstrap-admin`이 기존 `bootstrap.py`
CLI(test_phase7의 test_gc53)와 같은 동작을 유지하는지, (3) `eval`이
`eval_wiring.py`의 `__main__` 블록을 그대로 위임하는지, (4) 이번에
같이 고친 `create_app()`의 `HAIR_PLATFORM_DB_PATH` 환경변수 폴백
(이전엔 `uvicorn --factory`로 띄우면 DB 경로를 지정할 방법이 없어
매번 ":memory:"였다 — `docs/LIMITS.md` §83)이 제대로 동작하는지를
검증한다. `hair serve` 자체(uvicorn.run 호출)는 프로세스를 블로킹
시키므로 단위 테스트로 실행하지 않는다 — 실서버 기동으로 수동 확인.
단, uvicorn.run/threading.Timer를 모킹해 "브라우저 자동 열기 예약"
로직(사용자 요청, 2026-10-10, "자동으로 브라우저가 실행되고 8000번
포트 열리도록 개선해줘")만은 블로킹 없이 검증한다."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from hair_platform import cli
from hair_platform.app import create_app


def test_gc_help_lists_all_subcommands(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "serve" in out
    assert "bootstrap-admin" in out
    assert "eval" in out


@pytest.mark.parametrize("subcommand", ["serve", "bootstrap-admin", "eval"])
def test_gc_subcommand_help_exits_cleanly_with_output(subcommand, capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main([subcommand, "--help"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip()


def test_gc_bootstrap_admin_missing_required_args_exits_nonzero():
    with pytest.raises(SystemExit) as exc:
        cli.main(["bootstrap-admin"])  # -u/-p 없음
    assert exc.value.code != 0


def test_gc_bootstrap_admin_subcommand_creates_first_user_then_refuses(
    tmp_path, capsys
):
    db_path = str(tmp_path / "hair.db")

    code = cli.main(["bootstrap-admin", "-u", "admin", "-p", "pw", "-d", db_path])
    assert code == 0
    assert "생성 완료" in capsys.readouterr().out

    code = cli.main(["bootstrap-admin", "-u", "another", "-p", "pw", "-d", db_path])
    assert code == 1
    assert "최초 1회만" in capsys.readouterr().err


def test_gc_bootstrap_admin_db_path_defaults_to_hair_db():
    args = cli._build_parser().parse_args(["bootstrap-admin", "-u", "a", "-p", "b"])
    assert args.db_path == "hair.db"


def test_gc_serve_db_path_defaults_to_hair_db():
    args = cli._build_parser().parse_args(["serve"])
    assert args.db_path == "hair.db"
    assert args.host == "127.0.0.1"
    assert args.port == 8000
    assert args.reload is False
    assert args.no_browser is False


# ---------- `hair serve` 브라우저 자동 열기(사용자 요청, 2026-10-10) ----------
# uvicorn.run()은 블로킹이므로 실제로 서버를 띄우지 않고 uvicorn.run과
# threading.Timer를 모킹해 "무엇을 예약했는지"만 검증한다.


def test_gc_serve_schedules_browser_open_by_default():
    with patch("uvicorn.run") as mock_run, patch("threading.Timer") as mock_timer:
        code = cli.main(["serve"])
    assert code == 0
    mock_run.assert_called_once()
    mock_timer.assert_called_once()
    _delay, func = mock_timer.call_args.args
    assert func.__name__ == "open"  # webbrowser.open
    assert mock_timer.call_args.kwargs["args"] == ["http://127.0.0.1:8000/"]
    mock_timer.return_value.start.assert_called_once()


def test_gc_serve_no_browser_flag_skips_scheduling():
    with patch("uvicorn.run") as mock_run, patch("threading.Timer") as mock_timer:
        code = cli.main(["serve", "--no-browser"])
    assert code == 0
    mock_run.assert_called_once()
    mock_timer.assert_not_called()


def test_gc_serve_wildcard_host_opens_localhost_in_browser():
    with patch("uvicorn.run"), patch("threading.Timer") as mock_timer:
        cli.main(["serve", "--host", "0.0.0.0", "--port", "9000"])
    assert mock_timer.call_args.kwargs["args"] == ["http://127.0.0.1:9000/"]


def test_gc_eval_subcommand_delegates_to_eval_wiring_main_block():
    with patch("runpy.run_module") as mock_run:
        code = cli.main(["eval"])
    assert code == 0
    mock_run.assert_called_once_with("hair_platform.eval_wiring", run_name="__main__")


# ---------- create_app()의 HAIR_PLATFORM_DB_PATH 환경변수 폴백 ----------
# (`hair serve`가 --reload 재시작 시 팩토리를 인자 없이 재호출하므로
# db_path를 넘길 유일한 통로 — §83에서 새로 발견/수정한 갭)


def test_gc_create_app_defaults_to_memory_without_env_var(monkeypatch):
    monkeypatch.delenv("HAIR_PLATFORM_DB_PATH", raising=False)
    app = create_app()
    conn = app.state.db
    conn.execute(
        "INSERT INTO users (username, password_hash, role) VALUES ('x','x','admin')"
    )
    conn.commit()

    app2 = create_app()  # 인자 없음 — 환경변수도 없으니 또 다른 :memory:
    count = app2.state.db.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
    assert count == 0  # 서로 다른 인메모리 DB라 방금 넣은 행이 안 보인다


def test_gc_create_app_honors_db_path_env_var(monkeypatch, tmp_path):
    db_path = str(tmp_path / "env.db")
    monkeypatch.setenv("HAIR_PLATFORM_DB_PATH", db_path)

    app = create_app()
    app.state.db.execute(
        "INSERT INTO users (username, password_hash, role) VALUES ('y','y','admin')"
    )
    app.state.db.commit()

    app2 = create_app()  # 같은 파일을 다시 연다 — 데이터가 남아있어야 한다
    count = app2.state.db.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
    assert count == 1


def test_gc_create_app_explicit_db_path_overrides_env_var(monkeypatch, tmp_path):
    monkeypatch.setenv("HAIR_PLATFORM_DB_PATH", str(tmp_path / "env.db"))
    explicit_path = str(tmp_path / "explicit.db")

    app = create_app(explicit_path)
    app.state.db.execute(
        "INSERT INTO users (username, password_hash, role) VALUES ('z','z','admin')"
    )
    app.state.db.commit()

    # 명시적으로 넘긴 경로가 우선이었는지 — env.db가 아니라 explicit.db에
    # 쓰였어야 하므로, env.db로 새로 열면 비어 있어야 한다.
    app_env = create_app(str(tmp_path / "env.db"))
    count = app_env.state.db.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
    assert count == 0
