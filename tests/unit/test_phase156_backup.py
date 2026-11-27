"""백업·복원(2026-11-27, 개선 제안 5-1 — `backup.py`).

예전: 백업 명령이 없었고 `hair reset-data`도 지우기 전에 사본을 남기지 않았다. 운영 DB가 WAL 모드라 파일 복사로는 최근
변경이 빠졌다(실측 — 복사본에 업무가 0건으로 보였다)."""

from __future__ import annotations

import io
import json
import os
import sqlite3
import tarfile
import time

import pytest

from hair_platform import backup, db


@pytest.fixture
def live_db(tmp_path, monkeypatch):
    monkeypatch.delenv("HAIR_BACKUP_DIR", raising=False)
    monkeypatch.delenv("HAIR_BACKUP_KEEP", raising=False)
    monkeypatch.delenv("HAIR_VECTOR_DIR", raising=False)
    path = str(tmp_path / "hair.db")
    conn = db.get_connection(path)
    db.init_schema(conn)
    from hair_platform.auth import Role, create_user

    uid = create_user(conn, "bk_admin", "bk-pass-1234", Role.ADMIN)
    conn.execute("INSERT INTO briefs (submitted_by, raw_text) VALUES (?, '백업 전 업무')", (uid,))
    conn.commit()
    vdir = tmp_path / "hair.db-vectors"
    vdir.mkdir()
    (vdir / "index.bin").write_bytes(b"vectors")
    yield path, conn
    conn.close()


def _briefs(path):
    c = sqlite3.connect(path)
    try:
        return [r[0] for r in c.execute("SELECT raw_text FROM briefs ORDER BY id")]
    finally:
        c.close()


def test_backup_includes_uncheckpointed_wal_changes_and_vectors(live_db):
    path, conn = live_db
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.execute("INSERT INTO briefs (submitted_by, raw_text) SELECT id, 'WAL에만 있는 업무' FROM users LIMIT 1")
    conn.commit()  # 아직 본 파일에 반영 안 된 변경 — 파일 복사로는 빠진다
    info = backup.create_backup(conn)
    assert info["path"].endswith(".tar.gz") and os.path.dirname(info["path"]).endswith("backups") and info["vectors"]
    with tarfile.open(info["path"]) as tar:
        names = set(tar.getnames())
        assert {"manifest.json", "hair.db", "vectors/index.bin"} <= names
        manifest = json.loads(tar.extractfile("manifest.json").read())  # type: ignore[union-attr]
        assert manifest["reason"] == "manual" and manifest["format"] == 1
        db_bytes = tar.extractfile("hair.db").read()  # type: ignore[union-attr]
    copy = os.path.join(os.path.dirname(path), "extracted.db")
    with open(copy, "wb") as f:
        f.write(db_bytes)
    assert "WAL에만 있는 업무" in _briefs(copy)  # 파일 복사와 달리 최근 변경까지 들어 있다


def test_restore_puts_backup_back_and_keeps_current_data_aside(live_db, tmp_path):
    path, conn = live_db
    info = backup.create_backup(conn)
    conn.execute("INSERT INTO briefs (submitted_by, raw_text) SELECT id, '백업 뒤 업무' FROM users LIMIT 1")
    conn.commit()
    conn.close()
    result = backup.restore_backup(info["path"], path)
    assert _briefs(path) == ["백업 전 업무"]
    aside_db = next(m for m in result["moved_aside"] if m.endswith(tuple("0123456789")) and ".before-restore-" in m)
    assert "백업 뒤 업무" in _briefs(aside_db)  # 지금 데이터는 지우지 않고 옆에
    assert (tmp_path / "hair.db-vectors" / "index.bin").read_bytes() == b"vectors"
    assert any("hair.db-vectors.before-restore-" in m for m in result["moved_aside"])


