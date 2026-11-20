"""架构B 落期口径六项核对（端到端，独立临时库，不污染开发库）。

用法:  cd backend && .venv/bin/python tests/verify_fold_rule.py

核对的六条（任一条失败即非零退出）:
  1. occurred_at 与申报账期不同 → 落期不同（B：落期=折期月，申报仅审计）
  2. 折期后落期账期已封 → 整笔拒绝（不改挂），拒绝回执可核对
  3. 已封申报账期不能被撬写（估抄直写/实抄折回均被拒；已封期零新增）
  4. 总览按账期计数与按期列表同口径一致
  5. 对齐键集合随落期变化可对照（批次快照 fold_rule/折期笔数/原申报账期）
  6. 并发折期入湖与封印竞速：不得半截写入已封账期
"""
from __future__ import annotations

import json
import os
import random
import socket
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


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="foldcheck-")
    port = free_port()
    env = {**os.environ, "METERSEAL_DB": str(Path(tmp) / "check.db")}
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app",
         "--host", "127.0.0.1", "--port", str(port)],
        cwd=BACKEND, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    cli = Client(f"http://127.0.0.1:{port}/api")
    try:
        wait_up(cli, proc)

        # ---------- 核对 1：occurred_at ≠ 申报账期 → 落期不同 ----------
        status, body = cli.post("/actuals", {
            "meter_id": "M-W-101", "period_id": "2026-10", "qty": 7,
            "occurred_at": "2026-11-03T09:00:00", "reader_code": "R09", "doc_no": "T1",
        })
        check("1.1 折期装载回执成功", status == 200, f"status={status} body={body}")
        check("1.2 落期=折期月而非申报账期",
              status == 200 and body.get("period_id") == "2026-11"
              and body.get("declared_period_id") == "2026-10"
              and body.get("folded") is True
              and body.get("fold_rule") == "occurred_at",
              f"body={body}")
        nov = actual_docs(cli, "2026-11")
        oct_ = actual_docs(cli, "2026-10")
        check("1.3 正式表按落期归集", "T1" in nov and "T1" not in oct_,
              f"2026-11有T1={'T1' in nov}, 2026-10有T1={'T1' in oct_}")
        check("1.4 落期行内申报账期仅审计",
              nov.get("T1", {}).get("declared_period_id") == "2026-10"
              and nov.get("T1", {}).get("period_id") == "2026-11")
        status, periods = cli.get("/periods")
        p_nov = next((p for p in periods if p["period_id"] == "2026-11"), None)
        check("1.5 落期账期不存在则自动开立 open",
              p_nov is not None and p_nov["status"] == "open", f"{p_nov}")
        status, rc = cli.get("/receipts?kind=actual&decision=landed")
        t1_rc = next((r for r in rc if r["doc_no"] == "T1"), None)
        check("1.6 回执记录折期与自动开期",
              t1_rc is not None and t1_rc["landing_period_id"] == "2026-11"
              and t1_rc["declared_period_id"] == "2026-10"
              and t1_rc["period_created"] == 1, f"{t1_rc}")
        status, _ = cli.post("/actuals", {
            "meter_id": "M-W-101", "period_id": "2026-10", "qty": 1,
            "occurred_at": "not-a-date", "doc_no": "T1-BAD",
        })
        check("1.7 非法 occurred_at 拒载", status == 400, f"status={status}")

        # ---------- 核对 2：折期后落期已封 → 整笔拒绝 ----------
        status, _ = cli.post("/estimates", {
            "meter_id": "M-W-101", "period_id": "2026-09", "qty": 9, "doc_no": "T2-EST",
        })
        assert status == 200
        status, _ = cli.post("/align", {"period_id": "2026-09"})
        assert status == 200
        status, seal9 = cli.post("/seal", {"period_id": "2026-09"})
        assert status == 200, seal9
        status, body = cli.post("/actuals", {
            "meter_id": "M-W-102", "period_id": "2026-10", "qty": 3,
            "occurred_at": "2026-09-15T08:00:00", "doc_no": "T2",
        })
        check("2.1 折期落已封账期 → 整笔拒绝(409)", status == 409, f"status={status} body={body}")
        sep = actual_docs(cli, "2026-09")
        check("2.2 已封账期零写入（无半截）", "T2" not in sep and len(sep) == 0,
              f"2026-09 docs={list(sep)}")
        status, rc = cli.get("/receipts?decision=rejected")
        t2_rc = next((r for r in rc if r["doc_no"] == "T2"), None)
        check("2.3 拒绝回执可核对（落期/原因）",
              t2_rc is not None and t2_rc["landing_period_id"] == "2026-09"
              and t2_rc["decision"] == "rejected" and "封印" in (t2_rc["reason"] or ""),
              f"{t2_rc}")

        # ---------- 核对 3：已封申报账期不能被撬写 ----------
        status, _ = cli.post("/estimates", {
            "meter_id": "M-W-102", "period_id": "2026-09", "qty": 1, "doc_no": "T3-EST",
        })
        check("3.1 估抄直写已封申报账期被拒", status == 409, f"status={status}")
        status, _ = cli.post("/actuals", {
            "meter_id": "M-W-102", "period_id": "2026-09", "qty": 3,
            "occurred_at": "2026-09-20T08:00:00", "doc_no": "T3A",
        })
        check("3.2 申报即落期且已封 → 拒", status == 409, f"status={status}")
        before = actual_docs(cli, "2026-09")
        status, body = cli.post("/actuals", {
            "meter_id": "M-W-102", "period_id": "2026-09", "qty": 14,
            "occurred_at": "2026-10-05T08:00:00", "doc_no": "T3B",
        })
        after = actual_docs(cli, "2026-09")
        check("3.3 申报已封但折期落开放期：落开放期且已封期零新增",
              status == 200 and body.get("period_id") == "2026-10"
              and before == after and len(after) == 0,
              f"status={status} landing={body.get('period_id')} 2026-09 docs={list(after)}")
        status, periods = cli.get("/periods")
        p_sep = next(p for p in periods if p["period_id"] == "2026-09")
        check("3.4 已封账期状态未被撬动", p_sep["status"] == "sealed", f"{p_sep}")

        # ---------- 核对 4：总览与列表同期计数一致 ----------
        status, ov = cli.get("/overview")
        assert status == 200, ov
        ok4, detail4 = True, []
        for pc in ov["period_counts"]:
            pid = pc["period_id"]
            _, ests = cli.get(f"/estimates?period_id={pid}")
            _, acts = cli.get(f"/actuals?period_id={pid}")
            folded = sum(1 for a in acts
                         if a.get("declared_period_id") and a["declared_period_id"] != pid)
            if not (pc["estimates"] == len(ests) and pc["actuals"] == len(acts)
                    and pc["folded_actuals"] == folded):
                ok4 = False
                detail4.append(f"{pid}: 总览{pc} vs 列表 估{len(ests)} 实{len(acts)} 折{folded}")
        check("4.1 总览按账期计数 == 按期列表计数（含折期列）", ok4, "; ".join(detail4))
        status, periods = cli.get("/periods")
        check("4.2 总览期计数 == 账期表计数",
              ov["open_periods"] + ov["sealed_periods"] == len(periods)
              and ov["sealed_periods"] == sum(1 for p in periods if p["status"] == "sealed"))

        # ---------- 核对 5：对齐键集合随落期变化可对照 ----------
        cli.post("/estimates", {
            "meter_id": "M-W-201", "period_id": "2026-12", "qty": 5, "doc_no": "T5-E",
        })
        cli.post("/actuals", {
            "meter_id": "M-W-201", "period_id": "2026-12", "qty": 5,
            "occurred_at": "2026-12-10T10:00:00", "doc_no": "T5-A1",
        })
        status, b1 = cli.post("/align", {"period_id": "2026-12"})
        check("5.1 首期对齐键集合", status == 200 and b1.get("keys") == ["M-W-201"]
              and b1.get("matched_count") == 1, f"{b1}")
        status, body = cli.post("/actuals", {
            "meter_id": "M-W-201", "period_id": "2026-12", "qty": 6,
            "occurred_at": "2027-01-05T10:00:00", "doc_no": "T5-A2",
        })
        check("5.2 跨月实抄折入下一自然月", status == 200 and body.get("period_id") == "2027-01",
              f"{body}")
        status, b2 = cli.post("/align", {"period_id": "2026-12"})
        check("5.3 申报账期不撬动落期对齐（键集合/指纹不变）",
              status == 200 and b2.get("keys") == ["M-W-201"]
              and b2.get("fingerprint") == b1.get("fingerprint")
              and b2.get("folded_count") == 0,
              f"b1={b1.get('fingerprint')} b2={b2.get('fingerprint')}")
        status, b3 = cli.post("/align", {"period_id": "2027-01"})
        check("5.4 折入期键集合/快照含折期口径",
              status == 200 and b3.get("keys") == ["M-W-201"]
              and b3.get("variance_count") == 1
              and b3.get("fold_rule") == "occurred_at"
              and b3.get("folded_count") == 1
              and b3.get("declared_periods") == ["2026-12"]
              and b3.get("fingerprint") != b1.get("fingerprint"),
              f"{b3}")
        status, batches = cli.get("/batches?period_id=2027-01")
        snap = batches[0] if batches else {}
        check("5.5 批次快照持久化 fold_rule/折期笔数/原申报账期",
              snap.get("fold_rule") == "occurred_at" and snap.get("folded_count") == 1
              and json.loads(snap.get("declared_periods", "[]")) == ["2026-12"],
              f"{snap}")

        # ---------- 核对 6：并发折期入湖 vs 封印，不得半截写入 ----------
        cli.post("/estimates", {
            "meter_id": "M-W-101", "period_id": "2026-05", "qty": 3, "doc_no": "T6-E",
        })
        status, _ = cli.post("/align", {"period_id": "2026-05"})
        assert status == 200
        N = 24
        results: list[tuple[int, object]] = []
        lock = threading.Lock()

        def ingest(i: int) -> None:
            r = cli.post("/actuals", {
                "meter_id": f"M-W-10{1 + i % 2}", "period_id": "2026-06", "qty": 1,
                "occurred_at": f"2026-05-{1 + i % 28:02d}T08:00:00", "doc_no": f"T6-{i}",
            })
            with lock:
                results.append(("post", r))

        def sealer() -> None:
            time.sleep(random.uniform(0.02, 0.4))
            r = cli.post("/seal", {"period_id": "2026-05"})
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
        check("6.1 封印竞速中封印成功", seal_res[0] == 200, f"{seal_res}")
        check("6.2 并发装载只有 200/409 两种结局", n200 + n409 == N,
              f"200={n200} 409={n409} 其他={N - n200 - n409}")
        may = actual_docs(cli, "2026-05")
        t6_rows = {d: r for d, r in may.items() if d.startswith("T6-")}
        status, rc = cli.get("/receipts?kind=actual")
        t6_rc = [r for r in rc if r["doc_no"].startswith("T6-")]
        landed_rc = [r for r in t6_rc if r["decision"] == "landed"]
        rejected_rc = [r for r in t6_rc if r["decision"] == "rejected"]
        check("6.3 落湖行数 == 200回执数 == landed回执数（无半截写入）",
              len(t6_rows) == n200 == len(landed_rc),
              f"行={len(t6_rows)} 200={n200} landed回执={len(landed_rc)}")
        check("6.4 拒绝数 == 409数 == rejected回执数",
              n409 == len(rejected_rc) and n200 + n409 == N,
              f"409={n409} rejected回执={len(rejected_rc)}")
        check("6.5 每笔恰一条回执且与正式表互斥一致",
              len(t6_rc) == N
              and all((r["doc_no"] in t6_rows) == (r["decision"] == "landed") for r in t6_rc),
              f"回执={len(t6_rc)}")
        check("6.6 落湖行落期/申报账期正确（折期入 2026-05）",
              all(r["period_id"] == "2026-05" and r["declared_period_id"] == "2026-06"
                  for r in t6_rows.values()))
        status, periods = cli.get("/periods")
        p_may = next(p for p in periods if p["period_id"] == "2026-05")
        check("6.7 竞速后账期处于封印态", p_may["status"] == "sealed", f"{p_may}")
        # 封印后再来一笔折期 → 必须拒，且计数不再变
        status, _ = cli.post("/actuals", {
            "meter_id": "M-W-101", "period_id": "2026-06", "qty": 1,
            "occurred_at": "2026-05-10T08:00:00", "doc_no": "T6-POST",
        })
        check("6.8 封印后折期写入被拒", status == 409
              and len(actual_docs(cli, "2026-05")) == len(may),
              f"status={status}")
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
    print("六项核对全部通过（架构B：occurred_at 折自然月入湖，已封整笔拒绝）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
