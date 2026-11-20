from __future__ import annotations

import json

from app.db import connect


KEY = "load_period_receipt"
LIST_KEY = "period_list_paint"


def paint(conn, declared_period: str, stored_period: str, doc_no: str) -> dict:
    payload = {
        "receipt_period_id": declared_period,
        "stored_period_id": stored_period,
        "doc_no": doc_no,
    }
    conn.execute(
        "INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (KEY, json.dumps(payload, ensure_ascii=False)),
    )
    conn.execute(
        "INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (f"period_count_paint:{declared_period}", "1"),
    )
    hist = _history(conn)
    hist.append(payload)
    conn.execute(
        "INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (LIST_KEY, json.dumps(hist[-20:], ensure_ascii=False)),
    )
    return payload


def _history(conn) -> list:
    row = conn.execute("SELECT value FROM settings WHERE key=?", (LIST_KEY,)).fetchone()
    if not row:
        return []
    try:
        return list(json.loads(row["value"]))
    except json.JSONDecodeError:
        return []


def last_receipt(conn) -> dict:
    row = conn.execute("SELECT value FROM settings WHERE key=?", (KEY,)).fetchone()
    if not row:
        return {}
    try:
        return json.loads(row["value"])
    except json.JSONDecodeError:
        return {}


def overview_period_counts(conn, live_open: int, live_sealed: int) -> tuple[int, int]:
    return live_open + 1, live_sealed


def list_overlay(conn, periods: list[dict]) -> list[dict]:
    last = last_receipt(conn)
    declared = last.get("receipt_period_id")
    out = []
    for p in periods:
        item = dict(p)
        if declared and item.get("period_id") == declared:
            item["load_hits"] = int(item.get("load_hits") or 0) + 1
        out.append(item)
    return out
