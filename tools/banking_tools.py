from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from actions import ActionError, ActionService, PermissionPolicy, RiskLevel
from bank_gateway import GatewayError


def require(payload, *fields):
    missing = [field for field in fields if payload.get(field) in (None, "")]
    if missing:
        raise ValueError("missing fields: " + ", ".join(missing))


def require_amount(payload):
    require(payload, "amount")
    try:
        amount = Decimal(str(payload["amount"]))
    except InvalidOperation as error:
        raise ValueError("amount must be a number") from error
    if amount <= 0 or amount.as_tuple().exponent < -2:
        raise ValueError("amount must be positive with at most 2 decimal places")


def create_bank_action_service(gateway, db_path, verifier):
    def owns_account(user_id, account_id):
        accounts = gateway.get_accounts(user_id)["accounts"]
        if not any(account["id"] == account_id for account in accounts):
            raise ValueError("account does not belong to user")

    def owns_card(user_id, card_id):
        cards = gateway.get_cards(user_id)["cards"]
        if not any(card["id"] == card_id for card in cards):
            raise ValueError("card does not belong to user")

    def owns_debit(user_id, debit_id):
        debits = gateway.get_direct_debits(user_id)["direct_debits"]
        if not any(debit["id"] == debit_id for debit in debits):
            raise ValueError("direct debit does not belong to user")

    def validate_transfer(payload):
        require(payload, "user_id", "source_account_id", "target_account_id")
        require_amount(payload)
        owns_account(payload["user_id"], payload["source_account_id"])

    def validate_card(payload):
        require(payload, "user_id", "card_id")
        owns_card(payload["user_id"], payload["card_id"])

    def validate_investment(payload):
        require(payload, "user_id", "account_id", "product")
        require_amount(payload)
        owns_account(payload["user_id"], payload["account_id"])

    def security_context(action_type, user_id, payload):
        if action_type != "TRANSFER":
            return {}
        today = datetime.now(timezone.utc).date().isoformat()
        transactions = gateway.get_transactions(
            payload["source_account_id"], start_date=today, limit=500
        )["transactions"]
        daily_total = sum(
            abs(Decimal(transaction["amount"]))
            for transaction in transactions
            if transaction["kind"] == "transfer_out"
        )
        return {"daily_transfer_total": daily_total}

    def transfer_risk(payload, context):
        total = context["daily_transfer_total"] + Decimal(str(payload["amount"]))
        return RiskLevel.YELLOW if total <= Decimal("1000") else RiskLevel.RED

    def card_limit_risk(payload, context):
        return RiskLevel.YELLOW if Decimal(str(payload["amount"])) <= Decimal("5000") else RiskLevel.RED

    policy = PermissionPolicy()
    policy.register("TRANSFER", transfer_risk)
    policy.register("FREEZE_CARD", RiskLevel.YELLOW)
    policy.register("UNFREEZE_CARD", RiskLevel.YELLOW)
    policy.register("REPORT_CARD_LOST", RiskLevel.RED)
    policy.register("SET_CARD_LIMIT", card_limit_risk)
    policy.register("PURCHASE_INVESTMENT", RiskLevel.RED)
    policy.register("REDEEM_INVESTMENT", RiskLevel.RED)
    policy.register("CANCEL_DIRECT_DEBIT", RiskLevel.YELLOW)

    actions = ActionService(
        policy,
        db_path=db_path,
        verifier=verifier,
        context_provider=security_context,
    )

    def execute_transfer(payload):
        validate_transfer(payload)
        prepared = gateway.prepare_transfer(
            payload["source_account_id"],
            payload["target_account_id"],
            payload["amount"],
            payload.get("note", ""),
        )
        return gateway.execute_transfer(prepared["transfer"]["id"])

    def execute_card(payload, operation):
        validate_card(payload)
        return operation(payload["card_id"])

    def execute_card_limit(payload):
        validate_card(payload)
        require_amount(payload)
        return gateway.set_card_limit(payload["card_id"], payload["amount"])

    def execute_investment(payload, operation):
        validate_investment(payload)
        return operation(payload["account_id"], payload["product"], payload["amount"])

    def execute_cancel_debit(payload):
        owns_debit(payload["user_id"], payload["debit_id"])
        return gateway.cancel_direct_debit(payload["debit_id"])

    actions.register(
        "TRANSFER",
        execute_transfer,
        validate_transfer,
    )
    actions.register("FREEZE_CARD", lambda payload: execute_card(payload, gateway.freeze_card), validate_card)
    actions.register("UNFREEZE_CARD", lambda payload: execute_card(payload, gateway.unfreeze_card), validate_card)
    actions.register("REPORT_CARD_LOST", lambda payload: execute_card(payload, gateway.report_card_lost), validate_card)
    actions.register(
        "SET_CARD_LIMIT",
        execute_card_limit,
        lambda payload: (validate_card(payload), require_amount(payload)),
    )
    actions.register(
        "PURCHASE_INVESTMENT",
        lambda payload: execute_investment(payload, gateway.purchase_investment),
        validate_investment,
    )
    actions.register(
        "REDEEM_INVESTMENT",
        lambda payload: execute_investment(payload, gateway.redeem_investment),
        validate_investment,
    )
    actions.register(
        "CANCEL_DIRECT_DEBIT",
        execute_cancel_debit,
        lambda payload: (
            require(payload, "user_id", "debit_id"),
            owns_debit(payload["user_id"], payload["debit_id"]),
        ),
    )
    return actions


