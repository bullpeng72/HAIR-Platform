"""Phase 124 — `hair init`/`hair check`, 사용자 요청(2026-11-25, "hair .env 및 주요
환경변수를 설정하는 기능을 담당할 init 와 점검 기능인 check 명령어를 추가해줘").

`env_setup.py`(파싱/렌더링/교차 점검의 단일 정의)와 `cli.py`의 두 서브커맨드를
함께 검증한다 — `init`은 대화형으로 `.env`를 쓰고(`--yes`로 건너뛸 수 있음),
`check`는 그 파일 + 셸 환경변수를 합쳐 읽어 보여주고 흔한 불일치를 찾는다."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from hair_platform import cli, env_setup

# ---------- env_setup 단위 ----------


def test_parse_env_file_skips_comments_and_strips_quotes(tmp_path):
    path = tmp_path / ".env"
    path.write_text(
        "# 주석\n\nANTHROPIC_API_KEY=sk-abc\nHAIR_SMTP_FROM=\"hair@example.com\"\n"
        "export OPENAI_API_KEY=sk-def\n",
        encoding="utf-8",
    )
    values = env_setup.parse_env_file(path)
    assert values["ANTHROPIC_API_KEY"] == "sk-abc"
    assert values["HAIR_SMTP_FROM"] == "hair@example.com"
    assert values["OPENAI_API_KEY"] == "sk-def"  # "export " 접두어 제거


def test_parse_env_file_missing_file_returns_empty(tmp_path):
    assert env_setup.parse_env_file(tmp_path / "nope.env") == {}


def test_leftover_assignments_preserves_unknown_keys(tmp_path):
    path = tmp_path / ".env"
    path.write_text("ANTHROPIC_API_KEY=sk-abc\nCUSTOM_FLAG=1\n", encoding="utf-8")
    assert env_setup.leftover_assignments(path) == ["CUSTOM_FLAG=1"]


def test_effective_values_env_takes_priority_over_file(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text("ANTHROPIC_API_KEY=from-file\n", encoding="utf-8")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "from-shell")
    assert env_setup.effective_values(path)["ANTHROPIC_API_KEY"] == "from-shell"


def test_mask_short_value_fully_masked_long_value_shows_edges():
    assert env_setup.mask("") == ""
    assert env_setup.mask("ab") == "**"
    assert env_setup.mask("sk-abcdef") == "sk…ef"


def test_render_env_file_roundtrips_through_parse(tmp_path):
    values = {"ANTHROPIC_API_KEY": "sk-abc", "HAIR_SMTP_HOST": "smtp.example.com"}
    content = env_setup.render_env_file(values, extra_lines=["CUSTOM_FLAG=1"])
    path = tmp_path / ".env"
    path.write_text(content, encoding="utf-8")
    parsed = env_setup.parse_env_file(path)
    assert parsed["ANTHROPIC_API_KEY"] == "sk-abc"
    assert parsed["HAIR_SMTP_HOST"] == "smtp.example.com"
    assert parsed["CUSTOM_FLAG"] == "1"
    # 값이 없는 키는 주석으로만 남아 실제 설정으로 읽히지 않는다.
    assert "OPENAI_API_KEY" not in parsed
    assert "# OPENAI_API_KEY=" in content


def test_cross_check_flags_missing_llm_key_and_provider():
    issues = env_setup.cross_check({})
    assert any("전부 없습니다" in msg for level, msg in issues if level == "warn")


def test_cross_check_no_warning_when_ollama_explicit():
    issues = env_setup.cross_check({"HAIR_PLATFORM_LLM_PROVIDER": "ollama"})
    assert not any("전부 없습니다" in msg for _level, msg in issues)


def test_cross_check_provider_without_matching_key_warns():
    issues = env_setup.cross_check({"HAIR_PLATFORM_LLM_PROVIDER": "anthropic"})
    assert any("ANTHROPIC_API_KEY가 없습니다" in msg for _level, msg in issues)


def test_cross_check_bad_smtp_port_is_an_error():
    issues = env_setup.cross_check({"HAIR_SMTP_HOST": "smtp.example.com", "HAIR_SMTP_USER": "u",
                                     "HAIR_SMTP_PORT": "not-a-port"})
    assert any(level == "error" for level, _ in issues)


def test_cross_check_smtp_host_without_user_warns():
    issues = env_setup.cross_check({"HAIR_SMTP_HOST": "smtp.example.com"})
    assert any("HAIR_SMTP_USER가 없습니다" in msg for _level, msg in issues)


def test_cross_check_smtp_fields_without_host_warns():
    issues = env_setup.cross_check({"HAIR_SMTP_USER": "u"})
    assert any("메일 알림이 비활성" in msg for _level, msg in issues)


def test_cross_check_clean_config_has_no_issues():
    assert env_setup.cross_check({"ANTHROPIC_API_KEY": "sk-abc"}) == []


# ---------- `hair init` ----------


def test_init_help_exits_cleanly():
    with pytest.raises(SystemExit) as exc:
        cli.main(["init", "--help"])
    assert exc.value.code == 0


def test_init_yes_flag_skips_prompts_and_autogenerates_secret_key(tmp_path):
    env_path = tmp_path / ".env"

    def _fail_if_called(_prompt=""):
        raise AssertionError("--yes면 input()을 부르면 안 된다")

    with patch("builtins.input", _fail_if_called):
        code = cli.main(["init", "--yes", "--path", str(env_path)])
    assert code == 0
    assert env_path.exists()
    values = env_setup.parse_env_file(env_path)
    assert len(values["HAIR_PLATFORM_SECRET_KEY"]) == 64  # secrets.token_hex(32)


def test_init_yes_flag_keeps_existing_secret_key(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text("HAIR_PLATFORM_SECRET_KEY=already-set\n", encoding="utf-8")

    cli.main(["init", "--yes", "--path", str(env_path)])
    assert env_setup.parse_env_file(env_path)["HAIR_PLATFORM_SECRET_KEY"] == "already-set"


def test_init_interactive_fills_values_and_preserves_unknown_keys(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text("CUSTOM_FLAG=1\n", encoding="utf-8")

    answers = iter([
        "sk-ant-test",  # ANTHROPIC_API_KEY
        "",             # OPENAI_API_KEY
        "",             # OLLAMA_BASE_URL
        "",             # TAVILY_API_KEY
        "",             # HAIR_PLATFORM_LLM_PROVIDER
        "",             # HAIR_PLATFORM_LLM_MODEL
        "y",            # 세션 서명 키 자동 생성?
        "",             # HAIR_GITHUB_TOKEN(2026-11-25, P1-8)
        "n",            # 메일 알림 설정하시겠습니까?
        "external",     # HAIR_WORKER_MODE(2026-11-26, P1-6 2단계)
        "",             # HAIR_TLS_CA_FILE(2026-11-26 — 메일 서버 인증서 검증)
        "",             # HAIR_TIMEZONE
        "n",            # 고급 옵션 설정하시겠습니까?
    ])
    with patch("builtins.input", lambda _prompt="": next(answers)):
        code = cli.main(["init", "--path", str(env_path)])
    assert code == 0

    values = env_setup.parse_env_file(env_path)
    assert values["ANTHROPIC_API_KEY"] == "sk-ant-test"
    assert len(values["HAIR_PLATFORM_SECRET_KEY"]) == 64
    assert "HAIR_SMTP_HOST" not in values  # 메일 알림을 건너뜀
    assert values["HAIR_WORKER_MODE"] == "external" and "HAIR_STAGE_RUNTIME" not in values  # 고급 옵션은 건너뜀
    assert values["CUSTOM_FLAG"] == "1"  # 알 수 없는 기존 값은 보존


def test_init_interactive_enter_keeps_current_value(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text("ANTHROPIC_API_KEY=existing-key\nHAIR_PLATFORM_SECRET_KEY=existing-secret\n",
                         encoding="utf-8")

    with patch("builtins.input", lambda _prompt="": ""):  # 전부 Enter
        code = cli.main(["init", "--path", str(env_path)])
    assert code == 0
    values = env_setup.parse_env_file(env_path)
    assert values["ANTHROPIC_API_KEY"] == "existing-key"
    assert values["HAIR_PLATFORM_SECRET_KEY"] == "existing-secret"


def test_init_prints_set_keys_summary(tmp_path, capsys):
    env_path = tmp_path / ".env"
    cli.main(["init", "--yes", "--path", str(env_path)])
    out = capsys.readouterr().out
    assert str(env_path) in out
    assert "HAIR_PLATFORM_SECRET_KEY" in out
    assert "hair check" in out


# ---------- `hair check` ----------


def test_check_help_exits_cleanly():
    with pytest.raises(SystemExit) as exc:
        cli.main(["check", "--help"])
    assert exc.value.code == 0


def test_check_reports_missing_file_and_still_exits_zero_when_clean(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("HAIR_PLATFORM_LLM_PROVIDER", "ollama")
    missing_path = tmp_path / "missing.env"

    code = cli.main(["check", "--path", str(missing_path)])
    out = capsys.readouterr().out
    assert "파일 없음" in out
    assert "특별한 문제가 없습니다" in out
    assert code == 0


def test_check_exit_code_is_nonzero_on_error_level_issue(tmp_path, monkeypatch):
    monkeypatch.delenv("HAIR_SMTP_PORT", raising=False)
    env_path = tmp_path / ".env"
    env_path.write_text(
        "HAIR_SMTP_HOST=smtp.example.com\nHAIR_SMTP_USER=u\nHAIR_SMTP_PORT=not-a-port\n",
        encoding="utf-8",
    )
    code = cli.main(["check", "--path", str(env_path)])
    assert code == 1


def test_check_masks_secret_values_in_output(tmp_path, capsys):
    env_path = tmp_path / ".env"
    env_path.write_text("ANTHROPIC_API_KEY=sk-abcdefgh\n", encoding="utf-8")
    cli.main(["check", "--path", str(env_path)])
    out = capsys.readouterr().out
    assert "sk-abcdefgh" not in out
    assert "sk…gh" in out


def test_check_shows_plain_non_secret_values(tmp_path, capsys):
    env_path = tmp_path / ".env"
    env_path.write_text("OLLAMA_BASE_URL=http://localhost:12345\n", encoding="utf-8")
    cli.main(["check", "--path", str(env_path)])
    out = capsys.readouterr().out
    assert "http://localhost:12345" in out


# ---------- 전체 도움말에 새 서브커맨드가 보이는지 ----------


def test_top_level_help_and_brief_help_list_init_and_check(capsys):
    with pytest.raises(SystemExit):
        cli.main(["--help"])
    full = capsys.readouterr().out
    assert "init" in full and "check" in full

    cli.main([])
    brief = capsys.readouterr().out
    assert "init" in brief and "check" in brief


def test_dependency_check_warns_on_minor_version_drift(monkeypatch):
    """2026-11-25(P0-2) — 운영 conda가 crewai 1.6.1, 테스트가 1.15.22였던 drift를 `hair check`가 알려야 한다."""
    import importlib.metadata as md

    from hair_platform import env_setup, verified_versions

    monkeypatch.setattr(verified_versions, "VERIFIED", {"crewai": "1.15.22", "not-installed-pkg": "1.0.0"})
    monkeypatch.setattr(md, "version", lambda name: {"crewai": "1.6.1"}.get(name) or (_ for _ in ()).throw(md.PackageNotFoundError(name)))
    issues = env_setup.dependency_check()
    assert len(issues) == 1 and issues[0][0] == "warn" and "crewai 1.6.1" in issues[0][1] and "1.15.22" in issues[0][1]
    monkeypatch.setattr(md, "version", lambda name: {"crewai": "1.15.9"}.get(name) or (_ for _ in ()).throw(md.PackageNotFoundError(name)))
    assert env_setup.dependency_check() == []  # 패치 차이는 경고하지 않는다



# ---------- 서버 운영·고급 옵션·연동 비밀값(2026-11-26) ----------

def test_every_hair_env_var_read_by_code_is_documented():
    """코드가 읽는 HAIR_* 환경변수는 모두 hair init/check 목록이나 README의 "자동 설정·내부용" 표에 있다."""
    import re
    from pathlib import Path

    src = Path(env_setup.__file__).parent
    read = set()
    for f in src.glob("*.py"):
        read |= set(re.findall(r"environ(?:\.get)?\(\s*[\"'](HAIR_[A-Z0-9_]+)", f.read_text(encoding="utf-8")))
        read |= set(re.findall(r"environ\[[\"'](HAIR_[A-Z0-9_]+)", f.read_text(encoding="utf-8")))
        read |= set(re.findall(r"getenv\(\s*[\"'](HAIR_[A-Z0-9_]+)", f.read_text(encoding="utf-8")))
    known = {v.key for v in env_setup.ENV_VARS}
    readme = (src.parent.parent / "README.md").read_text(encoding="utf-8")
    missing = sorted(k for k in read if k not in known and f"`{k}`" not in readme)
    assert read and missing == [], missing


def test_cross_check_flags_bad_operation_and_advanced_values():
    issues = dict(env_setup.cross_check({
        "HAIR_PLATFORM_LLM_PROVIDER": "ollama",
        "HAIR_WORKER_MODE": "separate", "HAIR_TIMEZONE": "Seoul/Korea", "HAIR_STAGE_RUNTIME": "fast",
        "HAIR_LANGUAGE_QA": "yes", "HAIR_STAGE_STALL_SECONDS": "45m", "HAIR_ALLOW_LOCAL_CODE_RUN": "1",
    }))
    text = " ".join(m for _l, m in env_setup.cross_check({
        "HAIR_PLATFORM_LLM_PROVIDER": "ollama", "HAIR_WORKER_MODE": "separate", "HAIR_TIMEZONE": "Seoul/Korea",
        "HAIR_STAGE_RUNTIME": "fast", "HAIR_LANGUAGE_QA": "yes", "HAIR_STAGE_STALL_SECONDS": "45m",
        "HAIR_ALLOW_LOCAL_CODE_RUN": "1"}))
    assert issues  # 최소 하나
    for needle in ("HAIR_WORKER_MODE='separate'", "HAIR_TIMEZONE='Seoul/Korea'", "HAIR_STAGE_RUNTIME='fast'",
                   "HAIR_LANGUAGE_QA='yes'", "HAIR_STAGE_STALL_SECONDS='45m'", "HAIR_ALLOW_LOCAL_CODE_RUN=1"):
        assert needle in text, needle
    clean = env_setup.cross_check({"HAIR_PLATFORM_LLM_PROVIDER": "ollama", "HAIR_WORKER_MODE": "external",
                                   "HAIR_TIMEZONE": "Asia/Seoul", "HAIR_STAGE_STALL_SECONDS": "0"})
    assert clean == []


def test_check_reports_connector_secrets_and_missing_worker(tmp_path, capsys, monkeypatch):
    from hair_platform import calendar_feeds, data_connectors, db, job_queue, mail_inbox

    for k in ("HAIR_DB_SALES_PASSWORD", "HAIR_MAIL_SUPPORT_PASSWORD", "HAIR_CAL_TEAM_URL"):
        monkeypatch.delenv(k, raising=False)
    dbp = tmp_path / "hair.db"
    conn = db.get_connection(str(dbp))
    db.init_schema(conn)
    admin = int(conn.execute("INSERT INTO users (username, password_hash, role) VALUES ('a', 'x', 'admin')").lastrowid or 0)
    conn.commit()
    sales = tmp_path / "sales.db"
    sales.write_bytes(b"")
    data_connectors.register(conn, admin, "sales_db", "postgresql", "db:5432/sales", username="r",
                             password_env="HAIR_DB_SALES_PASSWORD")
    mail_inbox.register(conn, admin, "support", "imap.example.com", "s@example.com", "HAIR_MAIL_SUPPORT_PASSWORD")
    calendar_feeds.register(conn, admin, "team", "url", "HAIR_CAL_TEAM_URL")
    job_queue.ensure_schema(conn)
    conn.close()
    env_path = tmp_path / ".env"
    env_path.write_text("HAIR_PLATFORM_LLM_PROVIDER=ollama\nHAIR_WORKER_MODE=external\nHAIR_DB_SALES_PASSWORD=pw\n",
                        encoding="utf-8")
    cli.main(["check", "--path", str(env_path), "--db-path", str(dbp)])
    out = capsys.readouterr().out
    assert "[연동 비밀값" in out
    assert "✓ HAIR_DB_SALES_PASSWORD (사내 DB 비밀번호 「sales_db」)" in out
    assert "· HAIR_MAIL_SUPPORT_PASSWORD" in out and "· HAIR_CAL_TEAM_URL" in out
    assert "pw" not in out.split("[연동 비밀값")[1].split("[점검 결과]")[0]  # 값은 보이지 않는다
    assert "작업 프로세스가 실행되고 있지 않습니다" in out
    assert "[서버 운영(작업 프로세스·시간대)]" in out and "HAIR_WORKER_MODE = external" in out


def test_check_without_db_skips_connector_section(tmp_path, capsys):
    cli.main(["check", "--path", str(tmp_path / ".env"), "--db-path", str(tmp_path / "none.db")])
    assert "[연동 비밀값" not in capsys.readouterr().out
