"""落期一致性架构（全局唯一）：以正式行 period_id 为准，回执可重放。

落期冲突听谁 —— 听正式表。五条链同源，唯一真相是 estimates/actuals.period_id：

  装载入口（同事务写正式行+回执）
  → 正式表 period_id（落期，唯一真相）
  → 装载回执 landing_period_id（必须与正式行一致，可由正式行重放核对）
  → 对齐扫描 / 差额分桶（只扫正式落期，variances.period_id 承批次落期）
  → 总览期计数（由正式表重放，不读回执、不读任何涂装）
  → 按期列表（直接读正式表）

对照被否决的架构二（以回执申报期为准、正式行改挂回执账期）：本仓库不采用。
申报账期仅审计（declared_period_id），任何「回执宣称 A、正式行在 B」的撕裂
在本模块的核对下必须为空集；装载事务提交前先做链断言，五链未齐不得宣称成功。
"""
from __future__ import annotations

# 旧涂装层（架构二残余）曾用的 settings 键：迁移须清除，核对须为空
PAINT_KEYS = ("load_period_receipt", "period_list_paint")
PAINT_PREFIX = "period_count_paint:"


class ChainBreak(Exception):
    """装载事务内发现链撕裂：调用方必须回滚，禁止宣称落期成功。"""


# ---------------------------------------------------------------- 写路径断言

def assert_load_chains_tx(conn, *, kind: str, row_id: str, receipt_id: str,
                          landing: str) -> None:
    """装载事务提交前断言：正式行 / 回执 / 账期闸同落一个 period_id。

    任一链不齐 → 抛 ChainBreak，整笔回滚（五链未齐，禁止宣称装载落期成功）。
    """
    table = "actuals" if kind == "actual" else "estimates"
    key = "actual_id" if kind == "actual" else "estimate_id"
    row = conn.execute(
        f"SELECT period_id FROM {table} WHERE {key}=?", (row_id,)
    ).fetchone()
    rc = conn.execute(
        "SELECT landing_period_id, decision FROM ingest_receipts WHERE receipt_id=?",
        (receipt_id,),
    ).fetchone()
    gate = conn.execute(
        "SELECT status FROM periods WHERE period_id=?", (landing,)
    ).fetchone()
    problems = []
    if row is None or row["period_id"] != landing:
        problems.append(f"正式行落期={row and row['period_id']}")
    if rc is None or rc["decision"] != "landed" or rc["landing_period_id"] != landing:
        problems.append(f"回执={rc and (rc['decision'], rc['landing_period_id'])}")
    if gate is None or gate["status"] != "open":
        problems.append(f"账期闸={gate and gate['status']}")
    if problems:
        raise ChainBreak(f"装载链撕裂（落期 {landing}）：" + "；".join(problems))


# ---------------------------------------------------------------- 重放核对

def replay_period_counts(conn) -> list[dict]:
    """总览期计数：完全由正式表重放（估/实/其中折期/未决差额）。

    与按期列表同口径（WHERE period_id=?，period_id 即落期）；不读回执、
    不读涂装——总览只能由正式行推出。
    """
    out = []
    for p in conn.execute("SELECT period_id, status FROM periods ORDER BY period_id DESC"):
        pid = p["period_id"]
        est = conn.execute(
            "SELECT COUNT(*) AS c FROM estimates WHERE period_id=?", (pid,)
        ).fetchone()["c"]
        act = conn.execute(
            "SELECT COUNT(*) AS c FROM actuals WHERE period_id=?", (pid,)
        ).fetchone()["c"]
        folded = conn.execute(
            """SELECT COUNT(*) AS c FROM actuals
               WHERE period_id=? AND declared_period_id IS NOT NULL
                 AND declared_period_id <> period_id""",
            (pid,),
        ).fetchone()["c"]
        pend = conn.execute(
            "SELECT COUNT(*) AS c FROM variances WHERE period_id=? AND fate='pending'",
            (pid,),
        ).fetchone()["c"]
        out.append(
            {
                "period_id": pid,
                "status": p["status"],
                "estimates": est,
                "actuals": act,
                "folded_actuals": folded,
                "pending_variances": pend,
            }
        )
    return out


def landed_receipt_mismatches(conn) -> list[dict]:
    """landed 回执必须能重放到正式行：同 kind/meter/doc 且 period_id == 回执落期。"""
    bad = []
    for r in conn.execute(
        "SELECT * FROM ingest_receipts WHERE decision='landed'"
    ).fetchall():
        table = "actuals" if r["kind"] == "actual" else "estimates"
        row = conn.execute(
            f"SELECT period_id FROM {table} WHERE meter_id=? AND doc_no=?",
            (r["meter_id"], r["doc_no"]),
        ).fetchone()
        if row is None or row["period_id"] != r["landing_period_id"]:
            bad.append(
                {
                    "receipt_id": r["receipt_id"],
                    "doc_no": r["doc_no"],
                    "receipt_landing": r["landing_period_id"],
                    "formal_period": row and row["period_id"],
                }
            )
    return bad


def rejected_receipt_leaks(conn) -> list[dict]:
    """rejected 回执不得有正式行——除非同一单号后有 landed 回执解释（合法重试）。"""
    bad = []
    for r in conn.execute(
        "SELECT * FROM ingest_receipts WHERE decision='rejected'"
    ).fetchall():
        table = "actuals" if r["kind"] == "actual" else "estimates"
        row = conn.execute(
            f"SELECT 1 FROM {table} WHERE meter_id=? AND doc_no=?",
            (r["meter_id"], r["doc_no"]),
        ).fetchone()
        if row is None:
            continue
        landed = conn.execute(
            """SELECT 1 FROM ingest_receipts
               WHERE kind=? AND meter_id=? AND doc_no=? AND decision='landed'""",
            (r["kind"], r["meter_id"], r["doc_no"]),
        ).fetchone()
        if landed is None:
            bad.append(
                {
                    "receipt_id": r["receipt_id"],
                    "doc_no": r["doc_no"],
                    "reason": r["reason"],
                }
            )
    return bad


