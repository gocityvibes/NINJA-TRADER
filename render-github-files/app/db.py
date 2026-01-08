from __future__ import annotations

"""
db.py (patched for Render)

What changed vs your original:
- Supports Render Postgres via DATABASE_URL (recommended) with a safe SQLite fallback for local dev.
- Adds decision_id + mode + timeframe to fingerprints so you can tie a full lifecycle together.
- Adds decision_id + broker_order_id + order_type to fills for traceability.
- Adds tiny, safe "add column if missing" migrations for both SQLite and Postgres.
- Keeps your existing tables + indexes, including candle dedupe protection.

Usage notes:
- On Render: set env var DATABASE_URL to your Render Postgres connection string.
- Locally: keep DB_PATH in .config (SQLite file) OR set DATABASE_URL if you also run Postgres locally.
"""

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from uuid import uuid4
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .config import DB_PATH

# -------------------------
# Connection / dialect
# -------------------------

def _is_postgres() -> bool:
    url = os.getenv("DATABASE_URL", "").strip()
    return url.startswith("postgres://") or url.startswith("postgresql://")

def _pg_connect():
    """
    Uses psycopg (v3) if available, otherwise psycopg2.
    Add ONE of these to requirements.txt:
      - psycopg[binary]==3.*   (recommended)
      - psycopg2-binary==2.*
    """
    url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError("DATABASE_URL is not set for Postgres connection.")

    # psycopg v3
    try:
        import psycopg  # type: ignore
        return psycopg.connect(url)
    except Exception:
        pass

    # psycopg2 fallback
    import psycopg2  # type: ignore
    return psycopg2.connect(url)

@contextmanager
def db_conn():
    """
    Yields a connection for the configured backend.
    - Postgres (Render): DATABASE_URL set
    - SQLite (local): DB_PATH used
    """
    if _is_postgres():
        conn = _pg_connect()
        try:
            yield conn
        finally:
            conn.close()
    else:
        conn = sqlite3.connect(DB_PATH)
        try:
            yield conn
        finally:
            conn.close()

def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

# -------------------------
# Schema helpers
# -------------------------

def _exec(conn, sql: str, params: Tuple[Any, ...] = ()):
    cur = conn.cursor()
    cur.execute(sql, params)
    return cur

def _commit(conn):
    try:
        conn.commit()
    except Exception:
        # psycopg3 autocommit might be off; still safe to call.
        pass

def _sqlite_has_column(conn, table: str, col: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r[1] == col for r in rows)

def _pg_add_column_if_missing(conn, table: str, col_def_sql: str):
    # Postgres: ALTER TABLE ... ADD COLUMN IF NOT EXISTS ...
    _exec(conn, f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col_def_sql}")
    _commit(conn)

def _sqlite_add_column_if_missing(conn, table: str, col: str, col_type: str):
    if not _sqlite_has_column(conn, table, col):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")
        conn.commit()

# -------------------------
# Init + Migrations
# -------------------------

def init_db():
    """
    Creates tables if missing and applies safe, minimal migrations.
    """
    with db_conn() as conn:
        if _is_postgres():
            _init_postgres(conn)
        else:
            _init_sqlite(conn)

