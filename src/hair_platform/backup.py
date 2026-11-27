"""데이터 백업·복원(2026-11-27, 개선 제안 5-1 — "데이터를 잃으면 되돌릴 수 없다").

예전: 백업 명령이 없었고, 되돌릴 수 없는 `hair reset-data`도 지우기 전에 사본을 남기지 않았다. 운영 DB는 WAL 모드라
파일을 그냥 복사하면 최근 변경이 빠진다(이번 점검에서 실제로 겪음 — 복사본에 업무가 0건으로 보였다).

- **백업 한 개 = `.tar.gz` 파일 하나**: SQLite 온라인 백업 API로 뜬 DB(서버가 켜져 있어도 일관된 사본) + 벡터 저장 폴더
  (`<db>-vectors` 또는 `HAIR_VECTOR_DIR`) + `manifest.json`(버전·시각·이유). 업로드 문서·평가 리포트는 DB 안에 있다.
- **자동 백업**: `hair serve`의 자동화 루프가 하루에 한 번(마지막 백업이 24시간보다 오래되면) 뜨고, 최근
  `HAIR_BACKUP_KEEP`개(기본 7, 0이면 자동 백업 끔)만 남긴다. 저장 위치는 `HAIR_BACKUP_DIR`(기본 DB 파일 옆 `backups/`).
- **복원**: 서버를 끄고 `hair restore <파일>`. 지금 데이터는 지우지 않고 `<db>.before-restore-<시각>`으로 옮겨 둔다.
  압축 안의 경로를 검사하고(바깥 경로·링크 거부), 꺼낸 DB의 무결성을 확인한 뒤에만 바꾼다.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import sqlite3
import tarfile
import tempfile
import time
from pathlib import Path

PREFIX = "hair-backup-"
SUFFIX = ".tar.gz"
DEFAULT_KEEP = 7
AUTO_INTERVAL_SECONDS = 24 * 3600
STALE_WARN_SECONDS = 48 * 3600


class BackupError(RuntimeError):
    pass


def db_file(conn: sqlite3.Connection) -> str | None:
    for _seq, name, path in conn.execute("PRAGMA database_list"):
        if name == "main":
            return path or None
    return None


def backup_dir(db_path: str) -> Path:
    explicit = os.environ.get("HAIR_BACKUP_DIR")
    if explicit:
        return Path(explicit).expanduser()
    return Path(os.path.abspath(db_path)).parent / "backups"


def keep_count() -> int:
    try:
        return max(0, int(os.environ.get("HAIR_BACKUP_KEEP") or DEFAULT_KEEP))
    except ValueError:
        return DEFAULT_KEEP


def _vector_dir(conn: sqlite3.Connection) -> str | None:
    from hair_platform import vector_backends

    return vector_backends.vector_dir(conn)


def list_backups(dest: Path) -> list[dict]:
    if not dest.is_dir():
        return []
    out = []
    for p in dest.glob(PREFIX + "*" + SUFFIX):
        st = p.stat()
        out.append({"path": str(p), "name": p.name, "size": st.st_size, "mtime": st.st_mtime,
                    "created_at": dt.datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S")})
    return sorted(out, key=lambda b: b["mtime"], reverse=True)


def prune(dest: Path, keep: int) -> list[str]:
    """최근 `keep`개만 남긴다(0이면 지우지 않음)."""
    if keep <= 0:
        return []
    removed = []
    for b in list_backups(dest)[keep:]:
        try:
            os.remove(b["path"])
            removed.append(b["name"])
        except OSError:
            pass
    return removed


def create_backup(conn: sqlite3.Connection, *, dest: Path | None = None, reason: str = "manual") -> dict:
    from hair_platform import get_version

    path = db_file(conn)
    if not path:
        raise BackupError("메모리 DB는 백업할 수 없습니다")
    dest = dest or backup_dir(path)
    dest.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    tag = "" if reason == "manual" else "-" + "".join(c for c in reason if c.isalnum() or c == "-")[:20]
    name = f"{PREFIX}{stamp}{tag}{SUFFIX}"
    final = dest / name
    n = 1
    while final.exists():  # 같은 초에 두 번
        final = dest / f"{PREFIX}{stamp}{tag}-{n}{SUFFIX}"
        n += 1
    with tempfile.TemporaryDirectory(prefix="hair_backup_") as tmp:
        work = Path(tmp)
        target = sqlite3.connect(str(work / "hair.db"))
        try:
            conn.backup(target)  # 온라인 백업 — 쓰는 중이어도 일관된 사본(WAL 내용 포함)
        finally:
            target.close()
        vdir = _vector_dir(conn)
        has_vectors = bool(vdir and os.path.isdir(vdir))
        if has_vectors:
            shutil.copytree(vdir, work / "vectors", symlinks=False)  # pyright: ignore[reportArgumentType]
        manifest = {"format": 1, "hair_version": get_version(), "created_at": dt.datetime.now().isoformat(timespec="seconds"),
                    "reason": reason, "db_file": os.path.basename(path), "vectors": has_vectors,
                    "db_bytes": (work / "hair.db").stat().st_size}
        (work / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
        partial = final.with_name(final.name + ".partial")
        with tarfile.open(partial, "w:gz") as tar:
            tar.add(work / "manifest.json", arcname="manifest.json")
            tar.add(work / "hair.db", arcname="hair.db")
            if has_vectors:
                tar.add(work / "vectors", arcname="vectors")
        os.replace(partial, final)
    removed = prune(dest, keep_count()) if reason == "auto" else []
    return {"path": str(final), "name": final.name, "size": final.stat().st_size, "removed": removed, **manifest}


def _safe_members(tar: tarfile.TarFile) -> list[tarfile.TarInfo]:
    members = []
    for m in tar.getmembers():
        name = m.name
        if name.startswith(("/", "\\")) or ".." in Path(name).parts:
            raise BackupError(f"백업 파일 안에 허용하지 않는 경로가 있습니다: {name}")
        if m.issym() or m.islnk() or m.isdev():
            raise BackupError(f"백업 파일 안에 링크·장치 파일이 있습니다: {name}")
        if not (name in ("manifest.json", "hair.db") or name == "vectors" or name.startswith("vectors/")):
            raise BackupError(f"HAIR 백업이 아닌 파일이 들어 있습니다: {name}")
        members.append(m)
    return members


def inspect(archive: str) -> dict:
    """복원 전에 보여 줄 정보 — 백업 시각·버전·크기."""
    try:
        with tarfile.open(archive, "r:gz") as tar:
            _safe_members(tar)
            f = tar.extractfile("manifest.json")
            if f is None:
                raise BackupError("manifest.json이 없습니다 — HAIR 백업 파일이 아닙니다")
            return json.loads(f.read().decode("utf-8"))
    except (tarfile.TarError, KeyError, OSError, ValueError) as exc:
        raise BackupError(f"백업 파일을 읽지 못했습니다: {exc}") from exc


def restore_backup(archive: str, db_path: str, *, vector_dir: str | None = None) -> dict:
    """서버를 끈 상태에서 부른다. 지금 데이터는 `.before-restore-<시각>`으로 옮겨 둔다."""
    manifest = inspect(archive)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    with tempfile.TemporaryDirectory(prefix="hair_restore_") as tmp:
        work = Path(tmp)
        with tarfile.open(archive, "r:gz") as tar:
            members = _safe_members(tar)
            for m in members:  # 경로는 위에서 검사했다. 새 파이썬이면 표준 안전 필터도 함께 쓴다.
                try:
                    tar.extract(m, work, filter="data")
                except TypeError:  # filter 인자가 없는 옛 파이썬
                    tar.extract(m, work)  # noqa: S202
        check = sqlite3.connect(str(work / "hair.db"))
        try:
            ok = check.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        finally:
            check.close()
        if not ok:
            raise BackupError("백업 안의 DB가 손상돼 있습니다 — 다른 백업을 쓰세요")
        moved = []
        db_abs = os.path.abspath(db_path)
        for suffix in ("", "-wal", "-shm"):
            if os.path.exists(db_abs + suffix):
                aside = f"{db_abs}.before-restore-{stamp}{suffix}"
                os.replace(db_abs + suffix, aside)
                moved.append(aside)
        os.makedirs(os.path.dirname(db_abs) or ".", exist_ok=True)
        shutil.copy2(work / "hair.db", db_abs)
        vdir = vector_dir or os.environ.get("HAIR_VECTOR_DIR") or f"{db_abs}-vectors"
        if os.path.isdir(vdir):
            aside = f"{vdir}.before-restore-{stamp}"
            os.replace(vdir, aside)
            moved.append(aside)
        if (work / "vectors").is_dir():
            shutil.copytree(work / "vectors", vdir)
    return {"restored": db_abs, "moved_aside": moved, **manifest}


def latest(conn: sqlite3.Connection) -> dict | None:
    path = db_file(conn)
    if not path:
        return None
    items = list_backups(backup_dir(path))
    return items[0] if items else None


def run_scheduled_backup(conn: sqlite3.Connection) -> dict | None:
    """자동화 루프(`automation.tick`) — 마지막 백업이 하루보다 오래됐으면 새로 뜬다. `HAIR_BACKUP_KEEP=0`이면 끔."""
    if keep_count() == 0 or not db_file(conn):
        return None
    last = latest(conn)
    if last and time.time() - last["mtime"] < AUTO_INTERVAL_SECONDS:
        return None
    return create_backup(conn, reason="auto")