def unreceipted_rows(conn) -> list[dict]:
    """正式行必须有一一对应的 landed 回执（迁移会为历史行由正式表重放回填）。"""
    bad = []
    for kind, table, key in (("estimate", "estimates", "estimate_id"),
                             ("actual", "actuals", "actual_id")):
        for row in conn.execute(f"SELECT {key} AS id, meter_id, doc_no FROM {table}"):
            rc = conn.execute(
                """SELECT 1 FROM ingest_receipts
                   WHERE kind=? AND meter_id=? AND doc_no=? AND decision='landed'""",
                (kind, row["meter_id"], row["doc_no"]),
            ).fetchone()
            if rc is None:
                bad.append({"kind": kind, "id": row["id"], "doc_no": row["doc_no"]})
    return bad


def variance_period_breaks(conn) -> list[dict]:
    """差额分桶所属期必须承批次落期：variances.period_id == 其批次 period_id。"""
    return [
        dict(r)
        for r in conn.execute(
            """SELECT v.variance_id, v.period_id AS variance_period,
                      b.period_id AS batch_period
               FROM variances v JOIN reconcile_batches b ON b.batch_id=v.batch_id
               WHERE v.period_id <> b.period_id"""
        ).fetchall()
    ]


def orphan_chain_periods(conn) -> list[dict]:
    """批次/差额指向的账期必须存在于账期表（对齐/分桶链不悬空）。"""
    bad = []
    for table, id_col, label in (
        ("reconcile_batches", "batch_id", "batch"),
        ("variances", "variance_id", "variance"),
    ):
        sql = (
            f"SELECT t.{id_col} AS id, t.period_id FROM {table} t"
            " LEFT JOIN periods p ON p.period_id=t.period_id WHERE p.period_id IS NULL"
        )
        for r in conn.execute(sql).fetchall():
            bad.append({"chain": label, "id": r["id"], "period_id": r["period_id"]})
    return bad


def paint_residue(conn) -> list[str]:
    """旧涂装层残留键（架构二残余）必须为空。"""
    keys = []
    for r in conn.execute("SELECT key FROM settings"):
        k = r["key"]
        if k in PAINT_KEYS or k.startswith(PAINT_PREFIX):
            keys.append(k)
    return keys


def five_chain_report(conn) -> dict:
    """五链联动总核对：任何一链撕裂 ok 即为 False。"""
    chains = {
        "landed_receipt_mismatches": landed_receipt_mismatches(conn),
        "rejected_receipt_leaks": rejected_receipt_leaks(conn),
        "unreceipted_rows": unreceipted_rows(conn),
        "variance_period_breaks": variance_period_breaks(conn),
        "orphan_chain_periods": orphan_chain_periods(conn),
        "paint_residue": paint_residue(conn),
    }
    return {
        "ok": all(not v for v in chains.values()),
        "architecture": "formal_period_id",  # 以正式行 period_id 为准，回执可重放
        "chains": chains,
        "period_counts": replay_period_counts(conn),
    }


# ---------------------------------------------------------------- 扫空解释

def explain_empty_period(conn, period_id: str) -> str:
    """对齐扫空必须可解释：申报本期但按折期规则落入他期的正式行，指出去向。"""
    folded = conn.execute(
        """SELECT doc_no, period_id FROM actuals
           WHERE declared_period_id=? AND period_id<>? ORDER BY doc_no""",
        (period_id, period_id),
    ).fetchall()
    if not folded:
        return ""
    dests = sorted({r["period_id"] for r in folded})
    examples = "、".join(f"单号 {r['doc_no']} → 落期 {r['period_id']}" for r in folded[:3])
    return (
        f"；有 {len(folded)} 笔申报本期的实抄按折期规则落入他期（{examples}），"
        f"对齐请扫落期账期：{'、'.join(dests)}"
    )


def last_receipt_replay(conn) -> dict:
    """最近一条装载回执 + 由正式表重放的落期，二者必须一致（/period-receipt）。"""
    r = conn.execute(
        "SELECT * FROM ingest_receipts ORDER BY created_at DESC, receipt_id DESC LIMIT 1"
    ).fetchone()
    if r is None:
        return {}
    stored = None
    if r["decision"] == "landed":
        table = "actuals" if r["kind"] == "actual" else "estimates"
        row = conn.execute(
            f"SELECT period_id FROM {table} WHERE meter_id=? AND doc_no=?",
            (r["meter_id"], r["doc_no"]),
        ).fetchone()
        stored = row and row["period_id"]
    consistent = (
        (r["decision"] == "landed" and stored == r["landing_period_id"])
        or (r["decision"] == "rejected" and stored is None)
    )
    return {
        "receipt_id": r["receipt_id"],
        "kind": r["kind"],
        "doc_no": r["doc_no"],
        "declared_period_id": r["declared_period_id"],
        "receipt_period_id": r["landing_period_id"],  # 回执宣告的落期
        "stored_period_id": stored,                   # 由正式行重放的落期
        "decision": r["decision"],
        "consistent": consistent,
        "created_at": r["created_at"],
    }