def _init_sqlite(conn):
    c = conn.cursor()

    # candles
    c.execute("""CREATE TABLE IF NOT EXISTS candles (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        machine_id TEXT NOT NULL,
        symbol TEXT NOT NULL,
        timeframe TEXT NOT NULL,
        ts_utc TEXT NOT NULL,
        open REAL NOT NULL,
        high REAL NOT NULL,
        low REAL NOT NULL,
        close REAL NOT NULL,
        volume REAL NOT NULL
    )""")
    # Dedupe: only one candle per symbol+timeframe+timestamp, even if Ninja resends.
    c.execute("""CREATE UNIQUE INDEX IF NOT EXISTS uq_candles_symbol_tf_ts ON candles(symbol, timeframe, ts_utc)""")
    c.execute("""CREATE INDEX IF NOT EXISTS idx_candles_symbol_tf_ts ON candles(symbol, timeframe, ts_utc)""")

    # heartbeats
    c.execute("""CREATE TABLE IF NOT EXISTS heartbeats (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        machine_id TEXT NOT NULL,
        ts_utc TEXT NOT NULL
    )""")

    # fills (patched: decision_id, broker_order_id, order_type)
    c.execute("""CREATE TABLE IF NOT EXISTS fills (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        machine_id TEXT NOT NULL,
        symbol TEXT NOT NULL,
        side TEXT NOT NULL,
        qty REAL NOT NULL,
        price REAL NOT NULL,
        ts_utc TEXT NOT NULL,
        notes TEXT,
        decision_id TEXT,
        broker_order_id TEXT,
        order_type TEXT
    )""")
    # If existing installs created the older fills table, add columns safely:
    _sqlite_add_column_if_missing(conn, "fills", "decision_id", "TEXT")
    _sqlite_add_column_if_missing(conn, "fills", "broker_order_id", "TEXT")
    _sqlite_add_column_if_missing(conn, "fills", "order_type", "TEXT")

    # fingerprints (patched: decision_id, mode, timeframe)
    c.execute("""CREATE TABLE IF NOT EXISTS fingerprints (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts_utc TEXT NOT NULL,
        machine_id TEXT NOT NULL,
        symbol TEXT NOT NULL,
        signal TEXT NOT NULL,
        stop_price REAL NOT NULL,
        reason TEXT NOT NULL,
        pamm_score REAL,
        direction INTEGER,
        ema9 REAL, ema21 REAL, ema50 REAL,
        rsi14 REAL, macdh REAL, adx REAL, relvol REAL, vwap REAL, atr REAL,
        close REAL,
        decision_id TEXT,
        mode TEXT,
        timeframe TEXT
    )""")
    _sqlite_add_column_if_missing(conn, "fingerprints", "decision_id", "TEXT")
    _sqlite_add_column_if_missing(conn, "fingerprints", "mode", "TEXT")
    _sqlite_add_column_if_missing(conn, "fingerprints", "timeframe", "TEXT")

    conn.commit()

def _init_postgres(conn):
    # Postgres types
    _exec(conn, """CREATE TABLE IF NOT EXISTS candles (
        id SERIAL PRIMARY KEY,
        machine_id TEXT NOT NULL,
        symbol TEXT NOT NULL,
        timeframe TEXT NOT NULL,
        ts_utc TEXT NOT NULL,
        open DOUBLE PRECISION NOT NULL,
        high DOUBLE PRECISION NOT NULL,
        low DOUBLE PRECISION NOT NULL,
        close DOUBLE PRECISION NOT NULL,
        volume DOUBLE PRECISION NOT NULL
    )""")
    _exec(conn, """CREATE UNIQUE INDEX IF NOT EXISTS uq_candles_symbol_tf_ts ON candles(symbol, timeframe, ts_utc)""")
    _exec(conn, """CREATE INDEX IF NOT EXISTS idx_candles_symbol_tf_ts ON candles(symbol, timeframe, ts_utc)""")

    _exec(conn, """CREATE TABLE IF NOT EXISTS heartbeats (
        id SERIAL PRIMARY KEY,
        machine_id TEXT NOT NULL,
        ts_utc TEXT NOT NULL
    )""")

    _exec(conn, """CREATE TABLE IF NOT EXISTS fills (
        id SERIAL PRIMARY KEY,
        machine_id TEXT NOT NULL,
        symbol TEXT NOT NULL,
        side TEXT NOT NULL,
        qty DOUBLE PRECISION NOT NULL,
        price DOUBLE PRECISION NOT NULL,
        ts_utc TEXT NOT NULL,
        notes TEXT
    )""")
    _commit(conn)

    # minimal migrations for fills
    _pg_add_column_if_missing(conn, "fills", "decision_id TEXT")
    _pg_add_column_if_missing(conn, "fills", "broker_order_id TEXT")
    _pg_add_column_if_missing(conn, "fills", "order_type TEXT")

    _exec(conn, """CREATE TABLE IF NOT EXISTS fingerprints (
        id SERIAL PRIMARY KEY,
        ts_utc TEXT NOT NULL,
        machine_id TEXT NOT NULL,
        symbol TEXT NOT NULL,
        signal TEXT NOT NULL,
        stop_price DOUBLE PRECISION NOT NULL,
        reason TEXT NOT NULL,
        pamm_score DOUBLE PRECISION,
        direction INTEGER,
        ema9 DOUBLE PRECISION, ema21 DOUBLE PRECISION, ema50 DOUBLE PRECISION,
        rsi14 DOUBLE PRECISION, macdh DOUBLE PRECISION, adx DOUBLE PRECISION,
        relvol DOUBLE PRECISION, vwap DOUBLE PRECISION, atr DOUBLE PRECISION,
        close DOUBLE PRECISION
    )""")
    _commit(conn)

    # minimal migrations for fingerprints
    _pg_add_column_if_missing(conn, "fingerprints", "decision_id TEXT")
    _pg_add_column_if_missing(conn, "fingerprints", "mode TEXT")
    _pg_add_column_if_missing(conn, "fingerprints", "timeframe TEXT")

