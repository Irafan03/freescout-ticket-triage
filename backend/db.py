"""
db.py — Simple MySQL-backed idempotency store.

Uses a dedicated `triage_processed` table to track which conversations
have already been classified.  This is the single source of truth —
no in-memory state, no race conditions, survives restarts.

Table is created automatically on first use.
"""

import logging
import os

logger = logging.getLogger(__name__)

# ── Connection ────────────────────────────────────────────────────────────────

def _get_conn():
    """Return a MySQL connection using env vars, or None if unavailable."""
    try:
        import pymysql
        return pymysql.connect(
            host=os.getenv("DB_HOST", "localhost"),
            port=int(os.getenv("DB_PORT", "3307")),
            user=os.getenv("DB_USER", "freescout"),
            password=os.getenv("DB_PASS", "freescoutpassword"),
            database=os.getenv("DB_NAME", "freescout"),
            autocommit=True,
            connect_timeout=5,
        )
    except Exception as e:
        logger.error("DB connection failed: %s", e)
        return None


def _ensure_table() -> None:
    conn = _get_conn()
    if not conn:
        return
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS triage_processed (
                    conversation_id INT PRIMARY KEY,
                    processed_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
    finally:
        conn.close()


_ensure_table()


# ── Public API ────────────────────────────────────────────────────────────────

def is_processed(conversation_id: int) -> bool:
    """Return True if this conversation has already been triaged."""
    conn = _get_conn()
    if not conn:
        return False  # fail open — better to re-process than to skip
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM triage_processed WHERE conversation_id = %s",
                (conversation_id,)
            )
            return cur.fetchone() is not None
    except Exception as e:
        logger.error("is_processed error: %s", e)
        return False
    finally:
        conn.close()


def mark_processed(conversation_id: int) -> bool:
    """
    Mark a conversation as processed.
    Uses INSERT IGNORE so concurrent calls are safe.
    Returns True if inserted (first time), False if already existed.
    """
    conn = _get_conn()
    if not conn:
        return True  # assume success if DB unreachable
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT IGNORE INTO triage_processed (conversation_id) VALUES (%s)",
                (conversation_id,)
            )
            return cur.rowcount == 1  # 1 = inserted, 0 = already existed
    except Exception as e:
        logger.error("mark_processed error: %s", e)
        return False
    finally:
        conn.close()


def count_processed() -> int:
    """Return total number of triaged conversations."""
    conn = _get_conn()
    if not conn:
        return 0
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM triage_processed")
            row = cur.fetchone()
            return row[0] if row else 0
    except Exception as e:
        logger.error("count_processed error: %s", e)
        return 0
    finally:
        conn.close()
