"""Durable local-only storage for normalized ``convo`` sessions.

The CLI owns harness discovery and parsing.  This module owns the SQLite schema and
the replacement rules: a successfully parsed changed source atomically replaces its
old snapshot; a failed parse never does.  Keeping that state transition here gives
callers one small, vendorable boundary instead of SQLite policy scattered through
the command handlers.
"""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import fcntl
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional


SCHEMA_VERSION = 3
DEFAULT_DATA_HOME = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "minnows" / "convo"
DEFAULT_MAX_SOURCE_BYTES = 64 * 1024 * 1024
SYNC_BATCH_SIZE = 250
SYNC_BATCH_MESSAGE_BYTES = 8 * 1024 * 1024


class LedgerSchemaError(ValueError):
    pass


class LedgerBusyError(ValueError):
    pass


@dataclass(frozen=True)
class Message:
    role: str
    text: str
    timestamp: Optional[str]


@dataclass(frozen=True)
class ParsedSource:
    harness: str
    path: str
    size: int
    mtime_ns: int
    device: int
    inode: int
    ctime_ns: int
    session_id: str
    project: Optional[str]
    messages: Iterable[Message]
    source_status: str = "present"
    parser_error: Optional[str] = None


def data_dir_from_environment() -> Path:
    """Return the owner-controlled ledger location (overrideable for tests)."""
    override = os.environ.get("CONVO_DATA_DIR")
    return Path(override).expanduser() if override else DEFAULT_DATA_HOME


def max_source_bytes_from_environment() -> int:
    """Return the full-parser safety cap; tests may lower it with an env override."""
    raw = os.environ.get("CONVO_MAX_SOURCE_BYTES")
    if raw is None:
        return DEFAULT_MAX_SOURCE_BYTES
    try:
        limit = int(raw)
    except ValueError as exc:
        raise ValueError("CONVO_MAX_SOURCE_BYTES must be a positive integer") from exc
    if limit < 1:
        raise ValueError("CONVO_MAX_SOURCE_BYTES must be a positive integer")
    return limit


def _completeness(status: str) -> str:
    # The ledger retains only normalized text, never an exact raw-event replay.
    if status == "present":
        return "normalized_snapshot_source_present"
    if status == "missing":
        return "normalized_snapshot_source_missing"
    if status == "partial":
        return "partial_normalized_snapshot_source_present"
    if status == "pending":
        return "pending_normalized_snapshot_source_present"
    if status == "live":
        return "live_normalized_snapshot_source_present"
    if status == "skipped":
        return "no_normalized_messages_source_present"
    return "normalized_snapshot_source_unavailable"


