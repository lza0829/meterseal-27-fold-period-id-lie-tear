from __future__ import annotations
from fastapi import APIRouter
from pydantic import BaseModel

from app.db import connect
from app.services import reconcile_service

router = APIRouter(tags=["reconcile"])


class AlignIn(BaseModel):
    period_id: str


class SealIn(BaseModel):
    period_id: str
    batch_id: str | None = None


class FateIn(BaseModel):
    fate: str


@router.post("/align")
def align(body: AlignIn):
    return reconcile_service.run_align(body.period_id)


@router.get("/batches")
def batches(period_id: str | None = None):
    conn = connect()
    try:
        if period_id:
            rows = conn.execute(
                "SELECT * FROM reconcile_batches WHERE period_id=? ORDER BY created_at DESC",
                (period_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM reconcile_batches ORDER BY created_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.get("/variances")
def variances(batch_id: str | None = None, period_id: str | None = None):
    conn = connect()
    try:
        if batch_id:
            rows = conn.execute(
                "SELECT * FROM variances WHERE batch_id=? ORDER BY meter_id", (batch_id,)
            ).fetchall()
        elif period_id:
            rows = conn.execute(
                "SELECT * FROM variances WHERE period_id=? ORDER BY meter_id", (period_id,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM variances ORDER BY period_id, meter_id").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.post("/variances/{variance_id}/fate")
def set_fate(variance_id: str, body: FateIn):
    return reconcile_service.set_fate(variance_id, body.fate)


@router.post("/seal")
def seal(body: SealIn):
    return reconcile_service.seal_period(body.period_id, body.batch_id)


@router.get("/periods")
def periods():
    conn = connect()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM periods ORDER BY period_id DESC").fetchall()]
    finally:
        conn.close()
