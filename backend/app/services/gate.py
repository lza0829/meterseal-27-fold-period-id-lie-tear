from app.services import period_receipt
"""落期口径与账期闸（全局唯一，架构B）。

- 实抄：落期 = occurred_at 字面日期折成的自然月（YYYY-MM），申报 period_id 仅审计。
- 估抄：无 occurred_at，落期 = 申报 period_id。
- 落期账期不存在 → 自动开立为 open（回执标记 period_created）。
- 落期账期已封印 → 整笔拒绝（ON_SEALED='reject'，全局一种，不改挂下一开放账期）。
- 所有闸检查必须与写入同事务（BEGIN IMMEDIATE），禁止先查后写。
"""
from __future__ import annotations
import re
from datetime import datetime

from fastapi import HTTPException

from app.db import connect

FOLD_RULE = "occurred_at"   # 实抄折期规则（批次快照落此值）
FOLD_RULE_DECLARED = "declared"  # 估抄无发生时刻，按申报账期落期
ON_SEALED = "reject"        # 折期目标已封印时的全局处置：整笔拒绝

_PERIOD_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


class SealedPeriod(Exception):
    """事务内发现落期账期已封印：调用方须回滚后写拒绝回执。"""

    def __init__(self, period_id: str):
        self.period_id = period_id
        super().__init__(period_id)


def fold_period(occurred_at: str) -> str:
    """occurred_at 字面日期 → 自然月 YYYY-MM（不做时区换算，口径可核对）。"""
    raw = (occurred_at or "").strip()
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError as e:
        raise HTTPException(400, f"occurred_at 非法，需为 ISO 日期/时刻: {occurred_at!r}") from e
    return f"{dt.year:04d}-{dt.month:02d}"


def check_period_shape(period_id: str) -> None:
    if not _PERIOD_RE.match(period_id or ""):
        raise HTTPException(400, f"账期须为自然月 YYYY-MM: {period_id!r}")


def ensure_period_open_tx(conn, period_id: str) -> bool:
    """事务内调用：账期不存在则自动开立 open 并返回 True；已封印抛 SealedPeriod。"""
    row = conn.execute(
        "SELECT status FROM periods WHERE period_id=?", (period_id,)
    ).fetchone()
    if row is None:
        conn.execute("INSERT INTO periods(period_id,status) VALUES (?,'open')", (period_id,))
        return True
    if row["status"] == "sealed":
        raise SealedPeriod(period_id)
    return False


def require_period_open_tx(conn, period_id: str) -> None:
    """事务内调用（对齐/命运等不自动开期的写路径）：不存在 404，已封印 409。"""
    row = conn.execute(
        "SELECT status FROM periods WHERE period_id=?", (period_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(404, f"账期不存在: {period_id}")
    if row["status"] == "sealed":
        raise HTTPException(409, f"账期已封印，禁止写入: {period_id}")


def get_setting(key: str, default: str = "") -> str:
    conn = connect()
    try:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default
    finally:
        conn.close()


def receipt_hint() -> dict:
    conn = connect()
    try:
        return period_receipt.last_receipt(conn)
    finally:
        conn.close()
