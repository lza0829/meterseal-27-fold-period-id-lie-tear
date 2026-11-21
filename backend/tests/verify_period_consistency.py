"""落期一致性五链核对（架构：以正式行 period_id 为准，回执可重放）。

用法:  cd backend && .venv/bin/python tests/verify_period_consistency.py

五链：正式行 period_id / 对齐扫描账期 / 差额分桶所属期 / 总览期计数 / 按期列表，
      外加装载回执落期必须与正式行一致（回执可由正式行重放）。

夹具（任一条失败即非零退出）:
  1. 回执 A 正式 B：折期装载后回执落期 == 正式行 period_id；申报期仅审计；
     /period-receipt 重放一致；旧涂装键被迁移清除
  2. 总览跟错期：总览期计数跟随正式落期而非回执申报期；
     总览 == 按期列表 == 正式表计数
  3. 对齐扫空：申报期扫空 → 400 且解释折入去向；落期对齐成功；差额分桶承落期
  4. 封期仍宣称写入：已封落期装载 → 409 + 拒绝回执，正式表零新增，五链仍一致；
     并发折期 vs 封印无半截行
  5. 五链联动总核对：/consistency 全程 ok；正式行 ↔ landed 回执一一对应
"""
from __future__ import annotations

import json
import os
import random
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]

FAILURES: list[str] = []

PAINT_KEYS = {"load_period_receipt", "period_list_paint"}


def check(name: str, cond: bool, detail: str = "") -> None:
    mark = "PASS" if cond else "FAIL"
    line = f"[{mark}] {name}" + (f" — {detail}" if detail else "")
    print(line)
    if not cond:
        FAILURES.append(line)


