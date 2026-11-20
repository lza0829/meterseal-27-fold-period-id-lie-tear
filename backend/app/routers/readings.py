from __future__ import annotations
from fastapi import APIRouter
from pydantic import BaseModel, Field
from app.services import period_receipt

from app.db import connect
from app.services import ingest_service

router = APIRouter(tags=["readings"])


class EstimateIn(BaseModel):
    meter_id: str
    period_id: str
    qty: float
    doc_no: str = Field(min_length=1)


class ActualIn(BaseModel):
    meter_id: str
    period_id: str
    qty: float
    occurred_at: str
    reader_code: str = "R00"
    doc_no: str = Field(min_length=1)


@router.get("/estimates")
def list_estimates(period_id: str | None = None):
    conn = connect()
    try:
        if period_id:
            rows = conn.execute(
                "SELECT * FROM estimates WHERE period_id=? ORDER BY meter_id", (period_id,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM estimates ORDER BY period_id, meter_id").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.post("/estimates")
def create_estimate(body: EstimateIn):
    return ingest_service.load_estimate(
        meter_id=body.meter_id, period_id=body.period_id, qty=body.qty, doc_no=body.doc_no
    )


@router.get("/actuals")
def list_actuals(period_id: str | None = None):
    conn = connect()
    try:
        if period_id:
            rows = conn.execute(
                "SELECT * FROM actuals WHERE period_id=? ORDER BY meter_id", (period_id,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM actuals ORDER BY period_id, meter_id").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.post("/actuals")
def create_actual(body: ActualIn):
    return ingest_service.load_actual(
        meter_id=body.meter_id,
        period_id=body.period_id,
        qty=body.qty,
        occurred_at=body.occurred_at,
        reader_code=body.reader_code,
        doc_no=body.doc_no,
    )


@router.get("/receipts")
def list_receipts(
    period_id: str | None = None,
    declared_period_id: str | None = None,
    kind: str | None = None,
    decision: str | None = None,
):
    """装载回执（含拒绝记录）：落期口径可核对的审计面。"""
    where, args = [], []
    if period_id:
        where.append("landing_period_id=?")
        args.append(period_id)
    if declared_period_id:
        where.append("declared_period_id=?")
        args.append(declared_period_id)
    if kind:
        where.append("kind=?")
        args.append(kind)
    if decision:
        where.append("decision=?")
        args.append(decision)
    sql = "SELECT * FROM ingest_receipts"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY created_at DESC, receipt_id DESC LIMIT 200"
    conn = connect()
    try:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]
    finally:
        conn.close()


@router.get("/period-receipt")
def last_period_receipt():
    conn = connect()
    try:
        return period_receipt.last_receipt(conn)
    finally:
        conn.close()
