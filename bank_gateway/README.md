# Bank Gateway

Bank Gateway 将 Mock Bank 的 HTTP 接口转换成上层容易调用的 Python 方法。

不需要安装依赖。

先启动 Mock Bank：

```bash
cd mock_bank
python3 app.py
```

调用 Gateway：

```python
from bank_gateway.gateway import BankGateway

gateway = BankGateway("http://127.0.0.1:8000")
print(gateway.get_balance("A1001"))
```

可用方法：

```text
get_account(account_id)
get_accounts(user_id=None)
get_balance(account_id)
get_transactions(account_id, start_date=None, end_date=None, limit=100)
get_payees(user_id=None)
prepare_transfer(source_account_id, target_account_id, amount, note="")
execute_transfer(transfer_id)
get_transfer(transfer_id)
get_cards(user_id=None)
freeze_card(card_id)
unfreeze_card(card_id)
report_card_lost(card_id)
set_card_limit(card_id, amount)
freeze_account(account_id)
get_holdings(account_id)
get_investment_products()
purchase_investment(account_id, product, amount)
redeem_investment(account_id, product, amount)
get_direct_debits(user_id=None)
get_direct_debit(debit_id)
cancel_direct_debit(debit_id)
```

需要用户确认的转账使用 `prepare_transfer()` 和 `execute_transfer()`。`prepare_transfer()` 不会修改余额。

银行返回 HTTP 错误、连接失败或无效 JSON 时，Gateway 会抛出 `GatewayError`。

运行集成测试：

```bash
cd bank_gateway
python3 -m unittest -v
```
