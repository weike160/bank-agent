import json
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class ChatModel(Protocol):
    """Small adapter boundary for any tool-calling chat model."""

    def respond(self, messages, tools):
        """Return {content: str, tool_calls: [{id, name, arguments}]}.

        The model adapter must return JSON-compatible values.
        """


class OpenAICompatibleModel:
    def __init__(self, api_key, model, base_url, timeout=60, system_prompt=None):
        if not api_key:
            raise ValueError("API key is required")
        self.api_key = api_key
        self.model = model
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.timeout = timeout
        self.system_prompt = system_prompt

    def respond(self, messages, tools):
        request_messages = list(messages)
        if self.system_prompt:
            request_messages.insert(0, {"role": "system", "content": self.system_prompt})
        body = json.dumps({
            "model": self.model,
            "messages": request_messages,
            "tools": tools,
            "tool_choice": "auto",
        }).encode()
        request = Request(self.url, data=body, method="POST", headers={
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        })
        try:
            with urlopen(request, timeout=self.timeout) as response:
                payload = json.load(response)
        except HTTPError as error:
            raise RuntimeError(f"model request failed with HTTP {error.code}") from error
        except (URLError, json.JSONDecodeError) as error:
            raise RuntimeError("model request failed") from error

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
