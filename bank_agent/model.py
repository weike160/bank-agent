import json
import time
from typing import Protocol

import httpx


class ChatModel(Protocol):
    """Small adapter boundary for any tool-calling chat model."""

    def respond(self, messages, tools):
        """Return {content: str, tool_calls: [{id, name, arguments}]}.

        The model adapter must return JSON-compatible values.
        """


class OpenAICompatibleModel:
    def __init__(
        self, api_key, model, base_url, timeout=60, system_prompt=None, client=None,
    ):
        if not api_key:
            raise ValueError("API key is required")
        self.api_key = api_key
        self.model = model
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.system_prompt = system_prompt
        self.client = client or httpx.Client(timeout=timeout)

    def respond(self, messages, tools):
        request_messages = list(messages)
        if self.system_prompt:
            request_messages.insert(0, {"role": "system", "content": self.system_prompt})
        body = {
            "model": self.model,
            "messages": request_messages,
        }
        if tools:
            body.update({"tools": tools, "tool_choice": "auto"})
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        response = self._post_with_retry(body, headers)
        try:
            payload = response.json()
        except json.JSONDecodeError as error:
            raise RuntimeError("模型服务返回了无效 JSON") from error

        message = payload["choices"][0]["message"]
        calls = []
        for call in message.get("tool_calls", []):
            function = call["function"]
            try:
                arguments = json.loads(function.get("arguments") or "{}")
            except json.JSONDecodeError as error:
                raise RuntimeError("model returned invalid tool arguments") from error
            calls.append({
                "id": call.get("id", ""),
                "name": function["name"],
                "arguments": arguments,
            })
        return {"content": message.get("content") or "", "tool_calls": calls}

    def _post_with_retry(self, body, headers):
        for attempt in range(2):
            try:
                response = self.client.post(self.url, json=body, headers=headers)
                response.raise_for_status()
                return response
            except httpx.HTTPStatusError as error:
                raise RuntimeError(
                    f"模型请求失败（HTTP {error.response.status_code}）"
                ) from error
            except httpx.TransportError as error:
                if attempt == 0:
                    time.sleep(0.3)
                    continue
                raise RuntimeError("无法连接模型服务，请检查网络后重试") from error
