import json
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


class GatewayError(Exception):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


class BankGateway:
    def __init__(self, base_url="http://127.0.0.1:8000", timeout=5):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def request(self, method, path, data=None):
        body = json.dumps(data).encode() if data is not None else None
        request = Request(
            self.base_url + path,
            data=body,
            method=method,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                return json.load(response)
        except HTTPError as error:
            try:
                message = json.load(error).get("error", str(error))
            except (json.JSONDecodeError, AttributeError):
                message = str(error)
            raise GatewayError(message, error.code) from error
        except URLError as error:
            raise GatewayError(f"bank unavailable: {error.reason}") from error
        except json.JSONDecodeError as error:
            raise GatewayError("bank returned invalid JSON") from error

    def get_account(self, account_id):
        return self.request("GET", f"/accounts/{quote(account_id, safe='')}")

    def get_accounts(self, user_id=None):
        path = f"/users/{quote(user_id, safe='')}/accounts" if user_id else "/accounts"
        accounts = self.request("GET", path)
        return {"success": True, "accounts": accounts}

    def get_balance(self, account_id):
        account = self.get_account(account_id)
        return {
            "success": True,
            "account_id": account["id"],
            "balance": account["balance"],
            "currency": "CNY",
            "status": account["status"],
        }

    def get_transactions(self, account_id, start_date=None, end_date=None, limit=100):
        query = urlencode({
            key: value for key, value in {
                "start_date": start_date,
                "end_date": end_date,
                "limit": limit,
            }.items() if value is not None
        })
        transactions = self.request(
            "GET", f"/accounts/{quote(account_id, safe='')}/transactions?{query}"
        )
        return {"success": True, "transactions": transactions}

    def get_payees(self, user_id=None):
        path = f"/users/{quote(user_id, safe='')}/payees" if user_id else "/payees"
        payees = self.request("GET", path)
        return {"success": True, "payees": payees}

    def prepare_transfer(self, source_account_id, target_account_id, amount, note=""):
        transfer = self.request("POST", "/transfers/prepare", {
            "from": source_account_id,
            "to": target_account_id,
            "amount": amount,
            "note": note,
        })
        return {"success": True, "transfer": transfer}

    def execute_transfer(self, transfer_id):
        result = self.request(
            "POST", f"/transfers/{quote(transfer_id, safe='')}/execute", {}
        )
        return {
            "success": True,
            "transfer": result["transfer"],
            "source_account": result["from"],
            "target_account": result["to"],
        }

    def get_transfer(self, transfer_id):
        transfer = self.request("GET", f"/transfers/{quote(transfer_id, safe='')}")
        return {"success": True, "transfer": transfer}

    def get_cards(self, user_id=None):
        path = f"/users/{quote(user_id, safe='')}/cards" if user_id else "/cards"
        cards = self.request("GET", path)
        return {"success": True, "cards": cards}

    def freeze_card(self, card_id):
        card = self.request("POST", f"/cards/{quote(card_id, safe='')}/freeze", {})
        return {"success": True, "card": card}

    def unfreeze_card(self, card_id):
        card = self.request("POST", f"/cards/{quote(card_id, safe='')}/unfreeze", {})
        return {"success": True, "card": card}

    def report_card_lost(self, card_id):
        card = self.request(
            "POST", f"/cards/{quote(card_id, safe='')}/report-lost", {}
        )
        return {"success": True, "card": card}

    def set_card_limit(self, card_id, amount):
        card = self.request(
            "PUT", f"/cards/{quote(card_id, safe='')}/limit", {"amount": amount}
        )
        return {"success": True, "card": card}

    def freeze_account(self, account_id):
        account = self.request(
            "POST", f"/accounts/{quote(account_id, safe='')}/freeze", {}
        )
        return {"success": True, "account": account}

    def get_holdings(self, account_id):
        holdings = self.request(
            "GET", f"/accounts/{quote(account_id, safe='')}/holdings"
        )
        return {"success": True, "holdings": holdings}

    def get_investment_products(self):
        products = self.request("GET", "/investments/products")
        return {"success": True, "products": products}

    def purchase_investment(self, account_id, product, amount):
        holdings = self.request(
            "POST",
            f"/accounts/{quote(account_id, safe='')}/investments",
            {"product": product, "amount": amount},
        )
        return {"success": True, "holdings": holdings}

    def redeem_investment(self, account_id, product, amount):
        holdings = self.request(
            "POST",
            f"/accounts/{quote(account_id, safe='')}/investments/redeem",
            {"product": product, "amount": amount},
        )
        return {"success": True, "holdings": holdings}

    def get_direct_debit(self, debit_id):
        debit = self.request("GET", f"/debits/{quote(debit_id, safe='')}")
        return {"success": True, "direct_debit": debit}

    def get_direct_debits(self, user_id=None):
        path = f"/users/{quote(user_id, safe='')}/debits" if user_id else "/debits"
        debits = self.request("GET", path)
        return {"success": True, "direct_debits": debits}

    def cancel_direct_debit(self, debit_id):
        debit = self.request(
            "POST", f"/debits/{quote(debit_id, safe='')}/cancel", {}
        )
        return {"success": True, "direct_debit": debit}