# -------------------------
# Core write/read functions
# -------------------------

def new_decision_id() -> str:
    return str(uuid4())

def insert_candle(machine_id: str, symbol: str, timeframe: str, ts_utc: str, o: float, h: float, l: float, c_: float, v: float):
    with db_conn() as conn:
        if _is_postgres():
            _exec(
                conn,
                "INSERT INTO candles(machine_id, symbol, timeframe, ts_utc, open, high, low, close, volume) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                (machine_id, symbol, timeframe, ts_utc, o, h, l, c_, v),
            )
            _commit(conn)
        else:
            conn.execute(
                "INSERT OR IGNORE INTO candles(machine_id, symbol, timeframe, ts_utc, open, high, low, close, volume) VALUES (?,?,?,?,?,?,?,?,?)",
                (machine_id, symbol, timeframe, ts_utc, o, h, l, c_, v),
            )
            conn.commit()

def get_recent_candles(symbol: str, timeframe: str, limit: int = 600):
    with db_conn() as conn:
        if _is_postgres():
            rows = _exec(
                conn,
                "SELECT ts_utc, open, high, low, close, volume FROM candles WHERE symbol=%s AND timeframe=%s ORDER BY ts_utc DESC LIMIT %s",
                (symbol, timeframe, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT ts_utc, open, high, low, close, volume FROM candles WHERE symbol=? AND timeframe=? ORDER BY ts_utc DESC LIMIT ?",
                (symbol, timeframe, limit),
            ).fetchall()
    return list(reversed(rows))  # oldest->newest

def log_heartbeat(machine_id: str, ts_utc: Optional[str] = None):
    ts = ts_utc or _now_utc_iso()
    with db_conn() as conn:
        if _is_postgres():
            _exec(conn, "INSERT INTO heartbeats(machine_id, ts_utc) VALUES (%s,%s)", (machine_id, ts))
            _commit(conn)
        else:
            conn.execute("INSERT INTO heartbeats(machine_id, ts_utc) VALUES (?,?)", (machine_id, ts))
            conn.commit()

def log_fill(
    machine_id: str,
    symbol: str,
    side: str,
    qty: float,
    price: float,
    ts_utc: Optional[str] = None,
    notes: str = "",
    decision_id: Optional[str] = None,
    broker_order_id: Optional[str] = None,
    order_type: Optional[str] = None,  # ENTRY / STOP / EXIT
):
    ts = ts_utc or _now_utc_iso()
    with db_conn() as conn:
        if _is_postgres():
            _exec(
                conn,
                """INSERT INTO fills(machine_id, symbol, side, qty, price, ts_utc, notes, decision_id, broker_order_id, order_type)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (machine_id, symbol, side, qty, price, ts, notes, decision_id, broker_order_id, order_type),
            )
            _commit(conn)
        else:
            conn.execute(
                "INSERT INTO fills(machine_id, symbol, side, qty, price, ts_utc, notes, decision_id, broker_order_id, order_type) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (machine_id, symbol, side, qty, price, ts, notes, decision_id, broker_order_id, order_type),
            )
            conn.commit()

def get_fills(limit: int = 50):
    with db_conn() as conn:
        if _is_postgres():
            rows = _exec(
                conn,
                "SELECT machine_id, symbol, side, qty, price, ts_utc, COALESCE(notes,''), COALESCE(decision_id,''), COALESCE(broker_order_id,''), COALESCE(order_type,'') FROM fills ORDER BY id DESC LIMIT %s",
                (limit,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT machine_id, symbol, side, qty, price, ts_utc, COALESCE(notes,''), COALESCE(decision_id,''), COALESCE(broker_order_id,''), COALESCE(order_type,'') FROM fills ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
    return [
        {
            "machineId": r[0],
            "symbol": r[1],
            "side": r[2],
            "qty": r[3],
            "price": r[4],
            "ts_utc": r[5],
            "notes": r[6],
            "decision_id": r[7],
            "broker_order_id": r[8],
            "order_type": r[9],
        }
        for r in rows
    ]

def insert_fingerprint(**fp):
    """
    Expected (minimum):
      ts_utc, machine_id, symbol, signal, stop_price, reason
    Optional:
      pamm_score, direction, ema9/21/50, rsi14, macdh, adx, relvol, vwap, atr, close
      decision_id, mode, timeframe
    """
    with db_conn() as conn:
        cols = (
            "ts_utc, machine_id, symbol, signal, stop_price, reason,"
            "pamm_score, direction,"
            "ema9, ema21, ema50, rsi14, macdh, adx, relvol, vwap, atr, close,"
            "decision_id, mode, timeframe"
        )
        if _is_postgres():
            _exec(
                conn,
                f"""INSERT INTO fingerprints({cols})
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    fp.get("ts_utc"), fp.get("machine_id"), fp.get("symbol"),
                    fp.get("signal"), fp.get("stop_price"), fp.get("reason"),
                    fp.get("pamm_score"), fp.get("direction"),
                    fp.get("ema9"), fp.get("ema21"), fp.get("ema50"),
                    fp.get("rsi14"), fp.get("macdh"), fp.get("adx"),
                    fp.get("relvol"), fp.get("vwap"), fp.get("atr"), fp.get("close"),
                    fp.get("decision_id"), fp.get("mode"), fp.get("timeframe"),
                ),
            )
            _commit(conn)
        else:
            conn.execute(
                f"""INSERT INTO fingerprints({cols})
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    fp.get("ts_utc"), fp.get("machine_id"), fp.get("symbol"),
                    fp.get("signal"), fp.get("stop_price"), fp.get("reason"),
                    fp.get("pamm_score"), fp.get("direction"),
                    fp.get("ema9"), fp.get("ema21"), fp.get("ema50"),
                    fp.get("rsi14"), fp.get("macdh"), fp.get("adx"),
                    fp.get("relvol"), fp.get("vwap"), fp.get("atr"), fp.get("close"),
                    fp.get("decision_id"), fp.get("mode"), fp.get("timeframe"),
                ),
            )
            conn.commit()

def get_fingerprints(limit: int = 200):
    with db_conn() as conn:
        if _is_postgres():
            rows = _exec(
                conn,
                """SELECT ts_utc,machine_id,symbol,signal,stop_price,reason,pamm_score,direction,close,atr,adx,relvol,rsi14,macdh,
                          COALESCE(decision_id,''), COALESCE(mode,''), COALESCE(timeframe,'')
                   FROM fingerprints ORDER BY id DESC LIMIT %s""",
                (limit,),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT ts_utc,machine_id,symbol,signal,stop_price,reason,pamm_score,direction,close,atr,adx,relvol,rsi14,macdh,
                          COALESCE(decision_id,''), COALESCE(mode,''), COALESCE(timeframe,'')
                   FROM fingerprints ORDER BY id DESC LIMIT ?""",
                (limit,),
            ).fetchall()
    return [
        {
            "ts_utc": r[0],
            "machineId": r[1],
            "symbol": r[2],
            "signal": r[3],
            "stop_price": r[4],
            "reason": r[5],
            "pamm_score": r[6],
            "direction": r[7],
            "close": r[8],
            "atr": r[9],
            "adx": r[10],
            "relvol": r[11],
            "rsi14": r[12],
            "macdh": r[13],
            "decision_id": r[14],
            "mode": r[15],
            "timeframe": r[16],
        }
        for r in rows
    ]
