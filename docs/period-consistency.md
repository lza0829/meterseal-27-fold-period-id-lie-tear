# 落期一致性架构决策：以正式行 period_id 为准，回执可重放（架构一）

## 背景

装载回执宣告「落入某 period_id」之后，五条链曾各自为政：

- 正式行 period_id 在 B，回执却宣称 A；
- 总览 A 期笔数加一，按期列表却在 B；
- 对齐按 A 扫不到刚写入的行（扫空无解释）；
- 已封印的 A 仍被回执宣称写入成功；
- 并发两笔折期时，半截行停在错误期。

根源是旧涂装层（`period_receipt`，已删除）把「回执申报期」当成第二真相：
总览计数 +1、按期列表叠加、最近回执读 settings 而非正式表——即架构二残余。
本仓库全局只贯彻 **架构一**，架构二仅作对照记录。

## 决策（全局唯一）

**落期冲突听正式表。** `estimates/actuals.period_id` 是唯一真相（落期口径本身
见 [fold-rule.md](fold-rule.md)：实抄按 occurred_at 折自然月，估抄按申报账期），
其余各链全部由它推出或向它对齐：

1. **回执可重放**：`ingest_receipts.landing_period_id` 必须等于对应正式行的
   `period_id`。回执与正式行同事务写入（落湖）或独立落库（拒绝，不宣称成功）；
   历史正式行缺回执的，迁移按正式表内容重放回填（`seed._backfill_receipts`）。
2. **回执谎报即回滚**：装载事务提交前做链断言
   （`consistency.assert_load_chains_tx`：正式行/回执/账期闸同落期），
   任一不齐整笔回滚、返回 500，**禁止五链未齐却宣称装载落期成功**。
3. **对齐只扫正式真实所属期**：`run_align` 只按正式表 `period_id` 扫描；
   申报期扫空返回 400 并给出可核对解释（哪些单号折入了哪个落期，
   `consistency.explain_empty_period`）。
4. **总览与按期列表由正式表重放**：`/overview.period_counts` 与按期列表同
   `WHERE period_id=?` 口径（`consistency.replay_period_counts`），
   不读回执、不读涂装；涂装键（`load_period_receipt` / `period_list_paint` /
   `period_count_paint:*`）由迁移清除并不准再现。
5. **差额分桶承批次落期**：`variances.period_id == reconcile_batches.period_id`
   == 正式落期。
6. **封期仍宣称写入必须拒**：落期已封 → 409 + 拒绝回执，正式表零新增；
   并发折期与封印竞速由 `BEGIN IMMEDIATE` 串行化，无半截行。

## 正确性口径分叉：两架构对照

| 核对 | 架构一（本仓库实现，正式行为准） | 架构二（未采用，回执为准） |
| --- | --- | --- |
| 回执 A 正式 B | 不允许存在：回执落期=正式行 period_id 同事务写入；谎报即回滚；可由正式行重放核对（夹具 1） | 正式行必须改挂到回执申报期；改挂不了进隔离 |
| 总览跟错期 | 总览只能由正式表重放，申报期不参与计数（夹具 2） | 总览与按期列表跟回执声明期 |
| 对齐扫空 | 扫正式落期；扫空 400 并解释折入去向（夹具 3） | 按回执账期闭包扫描 |
| 封期仍宣称写入 | 409 拒绝 + 拒绝回执，正式表零新增，五链不失一致（夹具 4） | 回滚回执或改挂下一开放期 |
| 并发半截行 | 闸检与写入同事务串行化；落湖行数==landed 回执数==200 数（夹具 4.5–4.10） | 半截必须收敛到回执期 |

## 联动面（缺一即口径分叉）

装载入口（`ingest_service`，同事务 + 链断言）→ 正式表（`estimates/actuals.period_id`）
→ 装载回执（`ingest_receipts`，可重放）→ 对齐扫描（`run_align` 扫落期，扫空可解释）
→ 差额分桶（`variances.period_id` 承批次落期）→ 写闸（`ensure_period_open_tx` 事务内检）
→ 总览期计数（`replay_period_counts`）→ 按期列表（`/estimates`、`/actuals`、
`/receipts` 的 period_id 过滤）。观测面：`GET /api/consistency` 输出六类撕裂
（landed 回执失配 / 拒绝回执漏行 / 正式行缺回执 / 分桶跨期 / 链上悬空账期 /
涂装残留），全空则 `ok=true`。

## 核对

```bash
cd backend
.venv/bin/python tests/verify_period_consistency.py   # 五链一致性（本文件）
.venv/bin/python tests/verify_fold_rule.py            # 折期口径六项（fold-rule.md）
```

独立临时库 + 真实 HTTP 服务 + 真并发（24 笔折期装载与封印竞速），全绿退出码为 0。

## 明确不做

- 不采用架构二：不做「正式行改挂回执申报期」「对不齐进隔离区」。
- 不重做折期规则产品规格：落期口径（occurred_at 折月 / 申报落期 / 已封整笔拒）
  仍是 fold-rule.md 的架构B，本文件只规定「宣告与正式集合谁听谁」。
- 不做回执改写/补登界面：回执只能同事务产生或由正式表重放回填。
