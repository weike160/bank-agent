# Mock Bank

一个可持久化的 mock bank API。

## 启动

项目统一使用 Python 3.12。先在仓库根目录运行 `uv sync --frozen`。

```bash
uv run python mock_bank/app.py
```

服务地址：`http://127.0.0.1:8000`

测试是否启动成功：

```bash
curl http://127.0.0.1:8000/accounts/A1001
```

首次启动会创建 `bank.db`，并生成两个账户 `A1001`（1000.00）和 `A1002`（500.00）、银行卡 `C1001`，以及代扣协议 `D1001`。重启服务会继续使用原数据库。

## API

```text
GET  /accounts/A1001
GET  /accounts
GET  /users/U1/accounts
GET  /users/U1/payees
GET  /users/U1/cards
GET  /users/U1/debits
GET  /accounts/A1001/transactions?start_date=2026-09-01&limit=100
GET  /accounts/A1001/holdings
GET  /debits/D1001
GET  /debits
GET  /cards
GET  /payees
GET  /transfers/{transfer_id}
GET  /investments/products

POST /transfers/prepare
     {"from":"A1001","to":"A1002","amount":"100.00","note":"测试"}

POST /transfers/{transfer_id}/execute
     {}

POST /cards/C1001/freeze
POST /cards/C1001/unfreeze
POST /cards/C1001/report-lost
     {}

PUT  /cards/C1001/limit
     {"amount":"3000.00"}

POST /accounts/A1001/freeze
     {}

POST /accounts/A1001/investments
     {"product":"稳健理财","amount":"200.00"}

POST /accounts/A1001/investments/redeem
     {"product":"稳健理财","amount":"50.00"}

POST /debits/D1001/cancel
     {}
```

金额通过字符串传入并精确到分。`prepare` 只创建待执行转账，不修改余额；`execute` 才在同一个 SQLite 事务中修改余额、生成流水并更新转账状态。重复执行同一笔转账不会重复扣款。

默认测试用户：Alice（`U1`/`A1001`）、Bob（`U2`/`A1002`）、Tujing He（`U3`/`A1003`）和 Ke Wei（`U4`/`A1004`）。Agent 使用按用户查询接口，避免跨用户读取资源。

运行测试：

```bash
python3 -m unittest -v
```
