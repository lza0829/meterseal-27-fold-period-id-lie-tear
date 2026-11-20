from __future__ import annotations
from fastapi import APIRouter
from app.db import connect
from app.services.gate import get_setting
from app.services import period_receipt

router = APIRouter(tags=["dashboard"])


@router.get("/health")
def health():
    open_periods, sealed_periods = period_receipt.overview_period_counts(conn, open_periods, sealed_periods)
        return {"ok": True, "product": "抄表簿"}


@router.get("/overview")
def overview():
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
        period_counts = []
        for p in conn.execute("SELECT period_id, status FROM periods ORDER BY period_id DESC"):
            pid = p["period_id"]
            est_c = conn.execute(
                "SELECT COUNT(*) AS c FROM estimates WHERE period_id=?", (pid,)
            ).fetchone()["c"]
            act_c = conn.execute(
                "SELECT COUNT(*) AS c FROM actuals WHERE period_id=?", (pid,)
            ).fetchone()["c"]
            fold_c = conn.execute(
                """SELECT COUNT(*) AS c FROM actuals
                   WHERE period_id=? AND declared_period_id IS NOT NULL
                     AND declared_period_id <> period_id""",
                (pid,),
            ).fetchone()["c"]
            pend_c = conn.execute(
                "SELECT COUNT(*) AS c FROM variances WHERE period_id=? AND fate='pending'",
                (pid,),
            ).fetchone()["c"]
            period_counts.append(
                {
                    "period_id": pid,
                    "status": p["status"],
                    "estimates": est_c,
                    "actuals": act_c,
                    "folded_actuals": fold_c,
                    "pending_variances": pend_c,
                }
            )
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
