import json
import unittest

import httpx

from bank_agent.model import OpenAICompatibleModel


class OpenAICompatibleModelTest(unittest.TestCase):
    def test_retries_one_transport_failure(self):
        attempts = 0

        def handler(request):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise httpx.ConnectError("temporary TLS error", request=request)
            return httpx.Response(200, json={
                "choices": [{"message": {"content": "ok"}}],
            })

        client = httpx.Client(transport=httpx.MockTransport(handler))
        model = OpenAICompatibleModel("secret", "test", "https://example.com/v1", client=client)
        self.assertEqual("ok", model.respond([], [])["content"])
        self.assertEqual(2, attempts)

    def test_http_error_does_not_expose_response_or_key(self):
        client = httpx.Client(transport=httpx.MockTransport(
            lambda request: httpx.Response(401, text="sensitive provider response")
        ))
        model = OpenAICompatibleModel("secret-key", "test", "https://example.com/v1", client=client)
        with self.assertRaisesRegex(RuntimeError, "HTTP 401") as raised:
            model.respond([], [])
        self.assertNotIn("secret", str(raised.exception))
        self.assertNotIn("sensitive", str(raised.exception))

    def test_omits_empty_tools(self):
        captured = {}

        def handler(request):
            captured.update(json.loads(request.content))
            return httpx.Response(200, json={
                "choices": [{"message": {"content": "ok"}}],
            })

        client = httpx.Client(transport=httpx.MockTransport(handler))
        model = OpenAICompatibleModel("secret", "test", "https://example.com/v1", client=client)
        model.respond([], [])
        self.assertNotIn("tools", captured)
        self.assertNotIn("tool_choice", captured)


if __name__ == "__main__":
    unittest.main()