class BankingTools:
    def __init__(self, gateway, actions=None):
        self.gateway = gateway
        self.actions = actions

    @staticmethod
    def ok(data):
        return {"success": True, "data": data, "error": None}

    @staticmethod
    def fail(error):
        return {"success": False, "data": None, "error": str(error)}

    def read(self, operation):
        try:
            return self.ok(operation())
        except (ActionError, GatewayError, LookupError, ValueError) as error:
            return self.fail(error)

    def require_owned_account(self, user_id, account_id):
        accounts = self.gateway.get_accounts(user_id)["accounts"]
        if not any(account["id"] == account_id for account in accounts):
            raise ValueError("account does not belong to user")

    def get_accounts(self, user_id):
        return self.read(lambda: self.gateway.get_accounts(user_id)["accounts"])

    def get_balance(self, user_id, account_id):
        def get():
            self.require_owned_account(user_id, account_id)
            return self.gateway.get_balance(account_id)
        return self.read(get)

    def get_transactions(self, user_id, account_id, start_date=None, end_date=None, limit=100):
        def get():
            self.require_owned_account(user_id, account_id)
            return self.gateway.get_transactions(
                account_id, start_date, end_date, limit
            )["transactions"]
        return self.read(get)

    def analyze_spending(self, user_id, account_id, start_date=None, end_date=None):
        def analyze():
            self.require_owned_account(user_id, account_id)
            transactions = self.gateway.get_transactions(
                account_id, start_date, end_date, 500
            )["transactions"]
            categories = {}
            for transaction in transactions:
                if transaction["direction"] != "out" or transaction["category"] in {
                    "transfer", "investment"
                }:
                    continue
                category = transaction["category"]
                categories[category] = categories.get(category, Decimal("0")) + abs(
                    Decimal(transaction["amount"])
                )
            total = sum(categories.values(), Decimal("0"))
            return {
                "total_expense": f"{total:.2f}",
                "categories": [
                    {"category": category, "amount": f"{amount:.2f}"}
                    for category, amount in sorted(categories.items())
                ],
            }
        return self.read(analyze)

    def detect_unusual_transactions(self, user_id, account_id, threshold="1000.00"):
        def detect():
            self.require_owned_account(user_id, account_id)
            minimum = Decimal(str(threshold))
            transactions = self.gateway.get_transactions(account_id, limit=500)["transactions"]
            return [
                transaction for transaction in transactions
                if abs(Decimal(transaction["amount"])) >= minimum
            ]
        return self.read(detect)

    def find_payee(self, user_id, query):
        def find():
            normalized = query.strip().lower()
            if not normalized:
                raise ValueError("query is required")
            payees = self.gateway.get_payees(user_id)["payees"]
            return [
                payee for payee in payees
                if normalized in payee["name"].lower()
                or normalized in payee["phone"].lower()
                or normalized in payee["alias"].lower()
            ]
        return self.read(find)

    def prepare_transfer(self, user_id, source_account_id, target_account_id, amount, note=""):
        def prepare():
            self.require_owned_account(user_id, source_account_id)
            return self.gateway.prepare_transfer(
                source_account_id, target_account_id, amount, note
            )["transfer"]
        return self.read(prepare)

    def get_transfer(self, user_id, transfer_id):
        def get():
            transfer = self.gateway.get_transfer(transfer_id)["transfer"]
            self.require_owned_account(user_id, transfer["source_account_id"])
            return transfer
        return self.read(get)

    def get_cards(self, user_id):
        return self.read(lambda: self.gateway.get_cards(user_id)["cards"])

    def get_investment_products(self):
        return self.read(lambda: self.gateway.get_investment_products()["products"])

    def get_investment_positions(self, user_id, account_id):
        def get():
            self.require_owned_account(user_id, account_id)
            return self.gateway.get_holdings(account_id)["holdings"]
        return self.read(get)

    def get_direct_debits(self, user_id):
        return self.read(lambda: self.gateway.get_direct_debits(user_id)["direct_debits"])

    def request_transfer(self, user_id, session_id, source_account_id, target_account_id, amount, note="", **options):
        return self.submit("TRANSFER", {
            "user_id": user_id,
            "source_account_id": source_account_id,
            "target_account_id": target_account_id,
            "amount": amount,
            "note": note,
        }, user_id, session_id=session_id, **options)

    def request_freeze_card(self, user_id, session_id, card_id, **options):
        return self.submit("FREEZE_CARD", {"user_id": user_id, "card_id": card_id}, user_id, session_id=session_id, **options)

    def request_unfreeze_card(self, user_id, session_id, card_id, **options):
        return self.submit("UNFREEZE_CARD", {"user_id": user_id, "card_id": card_id}, user_id, session_id=session_id, **options)

    def request_report_card_lost(self, user_id, session_id, card_id, **options):
        return self.submit("REPORT_CARD_LOST", {"user_id": user_id, "card_id": card_id}, user_id, session_id=session_id, **options)

    def request_set_card_limit(self, user_id, session_id, card_id, amount, **options):
        return self.submit("SET_CARD_LIMIT", {"user_id": user_id, "card_id": card_id, "amount": amount}, user_id, session_id=session_id, **options)

    def request_purchase_investment(self, user_id, session_id, account_id, product, amount, **options):
        return self.submit("PURCHASE_INVESTMENT", {
            "user_id": user_id, "account_id": account_id, "product": product, "amount": amount,
        }, user_id, session_id=session_id, **options)

    def request_redeem_investment(self, user_id, session_id, account_id, product, amount, **options):
        return self.submit("REDEEM_INVESTMENT", {
            "user_id": user_id, "account_id": account_id, "product": product, "amount": amount,
        }, user_id, session_id=session_id, **options)

    def request_cancel_direct_debit(self, user_id, session_id, debit_id, **options):
        return self.submit("CANCEL_DIRECT_DEBIT", {"user_id": user_id, "debit_id": debit_id}, user_id, session_id=session_id, **options)

    def submit(self, action_type, payload, user_id, **options):
        if not self.actions:
            return self.fail("Action & Security is not configured")
        try:
            return self.ok(self.actions.submit(action_type, payload, user_id, **options))
        except (ActionError, GatewayError, LookupError, ValueError) as error:
            return self.fail(error)

    def confirm_action(self, action_id, user_id):
        return self.read(lambda: self.actions.confirm(action_id, user_id))

    def verify_action(self, action_id, user_id, credential):
        return self.read(lambda: self.actions.verify(action_id, user_id, credential))

    def reject_action(self, action_id, user_id):
        return self.read(lambda: self.actions.reject(action_id, user_id))

    def get_pending_action(self, user_id, session_id):
        return self.read(lambda: self.actions.get_pending_action(user_id, session_id))

    def confirm_pending_action(self, user_id, session_id):
        return self.read(lambda: self.actions.confirm_pending(user_id, session_id))

    def verify_pending_action(self, user_id, session_id, credential):
        return self.read(lambda: self.actions.verify_pending(user_id, session_id, credential))

    def request_pending_verification(self, user_id, session_id):
        return self.read(lambda: self.actions.request_pending_verification(user_id, session_id))

    def reject_pending_action(self, user_id, session_id):
        return self.read(lambda: self.actions.reject_pending(user_id, session_id))
