# 抄表簿

骨架：抄表读数装载 → 估抄/实抄对齐 → 差额处置 → 账期封印。

- 前端 http://127.0.0.1:6010（勿用 6000，浏览器会拦）
- 接口 http://127.0.0.1:11000

## 落期口径（全局唯一，架构B）

实抄按 `occurred_at` 折自然月落期入湖；申报 `period_id` 仅审计。
落期账期已封印 → 整笔拒绝（不改挂）；估抄按申报账期落期。
装载/对齐/差额/封印/批次快照/总览计数/按期列表全链路同口径，详见
[docs/fold-rule.md](docs/fold-rule.md)。

六项口径核对（独立临时库，不污染开发库）：

```bash
cd backend
.venv/bin/python tests/verify_fold_rule.py
```

## 启动

```powershell
cd backend
C:\Users\18435\AppData\Roaming\uv\python\cpython-3.11.15-windows-x86_64-none\python.exe -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\uvicorn app.main:app --host 127.0.0.1 --port 11000 --app-dir .
```

```powershell
cd frontend
npm install
npm run dev -- --host 127.0.0.1 --port 6010
```
