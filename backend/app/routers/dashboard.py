from __future__ import annotations
from fastapi import APIRouter
from app.db import connect
from app.services import consistency
from app.services.gate import get_setting

router = APIRouter(tags=["dashboard"])


@router.get("/health")
def health():
    return {"ok": True, "product": "抄表簿"}


@router.get("/overview")
def overview():
    """总览：期计数由正式表重放（与按期列表同口径），不读回执、不读涂装。"""
    conn = connect()
    try:
        open_periods = conn.execute(
            "SELECT COUNT(*) AS c FROM periods WHERE status='open'"
        ).fetchone()["c"]
        sealed_periods = conn.execute(
            "SELECT COUNT(*) AS c FROM periods WHERE status='sealed'"
        ).fetchone()["c"]
        meters = conn.execute("SELECT COUNT(*) AS c FROM meters").fetchone()["c"]
        pending = conn.execute(
            "SELECT COUNT(*) AS c FROM variances WHERE fate='pending'"
        ).fetchone()["c"]
        current = get_setting("current_period", "")
        batches = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM reconcile_batches ORDER BY created_at DESC LIMIT 8"
            ).fetchall()
        ]
        # 按账期计数：与按期列表同口径（WHERE period_id=?，period_id 即落期账期）
        period_counts = consistency.replay_period_counts(conn)
        return {
            "current_period": current,
            "open_periods": open_periods,
            "sealed_periods": sealed_periods,
            "meter_count": meters,
            "pending_variances": pending,
            "recent_batches": batches,
            "period_counts": period_counts,
        }
    finally:
        conn.close()


@router.get("/consistency")
def consistency_report():
    """五链联动核对：正式行/回执/对齐分桶/总览/列表同落期，任一撕裂 ok=false。"""
    conn = connect()
    try:
        return consistency.five_chain_report(conn)
    finally:
        conn.close()