def test_restore_rejects_unsafe_or_foreign_archives(tmp_path):
    def make(name, members):
        p = tmp_path / name
        with tarfile.open(p, "w:gz") as tar:
            for arcname, data in members:
                ti = tarfile.TarInfo(arcname)
                ti.size = len(data)
                tar.addfile(ti, io.BytesIO(data))
        return str(p)

    evil = make("evil.tar.gz", [("manifest.json", b"{}"), ("../escape.txt", b"x")])
    with pytest.raises(backup.BackupError, match="허용하지 않는 경로"):
        backup.restore_backup(evil, str(tmp_path / "x.db"))
    foreign = make("foreign.tar.gz", [("manifest.json", b"{}"), ("etc/passwd", b"x")])
    with pytest.raises(backup.BackupError, match="HAIR 백업이 아닌"):
        backup.inspect(foreign)
    broken = make("broken.tar.gz", [("manifest.json", b"{}"), ("hair.db", b"not a database at all" * 50)])
    with pytest.raises((backup.BackupError, sqlite3.DatabaseError)):
        backup.restore_backup(broken, str(tmp_path / "y.db"))
    assert not (tmp_path / "y.db").exists()  # 손상된 백업으로는 바꾸지 않는다


def test_scheduled_backup_runs_daily_and_keeps_only_recent(live_db, monkeypatch):
    path, conn = live_db
    monkeypatch.setenv("HAIR_BACKUP_KEEP", "2")
    first = backup.run_scheduled_backup(conn)
    assert first is not None and first["reason"] == "auto"
    assert backup.run_scheduled_backup(conn) is None  # 하루가 안 지났다
    dest = backup.backup_dir(path)
    for i, b in enumerate(backup.list_backups(dest)):  # 오래된 백업인 척
        os.utime(b["path"], (time.time() - 3 * 86400 - i, time.time() - 3 * 86400 - i))
    backup.run_scheduled_backup(conn)
    for b in backup.list_backups(dest):
        os.utime(b["path"], (time.time() - 2 * 86400, time.time() - 2 * 86400))
    backup.run_scheduled_backup(conn)
    assert len(backup.list_backups(dest)) == 2  # 최근 2개만
    monkeypatch.setenv("HAIR_BACKUP_KEEP", "0")
    for b in backup.list_backups(dest):
        os.utime(b["path"], (time.time() - 5 * 86400, time.time() - 5 * 86400))
    assert backup.run_scheduled_backup(conn) is None  # 0이면 자동 백업 끔


def test_backup_dir_env_and_status_item(live_db, tmp_path, monkeypatch):
    from hair_platform import system_status

    path, conn = live_db
    item = system_status._backup_item(conn)
    assert item["status"] == "warn" and "아직 백업이 없습니다" in item["detail"]
    monkeypatch.setenv("HAIR_BACKUP_DIR", str(tmp_path / "nas"))
    info = backup.create_backup(conn)
    assert info["path"].startswith(str(tmp_path / "nas"))
    item = system_status._backup_item(conn)
    assert item["status"] == "ok" and "마지막 백업" in item["detail"]
    os.utime(info["path"], (time.time() - 3 * 86400, time.time() - 3 * 86400))
    assert system_status._backup_item(conn)["status"] == "warn"


def test_cli_backup_list_restore_and_reset_takes_a_backup_first(live_db, capsys):
    from hair_platform import cli

    path, conn = live_db
    conn.close()
    assert cli.main(["backup", "--db-path", path]) == 0
    assert "백업 완료" in capsys.readouterr().out
    assert cli.main(["backup", "--list", "--db-path", path]) == 0
    assert "hair-backup-" in capsys.readouterr().out
    assert cli.main(["reset-data", "--db-path", path, "--yes"]) == 0
    out = capsys.readouterr().out
    assert "초기화 전 백업" in out and "before-reset" in out
    assert _briefs(path) == []
    archive = next(b["path"] for b in backup.list_backups(backup.backup_dir(path)) if "before-reset" in b["name"])
    assert cli.main(["restore", archive, "--db-path", path, "--yes"]) == 0
    assert _briefs(path) == ["백업 전 업무"]  # 초기화를 되돌렸다
