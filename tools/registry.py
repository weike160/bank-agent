class ToolRegistry:
    """JSON Schema tool registry with trusted runtime identity injection."""

    def __init__(self):
        self.tools = {}

    def register(self, name, description, properties, required, handler):
        self.tools[name] = {
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
            "handler": handler,
        }

    def definitions(self):
        return [{
            "type": "function",
            "function": {
                "name": name,
                "description": tool["description"],
                "parameters": tool["parameters"],
            },
        } for name, tool in self.tools.items()]

    def call(self, name, arguments, user_id, session_id):
        if name not in self.tools:
            return {"success": False, "data": None, "error": "unknown tool"}
        if not user_id or not session_id:
            return {"success": False, "data": None, "error": "runtime identity is required"}
        try:
            self.validate(arguments, self.tools[name]["parameters"])
            return self.tools[name]["handler"](arguments, user_id, session_id)
        except (TypeError, ValueError) as error:
            return {"success": False, "data": None, "error": str(error)}

    @staticmethod
    def validate(arguments, schema):
        if not isinstance(arguments, dict):
            raise ValueError("tool arguments must be an object")
        missing = [name for name in schema["required"] if name not in arguments]
        if missing:
            raise ValueError("missing fields: " + ", ".join(missing))
        unknown = set(arguments) - set(schema["properties"])
        if unknown:
            raise ValueError("unknown fields: " + ", ".join(sorted(unknown)))
        python_types = {"string": str, "integer": int, "number": (int, float)}
        for name, value in arguments.items():
            field = schema["properties"][name]
            expected = python_types.get(field.get("type"))
            if expected and not isinstance(value, expected):
                raise ValueError(f"{name} has invalid type")
            if "minimum" in field and value < field["minimum"]:
                raise ValueError(f"{name} is below minimum")
            if "maximum" in field and value > field["maximum"]:
                raise ValueError(f"{name} is above maximum")


def create_banking_tool_registry(banking_tools):
    registry = ToolRegistry()
    string = {"type": "string"}
    integer = {"type": "integer", "minimum": 1, "maximum": 500}

    def add(name, description, properties, required, handler):
        registry.register(name, description, properties, required, handler)

    add("get_accounts", "查询当前用户的银行账户", {}, [],
        lambda args, user, session: banking_tools.get_accounts(user))
    add("get_balance", "查询指定账户余额", {"account_id": string}, ["account_id"],
        lambda args, user, session: banking_tools.get_balance(user, args["account_id"]))
    add("get_transactions", "查询账户交易流水", {
        "account_id": string, "start_date": string, "end_date": string, "limit": integer,
    }, ["account_id"], lambda args, user, session: banking_tools.get_transactions(
        user, args["account_id"], args.get("start_date"), args.get("end_date"), args.get("limit", 100)
    ))
    add("analyze_spending", "统计账户消费金额和分类", {
        "account_id": string, "start_date": string, "end_date": string,
    }, ["account_id"], lambda args, user, session: banking_tools.analyze_spending(
        user, args["account_id"], args.get("start_date"), args.get("end_date")
    ))
    add("detect_unusual_transactions", "按金额阈值检测异常交易", {
        "account_id": string, "threshold": string,
    }, ["account_id"], lambda args, user, session: banking_tools.detect_unusual_transactions(
        user, args["account_id"], args.get("threshold", "1000.00")
    ))
    add("find_payee", "按姓名、手机号或别名查找当前用户的收款人", {"query": string}, ["query"],
        lambda args, user, session: banking_tools.find_payee(user, args["query"]))
    add("get_transfer", "查询转账状态", {"transfer_id": string}, ["transfer_id"],
        lambda args, user, session: banking_tools.get_transfer(user, args["transfer_id"]))
    add("get_cards", "查询当前用户的银行卡", {}, [],
        lambda args, user, session: banking_tools.get_cards(user))
    add("get_investment_products", "查询可购买的理财产品", {}, [],
        lambda args, user, session: banking_tools.get_investment_products())
    add("get_investment_positions", "查询账户理财持仓", {"account_id": string}, ["account_id"],
        lambda args, user, session: banking_tools.get_investment_positions(user, args["account_id"]))
    add("get_direct_debits", "查询当前用户的代扣协议", {}, [],
        lambda args, user, session: banking_tools.get_direct_debits(user))

    transfer = {
        "source_account_id": string,
        "target_account_id": string,
        "amount": string,
        "note": string,
    }
    add("request_transfer", "申请转账；系统会根据金额要求确认或强验证", transfer,
        ["source_account_id", "target_account_id", "amount"],
        lambda args, user, session: banking_tools.request_transfer(
            user, session, args["source_account_id"], args["target_account_id"],
            args["amount"], args.get("note", "")
        ))
    add("request_freeze_card", "申请冻结银行卡", {"card_id": string}, ["card_id"],
        lambda args, user, session: banking_tools.request_freeze_card(user, session, args["card_id"]))
    add("request_report_card_lost", "申请挂失银行卡；需要强验证", {"card_id": string}, ["card_id"],
        lambda args, user, session: banking_tools.request_report_card_lost(user, session, args["card_id"]))
    add("request_set_card_limit", "申请调整银行卡额度", {"card_id": string, "amount": string},
        ["card_id", "amount"], lambda args, user, session: banking_tools.request_set_card_limit(
            user, session, args["card_id"], args["amount"]
        ))
    investment = {"account_id": string, "product": string, "amount": string}
    add("request_purchase_investment", "申请购买理财产品；需要强验证", investment,
        ["account_id", "product", "amount"], lambda args, user, session:
        banking_tools.request_purchase_investment(
            user, session, args["account_id"], args["product"], args["amount"]
        ))
    add("request_redeem_investment", "申请赎回理财产品；需要强验证", investment,
        ["account_id", "product", "amount"], lambda args, user, session:
        banking_tools.request_redeem_investment(
            user, session, args["account_id"], args["product"], args["amount"]
        ))
    add("request_cancel_direct_debit", "申请取消代扣协议", {"debit_id": string}, ["debit_id"],
        lambda args, user, session: banking_tools.request_cancel_direct_debit(
            user, session, args["debit_id"]
        ))
    return registry