class Ledger:
    """SQLite-backed session snapshots with idempotent source synchronization."""

    def __init__(self, data_dir: Optional[Path] = None):
        self.data_dir = data_dir or data_dir_from_environment()
        self.db_path = self.data_dir / "ledger.sqlite3"
        self.lock_path = self.data_dir / "sync.lock"
        self._conn: Optional[sqlite3.Connection] = None
        self._batch_active = False

    def __enter__(self) -> "Ledger":
        return self

    def __exit__(self, _exc_type, _exc_value, _traceback) -> None:
        self.close()

    def close(self) -> None:
        """Release this command's SQLite handle deterministically."""
        if self._conn is not None:
            if self._batch_active:
                self._conn.rollback()
                self._batch_active = False
            self._conn.close()
            self._conn = None

    def begin_batch(self) -> None:
        """Start one bounded sync batch; completed earlier batches stay durable."""
        if not self._batch_active:
            self._connect().execute("BEGIN IMMEDIATE")
            self._batch_active = True

    def commit_batch(self) -> None:
        if self._batch_active:
            self._connect().commit()
            self._batch_active = False

    def rollback_batch(self) -> None:
        """Abort only the active bounded write batch; keep the connection usable."""
        if self._batch_active:
            self._connect().rollback()
            self._batch_active = False

    def _write(self, operation):
        conn = self._connect()
        if self._batch_active:
            return operation(conn)
        with conn:
            return operation(conn)

    def _connect(self) -> sqlite3.Connection:
        if self._conn is not None:
            return self._conn
        self.data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            self.data_dir.chmod(0o700)
        except OSError:
            pass
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            self._migrate(conn)
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA journal_mode = WAL")
        except Exception:
            conn.close()
            raise
        try:
            self.db_path.chmod(0o600)
        except OSError:
            pass
        self._conn = conn
        return self._conn

    @staticmethod
    def _migrate(conn: sqlite3.Connection) -> None:
        current_version = conn.execute("PRAGMA user_version").fetchone()[0]
        if current_version > SCHEMA_VERSION:
            raise LedgerSchemaError(
                f"ledger schema version {current_version} is newer than supported version {SCHEMA_VERSION}"
            )
        if current_version == SCHEMA_VERSION:
            return
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS source_files (
                id INTEGER PRIMARY KEY,
                harness TEXT NOT NULL,
                path TEXT NOT NULL UNIQUE,
                size INTEGER NOT NULL,
                mtime_ns INTEGER NOT NULL,
                device INTEGER,
                inode INTEGER,
                ctime_ns INTEGER,
                content_hash TEXT,
                source_status TEXT NOT NULL DEFAULT 'present',
                parser_error TEXT,
                parser_version TEXT NOT NULL DEFAULT '',
                policy_version TEXT NOT NULL DEFAULT '',
                source_cap INTEGER,
                session_id TEXT,
                project TEXT,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY,
                source_id INTEGER NOT NULL REFERENCES source_files(id) ON DELETE CASCADE,
                ordinal INTEGER NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
                text TEXT NOT NULL,
                message_ts TEXT,
                UNIQUE(source_id, ordinal)
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
                text, content='messages', content_rowid='id'
            );
            CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
                INSERT INTO messages_fts(rowid, text) VALUES (new.id, new.text);
            END;
            CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
                INSERT INTO messages_fts(messages_fts, rowid, text)
                VALUES ('delete', old.id, old.text);
            END;
            CREATE TRIGGER IF NOT EXISTS messages_au AFTER UPDATE ON messages BEGIN
                INSERT INTO messages_fts(messages_fts, rowid, text)
                VALUES ('delete', old.id, old.text);
                INSERT INTO messages_fts(rowid, text) VALUES (new.id, new.text);
            END;
        """)
        existing_columns = {row["name"] for row in conn.execute("PRAGMA table_info(source_files)")}
        for column in ("device", "inode", "ctime_ns", "parser_version", "policy_version", "source_cap"):
            if column not in existing_columns:
                kind = "INTEGER" if column == "source_cap" else "TEXT"
                conn.execute(f"ALTER TABLE source_files ADD COLUMN {column} {kind}")
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    @contextmanager
    def sync_lock(self):
        """Fail fast rather than let simultaneous syncs race source-state transitions."""
        self.data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise LedgerBusyError("another convo sync is already running") from exc
            yield
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)

    def current_source_outcome(self, harness: str, path: Path, stat_result, parser_version: str,
                               policy_version: str, source_cap: Optional[int]) -> Optional[dict]:
        row = self._connect().execute(
            "SELECT size, mtime_ns, device, inode, ctime_ns, source_status, parser_error, parser_version, "
            "policy_version, source_cap "
            "FROM source_files WHERE harness = ? AND path = ?",
            (harness, str(path)),
        ).fetchone()
        if (
            row and row["size"] == stat_result.st_size and row["mtime_ns"] == stat_result.st_mtime_ns
            and row["device"] == stat_result.st_dev and row["inode"] == stat_result.st_ino
            and row["ctime_ns"] == stat_result.st_ctime_ns
            and row["parser_version"] == parser_version and row["policy_version"] == policy_version
            and row["source_cap"] == source_cap
        ):
            # Discovery is direct evidence that a previously missing physical path is
            # present again, even if inode and timestamps happen to be unchanged.
            if row["source_status"] not in ("missing", "live"):
                return {"source_status": row["source_status"], "parser_error": row["parser_error"]}
        return None

    def current_source_status(self, harness: str, path: Path, stat_result, parser_version: str,
                              policy_version: str, source_cap: Optional[int]) -> Optional[str]:
        outcome = self.current_source_outcome(harness, path, stat_result, parser_version, policy_version, source_cap)
        return outcome["source_status"] if outcome else None

    def source_is_current(self, harness: str, path: Path, stat_result, parser_version: str,
                          policy_version: str, source_cap: Optional[int]) -> bool:
        """Compatibility boolean for callers that only need cache membership."""
        return self.current_source_status(harness, path, stat_result, parser_version, policy_version, source_cap) is not None

    @staticmethod
    def hash_file(path: Path) -> str:
        """Hash a source without holding a large session log in memory."""
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    def replace_source(self, source: ParsedSource, content_hash: str, parser_version: str = "",
                       policy_version: str = "", source_cap: Optional[int] = None) -> None:
        """Atomically replace a source snapshot after parsing has already succeeded."""
        def replace(conn):
            conn.execute("SAVEPOINT replace_source")
            try:
                self._replace_source_rows(conn, source, content_hash, parser_version, policy_version, source_cap)
            except Exception:
                conn.execute("ROLLBACK TO replace_source")
                conn.execute("RELEASE replace_source")
                raise
            conn.execute("RELEASE replace_source")
        self._write(replace)

    @staticmethod
    def _replace_source_rows(conn: sqlite3.Connection, source: ParsedSource, content_hash: str,
                             parser_version: str, policy_version: str, source_cap: Optional[int]) -> None:
        existing = conn.execute("SELECT id FROM source_files WHERE path = ?", (source.path,)).fetchone()
        if existing:
            source_id = existing["id"]
            conn.execute("DELETE FROM messages WHERE source_id = ?", (source_id,))
            conn.execute("""
                UPDATE source_files SET harness = ?, size = ?, mtime_ns = ?, device = ?, inode = ?,
                    ctime_ns = ?, content_hash = ?,
                    source_status = ?, parser_error = ?, parser_version = ?, policy_version = ?, source_cap = ?,
                    session_id = ?, project = ?,
                    updated_at = CURRENT_TIMESTAMP WHERE id = ?
            """, (source.harness, source.size, source.mtime_ns, source.device, source.inode,
                  source.ctime_ns, content_hash, source.source_status, source.parser_error,
                  parser_version, policy_version, source_cap, source.session_id, source.project, source_id))
        else:
            cursor = conn.execute("""
                INSERT INTO source_files(harness, path, size, mtime_ns, device, inode, ctime_ns,
                    content_hash, source_status, parser_error, parser_version, policy_version, source_cap,
                    session_id, project)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (source.harness, source.path, source.size, source.mtime_ns, source.device,
                  source.inode, source.ctime_ns, content_hash, source.source_status, source.parser_error,
                  parser_version, policy_version, source_cap, source.session_id, source.project))
            source_id = cursor.lastrowid
        rows = []
        for ordinal, message in enumerate(source.messages):
            rows.append((source_id, ordinal, message.role, message.text, message.timestamp))
            if len(rows) >= 500:
                conn.executemany("INSERT INTO messages(source_id, ordinal, role, text, message_ts) VALUES (?, ?, ?, ?, ?)", rows)
                rows.clear()
        if rows:
            conn.executemany("INSERT INTO messages(source_id, ordinal, role, text, message_ts) VALUES (?, ?, ?, ?, ?)", rows)

    def record_parse_failure(self, harness: str, path: Path, stat_result, error: Exception,
                             parser_version: str = "", policy_version: str = "",
                             source_cap: Optional[int] = None) -> None:
        """Record failure metadata without touching the last known-good message rows."""
        detail = str(error).splitlines()[0][:500]
        def record(conn):
            conn.execute("""
                INSERT INTO source_files(harness, path, size, mtime_ns, device, inode, ctime_ns,
                    source_status, parser_error, parser_version, policy_version, source_cap)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'corrupt', ?, ?, ?, ?)
                ON CONFLICT(path) DO UPDATE SET harness = excluded.harness, size = excluded.size,
                    mtime_ns = excluded.mtime_ns, device = excluded.device, inode = excluded.inode,
                    ctime_ns = excluded.ctime_ns, source_status = 'corrupt',
                    parser_error = excluded.parser_error, parser_version = excluded.parser_version,
                    policy_version = excluded.policy_version, source_cap = excluded.source_cap,
                    updated_at = CURRENT_TIMESTAMP
            """, (harness, str(path), stat_result.st_size, stat_result.st_mtime_ns, stat_result.st_dev,
                  stat_result.st_ino, stat_result.st_ctime_ns, detail, parser_version, policy_version, source_cap))
        self._write(record)

    def record_partial(self, harness: str, path: Path, stat_result, detail: str,
                       parser_version: str, policy_version: str, source_cap: Optional[int]) -> None:
        """Keep a prior complete snapshot when a changed source has no safe rows to replace it."""
        detail = detail.splitlines()[0][:500]
        def record(conn):
            conn.execute("""
                INSERT INTO source_files(harness, path, size, mtime_ns, device, inode, ctime_ns,
                    source_status, parser_error, parser_version, policy_version, source_cap)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'partial', ?, ?, ?, ?)
                ON CONFLICT(path) DO UPDATE SET harness = excluded.harness, size = excluded.size,
                    mtime_ns = excluded.mtime_ns, device = excluded.device, inode = excluded.inode,
                    ctime_ns = excluded.ctime_ns, source_status = 'partial',
                    parser_error = excluded.parser_error, parser_version = excluded.parser_version,
                    policy_version = excluded.policy_version, source_cap = excluded.source_cap,
                    updated_at = CURRENT_TIMESTAMP
            """, (harness, str(path), stat_result.st_size, stat_result.st_mtime_ns, stat_result.st_dev,
                  stat_result.st_ino, stat_result.st_ctime_ns, detail, parser_version, policy_version, source_cap))
        self._write(record)

    def record_pending(self, harness: str, path: Path, stat_result, detail: str,
                       parser_version: str, policy_version: str, source_cap: Optional[int]) -> None:
        """Record a torn active source without discarding a prior safe snapshot."""
        detail = detail.splitlines()[0][:500]
        def record(conn):
            conn.execute("""
                INSERT INTO source_files(harness, path, size, mtime_ns, device, inode, ctime_ns,
                    source_status, parser_error, parser_version, policy_version, source_cap)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)
                ON CONFLICT(path) DO UPDATE SET harness = excluded.harness, size = excluded.size,
                    mtime_ns = excluded.mtime_ns, device = excluded.device, inode = excluded.inode,
                    ctime_ns = excluded.ctime_ns, source_status = 'pending',
                    parser_error = excluded.parser_error, parser_version = excluded.parser_version,
                    policy_version = excluded.policy_version, source_cap = excluded.source_cap,
                    updated_at = CURRENT_TIMESTAMP
            """, (harness, str(path), stat_result.st_size, stat_result.st_mtime_ns, stat_result.st_dev,
                  stat_result.st_ino, stat_result.st_ctime_ns, detail, parser_version, policy_version, source_cap))
        self._write(record)

    def record_live(self, harness: str, path: Path, stat_result, detail: str) -> None:
        """Record append activity without replacing a snapshot from an unstable source."""
        detail = detail.splitlines()[0][:500]
        def record(conn):
            conn.execute("""
                INSERT INTO source_files(harness, path, size, mtime_ns, device, inode, ctime_ns,
                    source_status, parser_error, parser_version, policy_version, source_cap)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'live', ?, '', '', NULL)
                ON CONFLICT(path) DO UPDATE SET harness = excluded.harness, size = excluded.size,
                    mtime_ns = excluded.mtime_ns, device = excluded.device, inode = excluded.inode,
                    ctime_ns = excluded.ctime_ns, source_status = 'live', parser_error = excluded.parser_error,
                    parser_version = '', policy_version = '', source_cap = NULL, updated_at = CURRENT_TIMESTAMP
            """, (harness, str(path), stat_result.st_size, stat_result.st_mtime_ns, stat_result.st_dev,
                  stat_result.st_ino, stat_result.st_ctime_ns, detail))
        self._write(record)

    def record_access_failure(self, harness: str, path: Path, error: Exception) -> None:
        """Keep an inaccessible discovered path from being mislabeled as disappeared."""
        detail = str(error).splitlines()[0][:500]
        def record(conn):
            existing = conn.execute("SELECT id FROM source_files WHERE path = ?", (str(path),)).fetchone()
            if existing:
                conn.execute("""
                    UPDATE source_files SET harness = ?, source_status = 'corrupt', parser_error = ?,
                        parser_version = '', policy_version = '', source_cap = NULL,
                        updated_at = CURRENT_TIMESTAMP WHERE id = ?
                """, (harness, detail, existing["id"]))
            else:
                conn.execute("""
                    INSERT INTO source_files(harness, path, size, mtime_ns, source_status, parser_error)
                    VALUES (?, ?, 0, 0, 'corrupt', ?)
                """, (harness, str(path), detail))
        self._write(record)

    def record_oversized(self, harness: str, path: Path, stat_result, limit: int,
                         parser_version: str = "", policy_version: str = "") -> None:
        """Record an honest partial-sync state without deleting an older snapshot."""
        detail = f"oversized source: {stat_result.st_size} bytes exceeds full-parser cap {limit} bytes"
        def record(conn):
            conn.execute("""
                INSERT INTO source_files(harness, path, size, mtime_ns, device, inode, ctime_ns,
                    source_status, parser_error, parser_version, policy_version, source_cap)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'oversized', ?, ?, ?, ?)
                ON CONFLICT(path) DO UPDATE SET harness = excluded.harness, size = excluded.size,
                    mtime_ns = excluded.mtime_ns, device = excluded.device, inode = excluded.inode,
                    ctime_ns = excluded.ctime_ns, source_status = 'oversized',
                    parser_error = excluded.parser_error, parser_version = excluded.parser_version,
                    policy_version = excluded.policy_version, source_cap = excluded.source_cap,
                    updated_at = CURRENT_TIMESTAMP
            """, (harness, str(path), stat_result.st_size, stat_result.st_mtime_ns, stat_result.st_dev,
                  stat_result.st_ino, stat_result.st_ctime_ns, detail, parser_version, policy_version, limit))
        self._write(record)

    def mark_missing_except(self, seen_paths: Iterable[str]) -> int:
        seen = tuple(seen_paths)
        def mark(conn):
            if seen:
                placeholders = ",".join("?" for _ in seen)
                cursor = conn.execute(
                    f"UPDATE source_files SET source_status = 'missing', updated_at = CURRENT_TIMESTAMP "
                    f"WHERE source_status != 'missing' AND path NOT IN ({placeholders})", seen)
            else:
                cursor = conn.execute(
                    "UPDATE source_files SET source_status = 'missing', updated_at = CURRENT_TIMESTAMP "
                    "WHERE source_status != 'missing'"
                )
            return cursor.rowcount
        return self._write(mark)

    def status(self) -> dict:
        conn = self._connect()
        status_counts = {row["source_status"]: row["count"] for row in conn.execute(
            "SELECT source_status, COUNT(*) AS count FROM source_files GROUP BY source_status"
        )}
        messages = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        failures = conn.execute("SELECT COUNT(*) FROM source_files WHERE source_status = 'corrupt'").fetchone()[0]
        partial = conn.execute("SELECT COUNT(*) FROM source_files WHERE source_status = 'partial'").fetchone()[0]
        skipped = conn.execute("SELECT COUNT(*) FROM source_files WHERE source_status = 'skipped'").fetchone()[0]
        pending = conn.execute("SELECT COUNT(*) FROM source_files WHERE source_status = 'pending'").fetchone()[0]
        live = conn.execute("SELECT COUNT(*) FROM source_files WHERE source_status = 'live'").fetchone()[0]
        oversized = conn.execute("SELECT COUNT(*) FROM source_files WHERE source_status = 'oversized'").fetchone()[0]
        return {"schema_version": SCHEMA_VERSION, "data_dir": str(self.data_dir), "database": str(self.db_path),
                "sources": status_counts, "messages": messages, "parser_failures": failures,
                "partial_sources": partial, "skipped_sources": skipped, "pending_sources": pending,
                "live_sources": live,
                "oversized_sources": oversized}

    @staticmethod
    def _fts_query(query: str) -> Optional[str]:
        # FTS syntax is deliberately not exposed in V1: only Unicode word terms become
        # quoted literals, so punctuation and operators cannot alter the SQL/FTS program.
        terms = re.findall(r"[^\W_]+", query, flags=re.UNICODE)
        return " AND ".join(f'"{term.replace(chr(34), chr(34) * 2)}"' for term in terms) or None

    def search(self, query: str, limit: int = 20, context: int = 1) -> list[dict]:
        fts_query = self._fts_query(query)
        if not fts_query:
            return []
        limit = max(1, min(limit, 100))
        context = max(0, min(context, 5))
        conn = self._connect()
        rows = conn.execute("""
                SELECT m.id, m.source_id, m.ordinal, m.role, m.text, m.message_ts,
                       s.harness, s.path, s.session_id, s.project, s.source_status,
                       bm25(messages_fts) AS relevance
                FROM messages_fts JOIN messages m ON m.id = messages_fts.rowid
                JOIN source_files s ON s.id = m.source_id
                WHERE messages_fts MATCH ?
                ORDER BY relevance ASC, m.message_ts DESC, m.id DESC LIMIT ?
        """, (fts_query, limit)).fetchall()
        results = []
        for row in rows:
            nearby = conn.execute("""
                    SELECT role, text, message_ts FROM messages
                    WHERE source_id = ? AND ordinal BETWEEN ? AND ? ORDER BY ordinal
            """, (row["source_id"], row["ordinal"] - context, row["ordinal"] + context)).fetchall()
            results.append({
                    "message_id": row["id"], "harness": row["harness"], "session_id": row["session_id"],
                    "project": row["project"], "path": row["path"], "role": row["role"],
                    "text": row["text"], "timestamp": row["message_ts"], "relevance": row["relevance"],
                    "source_status": row["source_status"], "completeness": _completeness(row["source_status"]),
                    "content_basis": "snapshot",
                    "context": [dict(item) for item in nearby],
            })
        return results
