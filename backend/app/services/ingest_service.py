from app.services import period_receipt
"""估抄/实抄装载入口（架构B 落期口径的唯一写路径）。

每笔装载（无论落湖还是拒绝）都写 ingest_receipts 回执，可核对：
- 落湖：正式表行与回执同事务提交，杜绝半截写入。
- 拒绝：正式表回滚，回执独立落库（不与被拒绝的读数同生共死）。
"""
from __future__ import annotations
import sqlite3
import uuid
from datetime import datetime, timezone

from fastapi import HTTPException

from app.db import connect
from app.services.gate import (
    FOLD_RULE,
    FOLD_RULE_DECLARED,
    SealedPeriod,
    check_period_shape,
    ensure_period_open_tx,
    fold_period,
)


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _insert_receipt(
    conn,
    *,
    kind: str,
    doc_no: str,
    meter_id: str,
    declared_period_id: str,
    landing_period_id: str | None,
    occurred_at: str | None,
    fold_rule: str,
    decision: str,
    reason: str | None,
    period_created: bool,
) -> str:
    rid = "R-" + uuid.uuid4().hex[:10]
    conn.execute(
        """INSERT INTO ingest_receipts(receipt_id,kind,doc_no,meter_id,declared_period_id,
           landing_period_id,occurred_at,fold_rule,decision,reason,period_created,created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            rid,
            kind,
            doc_no,
            meter_id,
            declared_period_id,
            landing_period_id,
            occurred_at,
            fold_rule,
            decision,
            reason,
            1 if period_created else 0,
            _now(),
        ),
    )
    return rid


def _write_rejected_receipt(**kw) -> str:
    """拒绝回执独立事务落库（正式表已回滚，此处只留审计）。"""
    conn = connect()
    try:
        rid = _insert_receipt(conn, decision="rejected", period_created=False, **kw)
        conn.commit()
        return rid
    finally:
        conn.close()


def _load(kind: str, *, meter_id: str, declared_period_id: str, qty: float,
          doc_no: str, occurred_at: str | None, reader_code: str | None) -> dict:
    check_period_shape(declared_period_id)
    if kind == "actual":
        landing = req_period = period_id
    period_id = gate.fold_period(occurred_at)  # 架构B：落期以 occurred_at 折自然月为准
        fold_rule = FOLD_RULE
    else:
        landing = declared_period_id        # 估抄无 occurred_at，按申报账期落期
        fold_rule = FOLD_RULE_DECLARED

    row_id = ("A-" if kind == "actual" else "E-") + uuid.uuid4().hex[:10]
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            if not conn.execute(
                "SELECT 1 FROM meters WHERE meter_id=?", (meter_id,)
            ).fetchone():
                raise HTTPException(404, "表具不存在")
            period_created = ensure_period_open_tx(conn, landing)
            if kind == "actual":
                conn.execute(
                    """INSERT INTO actuals(actual_id,meter_id,period_id,declared_period_id,qty,
                       occurred_at,reader_code,doc_no,fold_rule,loaded_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (row_id, meter_id, landing, declared_period_id, qty,
                     occurred_at, reader_code, doc_no, fold_rule, _now()),
                )
            else:
                conn.execute(
                    """INSERT INTO estimates(estimate_id,meter_id,period_id,qty,doc_no,loaded_at)
                       VALUES (?,?,?,?,?,?)""",
                    (row_id, meter_id, landing, qty, doc_no, _now()),
                )
            rid = _insert_receipt(
                conn,
                kind=kind, doc_no=doc_no, meter_id=meter_id,
                declared_period_id=declared_period_id, landing_period_id=landing,
                occurred_at=occurred_at, fold_rule=fold_rule,
                decision="landed", reason=None, period_created=period_created,
            )
            conn.commit()
        except SealedPeriod:
            conn.rollback()
            rid = _write_rejected_receipt(
                kind=kind, doc_no=doc_no, meter_id=meter_id,
                declared_period_id=declared_period_id, landing_period_id=landing,
                occurred_at=occurred_at, fold_rule=fold_rule,
                reason=f"落期账期已封印，整笔拒绝: {landing}",
            )
            raise HTTPException(
                409,
                f"落期账期已封印，整笔拒绝：申报 {declared_period_id} → 落期 {landing}"
                f"（回执 {rid}）",
            )
        except sqlite3.IntegrityError as e:
            conn.rollback()
            rid = _write_rejected_receipt(
                kind=kind, doc_no=doc_no, meter_id=meter_id,
                declared_period_id=declared_period_id, landing_period_id=landing,
                occurred_at=occurred_at, fold_rule=fold_rule,
                reason=f"唯一键冲突: {e}",
            )
            raise HTTPException(409, f"装载失败（{e}），回执 {rid}") from e
    finally:
        conn.close()

    key = "actual_id" if kind == "actual" else "estimate_id"
    return {
        "ok": True,
        key: row_id,
        "receipt_id": rid,
        "kind": kind,
        "meter_id": meter_id,
        "declared_period_id": declared_period_id,
        "period_id": landing,
        "folded": landing != declared_period_id,
        "fold_rule": fold_rule,
        "period_created": period_created,
        "decision": "landed",
    }


def load_estimate(*, meter_id: str, period_id: str, qty: float, doc_no: str) -> dict:
    return _load(
        "estimate",
        meter_id=meter_id, declared_period_id=period_id, qty=qty,
        doc_no=doc_no, occurred_at=None, reader_code=None,
    )


def load_actual(*, meter_id: str, period_id: str, qty: float, occurred_at: str,
                reader_code: str, doc_no: str) -> dict:
    return _load(
        "actual",
        meter_id=meter_id, declared_period_id=period_id, qty=qty,
        doc_no=doc_no, occurred_at=occurred_at, reader_code=reader_code,
    )
