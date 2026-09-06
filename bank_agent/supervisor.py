ROUTE_TOOL = [{
    "type": "function",
    "function": {
        "name": "route_to_agent",
        "description": "选择最适合处理用户请求的专业 Agent",
        "parameters": {
            "type": "object",
            "properties": {
                "agent": {
                    "type": "string",
                    "enum": ["account", "operations", "investment", "general"],
                },
                "reason": {"type": "string"},
            },
            "required": ["agent", "reason"],
            "additionalProperties": False,
        },
    },
}]


SPECIALISTS = {
    "account": {
        "prompt": "你是账户与账单分析 Agent，只处理账户、余额、流水、消费分析和异常交易。",
        "tools": {
            "get_accounts", "get_balance", "get_transactions",
            "analyze_spending", "detect_unusual_transactions",
        },
    },
    "operations": {
        "prompt": "你是银行业务 Agent，处理收款人、转账、银行卡和代扣业务。",
        "tools": {
            "find_payee", "get_transfer", "get_cards", "get_direct_debits",
            "request_transfer", "request_freeze_card", "request_report_card_lost",
            "request_set_card_limit", "request_cancel_direct_debit",
        },
    },
    "investment": {
        "prompt": "你是理财 Agent，只处理理财产品、持仓、申购和赎回。不得承诺收益。",
        "tools": {
            "get_investment_products", "get_investment_positions",
            "request_purchase_investment", "request_redeem_investment",
        },
    },
    "general": {
        "prompt": "你是银行服务总览 Agent。回答能力介绍或跨领域问题，必要时使用可用工具。",
        "tools": None,
    },
}


class Supervisor:
    def __init__(self, model):
        self.model = model

    def route(self, message):
        prompt = (
            "你是 Supervisor。只调用 route_to_agent，不处理银行业务。"
            "账户余额/流水选 account；转账/卡/代扣选 operations；"
            "理财选 investment；能力介绍、闲聊或跨领域选 general。"
        )
        reply = self.model.respond([
            {"role": "system", "content": prompt},
            {"role": "user", "content": message},
        ], ROUTE_TOOL)
        calls = reply.get("tool_calls") or []
        if calls and calls[0].get("name") == "route_to_agent":
            arguments = calls[0].get("arguments", {})
            agent = arguments.get("agent")
            if agent in SPECIALISTS:
                return agent, arguments.get("reason", "")
        return "general", "Supervisor 未返回有效路由，安全回退到 general"
