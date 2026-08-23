"""Lock the Ollama request contract.

293 of 369 historical generation calls failed with "Ollama returned no text".
Root cause: qwen3:8b is a hybrid reasoning model. Without think disabled it spends
the entire num_predict budget inside its <think> block and returns an empty
"response" field. At the 200-token budget used for DMs it never escaped at all —
instagram_dm and facebook_dm recorded 0 successes across 120 attempts.

Reproduced directly against a local Ollama: think omitted 0/6, think=False 6/6.
"""
import unittest
from unittest.mock import MagicMock, patch

from winston.ai import THINK_DISABLED, OllamaProvider, ProviderError


class OllamaContractTests(unittest.TestCase):
    def _response(self, payload):
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = payload
        return response

    def test_request_disables_thinking(self):
        provider = OllamaProvider(model="qwen3:8b")
        with patch("winston.ai.requests.post") as post:
            post.return_value = self._response(
                {"response": "hello", "prompt_eval_count": 5, "eval_count": 3})
            provider.generate("prompt", max_tokens=200)
        sent = post.call_args.kwargs["json"]
        self.assertIn("think", sent, "the think flag must be sent explicitly")
        self.assertIs(sent["think"], False, "thinking must be disabled or response comes back empty")

    def test_think_disabled_constant_is_false(self):
        self.assertIs(THINK_DISABLED, False)

    def test_empty_response_raises_rather_than_returning_blank(self):
        """An empty completion must fail loudly so the router can fall through."""
        provider = OllamaProvider()
        with patch("winston.ai.requests.post") as post:
            post.return_value = self._response({"response": "   "})
            with self.assertRaises(ProviderError):
                provider.generate("prompt")

    def test_reasoning_only_response_is_treated_as_failure(self):
        """The exact historical failure shape: all output inside `thinking`."""
        provider = OllamaProvider(model="qwen3:8b")
        with patch("winston.ai.requests.post") as post:
            post.return_value = self._response(
                {"response": "", "thinking": "Let me consider how to write this email...",
                 "done_reason": "length"})
            with self.assertRaises(ProviderError):
                provider.generate("prompt", max_tokens=200)


if __name__ == "__main__":
    unittest.main()