class Client:
    def __init__(self, base: str):
        self.base = base

    def req(self, method: str, path: str, body: dict | None = None):
        data = json.dumps(body).encode() if body is not None else None
        r = urllib.request.Request(
            self.base + path, data=data, method=method,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(r, timeout=30) as resp:
                return resp.status, json.loads(resp.read() or b"null")
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                return e.code, json.loads(raw or b"null")
            except json.JSONDecodeError:
                return e.code, raw.decode(errors="replace")

    def get(self, path):
        return self.req("GET", path)

    def post(self, path, body):
        return self.req("POST", path, body)


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def wait_up(cli: Client, proc: subprocess.Popen, tries: int = 100) -> None:
    for _ in range(tries):
        if proc.poll() is not None:
            raise RuntimeError("服务进程提前退出")
        try:
            status, body = cli.get("/health")
            if status == 200 and body.get("ok"):
                return
        except (urllib.error.URLError, ConnectionError, OSError):
            pass
        time.sleep(0.15)
    raise RuntimeError("服务未就绪")


def actual_docs(cli: Client, period_id: str) -> dict:
    status, rows = cli.get(f"/actuals?period_id={period_id}")
    assert status == 200, rows
    return {r["doc_no"]: r for r in rows}


def prepaint_legacy(db_path: Path) -> None:
    """预置旧涂装层残留（架构二残余）：启动迁移必须将其清除收口。"""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT INTO settings VALUES('load_period_receipt', "
            "'{\"receipt_period_id\":\"2026-10\",\"stored_period_id\":\"2026-11\"}')"
        )
        conn.execute("INSERT INTO settings VALUES('period_list_paint','[]')")
        conn.execute("INSERT INTO settings VALUES('period_count_paint:2026-10','1')")
        conn.commit()
    finally:
        conn.close()


def consistency_ok(cli: Client) -> tuple[bool, dict]:
    status, cs = cli.get("/consistency")
    return status == 200 and cs.get("ok") is True, cs


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="chaincheck-")
    db_path = Path(tmp) / "check.db"
    prepaint_legacy(db_path)
    port = free_port()
    env = {**os.environ, "METERSEAL_DB": str(db_path)}
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app",
         "--host", "127.0.0.1", "--port", str(port)],
        cwd=BACKEND, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    cli = Client(f"http://127.0.0.1:{port}/api")
    try:
        wait_up(cli, proc)

        ok, cs = consistency_ok(cli)
        check("0.1 空库起步五链即一致（种子行回执已由正式表重放回填）", ok,
              json.dumps(cs.get("chains", {}), ensure_ascii=False) if not ok else "")

        # ---------- 夹具 1：回执 A 正式 B ----------
        status, body = cli.post("/actuals", {
            "meter_id": "M-W-101", "period_id": "2026-10", "qty": 7,
            "occurred_at": "2026-11-03T09:00:00", "reader_code": "R09", "doc_no": "C1",
        })
        check("1.1 折期装载成功且宣告落期=折期月",
              status == 200 and body.get("period_id") == "2026-11"
              and body.get("declared_period_id") == "2026-10",
              f"status={status} body={body}")
        nov = actual_docs(cli, "2026-11")
        oct_ = actual_docs(cli, "2026-10")
        check("1.2 正式行只在落期 2026-11，申报期 2026-10 无此行",
              "C1" in nov and "C1" not in oct_
              and nov.get("C1", {}).get("period_id") == "2026-11",
              f"2026-11有C1={'C1' in nov}, 2026-10有C1={'C1' in oct_}")
        status, rc = cli.get("/receipts?kind=actual&decision=landed")
        c1_rc = next((r for r in rc if r["doc_no"] == "C1"), None)
        check("1.3 回执落期 == 正式行 period_id（回执 A 正式 B 已收口）",
              c1_rc is not None and c1_rc["landing_period_id"] == "2026-11"
              and c1_rc["declared_period_id"] == "2026-10",
              f"{c1_rc}")
        status, pr = cli.get("/period-receipt")
        check("1.4 /period-receipt 由正式行重放且两值一致",
              status == 200 and pr.get("receipt_period_id") == "2026-11"
              and pr.get("stored_period_id") == "2026-11"
              and pr.get("consistent") is True,
              f"{pr}")
        status, st = cli.get("/settings")
        check("1.5 旧涂装键被迁移清除（load_period_receipt/period_list_paint/period_count_paint:*）",
              status == 200
              and not any(k in PAINT_KEYS or k.startswith("period_count_paint:") for k in st),
              f"残留={[k for k in st if k in PAINT_KEYS or k.startswith('period_count_paint:')]}")
        ok, cs = consistency_ok(cli)
        check("1.6 折期后五链一致", ok,
              json.dumps(cs.get("chains", {}), ensure_ascii=False) if not ok else "")

        # ---------- 夹具 2：总览跟错期 ----------
        status, ov0 = cli.get("/overview")
        assert status == 200, ov0
        pc0 = {p["period_id"]: p for p in ov0["period_counts"]}
        status, body = cli.post("/actuals", {
            "meter_id": "M-W-102", "period_id": "2026-10", "qty": 3,
            "occurred_at": "2026-11-10T09:00:00", "reader_code": "R09", "doc_no": "C2",
        })
        assert status == 200, body
        status, ov1 = cli.get("/overview")
        pc1 = {p["period_id"]: p for p in ov1["period_counts"]}
        check("2.1 总览跟随正式落期：2026-11 实抄 +1",
              pc1["2026-11"]["actuals"] == pc0["2026-11"]["actuals"] + 1,
              f"前={pc0['2026-11']['actuals']} 后={pc1['2026-11']['actuals']}")
        check("2.2 总览不跟回执申报期虚增：2026-10 实抄不变、开放期数不虚增",
              pc1["2026-10"]["actuals"] == pc0["2026-10"]["actuals"]
              and ov1["open_periods"] == ov0["open_periods"]
              and ov1["sealed_periods"] == ov0["sealed_periods"],
              f"2026-10: 前={pc0['2026-10']['actuals']} 后={pc1['2026-10']['actuals']}")
        ok2, detail2 = True, []
        for pc in ov1["period_counts"]:
            pid = pc["period_id"]
            _, ests = cli.get(f"/estimates?period_id={pid}")
            _, acts = cli.get(f"/actuals?period_id={pid}")
            folded = sum(1 for a in acts
                         if a.get("declared_period_id") and a["declared_period_id"] != pid)
            if not (pc["estimates"] == len(ests) and pc["actuals"] == len(acts)
                    and pc["folded_actuals"] == folded):
                ok2 = False
                detail2.append(f"{pid}: 总览{pc} vs 列表 估{len(ests)} 实{len(acts)} 折{folded}")
        check("2.3 每个账期：总览计数 == 按期列表计数（含折期列）", ok2, "; ".join(detail2))
        status, periods = cli.get("/periods")
        check("2.4 总览期计数 == 账期表计数（无涂装 +1）",
              ov1["open_periods"] + ov1["sealed_periods"] == len(periods)
              and ov1["sealed_periods"] == sum(1 for p in periods if p["status"] == "sealed"))

        # ---------- 夹具 3：对齐扫空 ----------
        status, body = cli.post("/actuals", {
            "meter_id": "M-W-102", "period_id": "2026-09", "qty": 5,
            "occurred_at": "2026-12-05T08:00:00", "reader_code": "R09", "doc_no": "C3",
        })
        assert status == 200 and body["period_id"] == "2026-12", body
        status, body = cli.post("/align", {"period_id": "2026-09"})
        detail = body.get("detail", "") if isinstance(body, dict) else str(body)
        check("3.1 申报期扫空 → 400 且解释折入去向（落期+单号）",
              status == 400 and "2026-12" in detail and "C3" in detail,
              f"status={status} detail={detail}")
        status, b = cli.post("/align", {"period_id": "2026-12"})
        check("3.2 落期对齐成功且键集合正确",
              status == 200 and b.get("keys") == ["M-W-102"]
              and b.get("variance_count") == 1,
              f"{b}")
        status, vs = cli.get("/variances?period_id=2026-12")
        check("3.3 差额分桶所属期 == 批次落期 == 2026-12",
              status == 200 and len(vs) == 1
              and all(v["period_id"] == "2026-12" and v["batch_id"] == b["batch_id"]
                      for v in vs),
              f"{vs}")
        ok, cs = consistency_ok(cli)
        check("3.4 对齐/分桶后五链一致（分桶承落期）", ok,
              json.dumps(cs.get("chains", {}), ensure_ascii=False) if not ok else "")

        # ---------- 夹具 4：封期仍宣称写入 ----------
        cli.post("/estimates", {
            "meter_id": "M-W-201", "period_id": "2031-04", "qty": 9, "doc_no": "C4-E",
        })
        status, _ = cli.post("/align", {"period_id": "2031-04"})
        assert status == 200
        status, seal = cli.post("/seal", {"period_id": "2031-04"})
        assert status == 200, seal
        status, ov4 = cli.get("/overview")
        pc4 = {p["period_id"]: p for p in ov4["period_counts"]}["2031-04"]
        status, body = cli.post("/actuals", {
            "meter_id": "M-W-201", "period_id": "2031-05", "qty": 3,
            "occurred_at": "2031-04-15T08:00:00", "doc_no": "C4",
        })
        check("4.1 落期已封 → 409 整笔拒绝（不宣称写入成功）",
              status == 409 and not (isinstance(body, dict) and body.get("ok")),
              f"status={status} body={body}")
        apr = actual_docs(cli, "2031-04")
        status, ov5 = cli.get("/overview")
        pc5 = {p["period_id"]: p for p in ov5["period_counts"]}["2031-04"]
        check("4.2 已封期零新增：正式行/总览/列表均不变",
              "C4" not in apr and len(apr) == 0 and pc5 == pc4,
              f"2031-04实抄={list(apr)} 总览前={pc4} 后={pc5}")
        status, rc = cli.get("/receipts?decision=rejected")
        c4_rc = next((r for r in rc if r["doc_no"] == "C4"), None)
        check("4.3 拒绝回执可核对（落期/原因），且不构成五链撕裂",
              c4_rc is not None and c4_rc["landing_period_id"] == "2031-04"
              and "封印" in (c4_rc["reason"] or ""),
              f"{c4_rc}")
        ok, cs = consistency_ok(cli)
        check("4.4 拒绝后五链仍一致", ok,
              json.dumps(cs.get("chains", {}), ensure_ascii=False) if not ok else "")

        # 并发折期入湖 vs 封印：半截行不得停在错误期
        cli.post("/estimates", {
            "meter_id": "M-W-101", "period_id": "2032-04", "qty": 3, "doc_no": "C6-E",
        })
        status, _ = cli.post("/align", {"period_id": "2032-04"})
        assert status == 200
        N = 24
        results: list[tuple[str, tuple]] = []
        lock = threading.Lock()

        def ingest(i: int) -> None:
            try:
                r = cli.post("/actuals", {
                    "meter_id": f"M-W-10{1 + i % 2}", "period_id": "2032-05", "qty": 1,
                    "occurred_at": f"2032-04-{1 + i % 28:02d}T08:00:00", "doc_no": f"C6-{i}",
                })
            except Exception as e:  # 客户端异常也要入账，不得静默丢笔
                r = (-1, repr(e))
            with lock:
                results.append(("post", r))

        def sealer() -> None:
            time.sleep(random.uniform(0.02, 0.4))
            r = cli.post("/seal", {"period_id": "2032-04"})
            with lock:
                results.append(("seal", r))

        threads = [threading.Thread(target=ingest, args=(i,)) for i in range(N)]
        threads.append(threading.Thread(target=sealer))
        random.shuffle(threads)
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)

        seal_res = next(r for k, r in results if k == "seal")
        posts = [r for k, r in results if k == "post"]
        n200 = sum(1 for s, _ in posts if s == 200)
        n409 = sum(1 for s, _ in posts if s == 409)
        other = [(s, b) for s, b in posts if s not in (200, 409)]
        check("4.5 竞速结局只有 200/409，封印成功",
              seal_res[0] == 200 and n200 + n409 == N,
              f"seal={seal_res[0]} 200={n200} 409={n409} 其他={other}")
        apr6 = {d: r for d, r in actual_docs(cli, "2032-04").items()
                if d.startswith("C6-")}
        status, rc = cli.get("/receipts?kind=actual")
        rc6 = [r for r in rc if r["doc_no"].startswith("C6-")]
        landed6 = [r for r in rc6 if r["decision"] == "landed"]
        rejected6 = [r for r in rc6 if r["decision"] == "rejected"]
        check("4.6 落湖行数 == 200数 == landed回执数（并发无半截行停错期）",
              len(apr6) == n200 == len(landed6)
              and all(r["period_id"] == "2032-04" for r in apr6.values()),
              f"行={len(apr6)} 200={n200} landed={len(landed6)}")
        check("4.7 拒绝数 == 409数 == rejected回执数",
              n409 == len(rejected6) and len(rc6) == N,
              f"409={n409} rejected={len(rejected6)} 回执总数={len(rc6)}")
        status, ov6 = cli.get("/overview")
        pc6 = {p["period_id"]: p for p in ov6["period_counts"]}["2032-04"]
        check("4.8 竞速后总览 2032-04 实抄数 == 按期列表实抄行数",
              pc6["actuals"] == len(actual_docs(cli, "2032-04")),
              f"总览={pc6['actuals']} 列表={len(actual_docs(cli, '2032-04'))}")
        ok, cs = consistency_ok(cli)
        check("4.9 竞速后五链仍一致", ok,
              json.dumps(cs.get("chains", {}), ensure_ascii=False) if not ok else "")
        status, _ = cli.post("/actuals", {
            "meter_id": "M-W-101", "period_id": "2032-05", "qty": 1,
            "occurred_at": "2032-04-10T08:00:00", "doc_no": "C6-POST",
        })
        check("4.10 封印后折期写入被拒且计数不再变",
              status == 409
              and len(actual_docs(cli, "2032-04")) == pc6["actuals"],
              f"status={status}")

        # ---------- 核对 5：五链联动总核对 ----------
        ok, cs = consistency_ok(cli)
        chains = cs.get("chains", {})
        check("5.1 /consistency 终态 ok（六类撕裂全为空）", ok,
              json.dumps(chains, ensure_ascii=False) if not ok else "")
        status, ov = cli.get("/overview")
        check("5.2 总览期计数与一致性重放同源相等",
              status == 200 and ov["period_counts"] == cs.get("period_counts"),
              "" if ov.get("period_counts") == cs.get("period_counts") else "总览与重放不一致")
        check("5.3 正式行 ↔ landed 回执一一对应（含种子行重放回填）",
              chains.get("unreceipted_rows") == []
              and chains.get("landed_receipt_mismatches") == []
              and chains.get("rejected_receipt_leaks") == [],
              json.dumps({k: chains.get(k) for k in (
                  "unreceipted_rows", "landed_receipt_mismatches",
                  "rejected_receipt_leaks")}, ensure_ascii=False))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()

    print()
    if FAILURES:
        print(f"核对失败 {len(FAILURES)} 项：")
        for f in FAILURES:
            print("  " + f)
        return 1
    print("五链核对全部通过（以正式行 period_id 为准，回执可重放；五链未齐不宣称成功）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
