from __future__ import annotations
import hashlib
import json
import uuid
from datetime import datetime, timezone

from fastapi import HTTPException

from app.db import connect
from app.services.gate import FOLD_RULE, require_period_open_tx


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def run_align(period_id: str) -> dict:
    """对齐物化：只扫描落期账期（period_id 列即落期），批次快照记录折期口径。

    快照字段：fold_rule=occurred_at、folded_count（折期笔数）、
    declared_periods（折入本期的原申报账期集合）；指纹覆盖键集合与折期笔数，
    落期变化 → 键集合/指纹变化，可对照。
    """
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        require_period_open_tx(conn, period_id)
        est = {
            r["meter_id"]: r
            for r in conn.execute(
                "SELECT * FROM estimates WHERE period_id=?", (period_id,)
            ).fetchall()
        }
        act = {
            r["meter_id"]: r
            for r in conn.execute(
                "SELECT * FROM actuals WHERE period_id=?", (period_id,)
            ).fetchall()
        }
        keys = sorted(set(est) | set(act))
        if not keys:
            raise HTTPException(400, "该账期无估抄/实抄可对齐")

        # expire prior open batches for period
        conn.execute(
            "UPDATE reconcile_batches SET status='expired' WHERE period_id=? AND status='open'",
            (period_id,),
        )

        folded_rows = [
            r for r in act.values()
            if r["declared_period_id"] and r["declared_period_id"] != period_id
        ]
        folded_count = len(folded_rows)
        declared_periods = sorted({r["declared_period_id"] for r in folded_rows})

        batch_id = "B-" + uuid.uuid4().hex[:10]
        matched = 0
        variances = []
        for mid in keys:
            eq = est[mid]["qty"] if mid in est else None
            aq = act[mid]["qty"] if mid in act else None
            if eq is not None and aq is not None and abs(eq - aq) < 1e-9:
                matched += 1
                continue
            delta = (aq if aq is not None else 0.0) - (eq if eq is not None else 0.0)
            variances.append(
                (
                    "V-" + uuid.uuid4().hex[:10],
                    batch_id,
                    mid,
                    period_id,
                    eq,
                    aq,
                    delta,
                    "pending",
                )
            )

        fp_src = (
            f"{period_id}|{matched}|{len(variances)}|{folded_count}|" + ",".join(keys)
        )
        fingerprint = hashlib.sha256(fp_src.encode()).hexdigest()[:16]
        conn.execute(
            """INSERT INTO reconcile_batches(batch_id,period_id,status,matched_count,variance_count,
               fingerprint,fold_rule,folded_count,declared_periods,created_at)
               VALUES (?,?, 'open',?,?,?,?,?,?,?)""",
            (
                batch_id, period_id, matched, len(variances), fingerprint,
                FOLD_RULE, folded_count, json.dumps(declared_periods, ensure_ascii=False),
                _now(),
            ),
        )
        if variances:
            conn.executemany(
                """INSERT INTO variances(variance_id,batch_id,meter_id,period_id,estimate_qty,actual_qty,delta,fate)
                   VALUES (?,?,?,?,?,?,?,?)""",
                variances,
            )
        conn.commit()
        return {
            "batch_id": batch_id,
            "period_id": period_id,
            "matched_count": matched,
            "variance_count": len(variances),
            "fingerprint": fingerprint,
            "fold_rule": FOLD_RULE,
            "folded_count": folded_count,
            "declared_periods": declared_periods,
            "keys": keys,
            "status": "open",
        }
    finally:
        conn.close()


def seal_period(period_id: str, batch_id: str | None = None) -> dict:
    """单账期封印（一次一封，不做多账期工单）。封印与装载写互斥串行。"""
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        period = conn.execute(
            "SELECT * FROM periods WHERE period_id=?", (period_id,)
        ).fetchone()
        if not period:
            raise HTTPException(404, "账期不存在")
        if period["status"] == "sealed":
            raise HTTPException(409, "账期已封印")

        if batch_id:
            batch = conn.execute(
                "SELECT * FROM reconcile_batches WHERE batch_id=?", (batch_id,)
            ).fetchone()
            if not batch or batch["period_id"] != period_id:
                raise HTTPException(400, "对账批与账期不匹配")
            if batch["status"] not in ("open",):
                raise HTTPException(409, "对账批不可用")
        else:
            batch = conn.execute(
                """SELECT * FROM reconcile_batches WHERE period_id=? AND status='open'
                   ORDER BY created_at DESC LIMIT 1""",
                (period_id,),
            ).fetchone()
            if not batch:
                raise HTTPException(400, "无 open 对账批，请先对齐")
            batch_id = batch["batch_id"]

        pending = conn.execute(
            "SELECT COUNT(*) AS c FROM variances WHERE batch_id=? AND fate='pending'",
            (batch_id,),
        ).fetchone()["c"]
        # base: allow seal with pending (later topics can harden)
        sealed_at = _now()
        conn.execute(
            "UPDATE periods SET status='sealed', sealed_at=?, batch_no=? WHERE period_id=?",
            (sealed_at, batch_id, period_id),
        )
        conn.execute(
            "UPDATE reconcile_batches SET status='sealed' WHERE batch_id=?",
            (batch_id,),
        )
        conn.commit()
        return {
            "period_id": period_id,
            "batch_id": batch_id,
            "sealed_at": sealed_at,
            "pending_left": pending,
            "status": "sealed",
        }
    finally:
        conn.close()


def set_fate(variance_id: str, fate: str) -> dict:
    if fate not in ("pending", "accept_short", "accept_over", "quarantine"):
        raise HTTPException(400, "非法命运")
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT v.*, p.status AS period_status FROM variances v JOIN periods p ON p.period_id=v.period_id WHERE v.variance_id=?",
            (variance_id,),
        ).fetchone()
        if not row:
            raise HTTPException(404, "差额不存在")
        if row["period_status"] == "sealed":
            raise HTTPException(409, "账期已封印，禁止改命运")
        conn.execute("UPDATE variances SET fate=? WHERE variance_id=?", (fate, variance_id))
        conn.commit()
        return {"variance_id": variance_id, "fate": fate}
    finally:
        conn.close()
