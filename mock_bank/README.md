# Mock Bank

一个可持久化的 mock bank API。

## 启动

需要 Python 3.9 或更高版本。

不需要安装任何依赖，也不需要 `requirements.txt`。

```bash
python3 app.py
```

服务地址：`http://127.0.0.1:8000`

测试是否启动成功：

```bash
curl http://127.0.0.1:8000/accounts/A1001
```

首次启动会创建 `bank.db`，并生成两个账户 `A1001`（1000.00）和 `A1002`（500.00），以及代扣协议 `D1001`。重启服务会继续使用原数据库。

## API

```text
GET  /accounts/A1001
GET  /accounts/A1001/transactions
GET  /accounts/A1001/holdings
GET  /debits/D1001

POST /transfers
     {"from":"A1001","to":"A1002","amount":"100.00","note":"测试"}

POST /accounts/A1001/freeze
     {}

POST /accounts/A1001/investments
     {"product":"稳健理财","amount":"200.00"}

POST /debits/D1001/cancel
     {}
```

金额通过字符串传入并精确到分。转账、理财和流水写入处于同一个 SQLite 事务中，失败时不会留下部分更新。

运行测试：

```bash
python3 -m unittest -v
```
