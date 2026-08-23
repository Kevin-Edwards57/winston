import tempfile
import unittest
from pathlib import Path

from unittest.mock import Mock, patch

from winston.ai import AIService, GenerationResult, OllamaProvider, ProviderError
from winston.repository import WinstonRepository


class FakeProvider:
    def __init__(self, name, *, paid=False, available=True, error=None):
        self.name = name
        self.model = f"{name}-model"
        self.paid = paid
        self._available = available
        self.error = error
        self.calls = 0

    def available(self):
        return self._available

    def generate(self, prompt, *, system="", max_tokens=400):
        self.calls += 1
        if self.error:
            raise ProviderError(self.error)
        return GenerationResult(f"answer from {self.name}", self.name, self.model, 10, 5, 1.25 if self.paid else 0)


class AIServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = WinstonRepository(Path(self.temp.name) / "ai.db")
        self.repo.initialize()

    def tearDown(self):
        self.temp.cleanup()

    def test_gemini_first_then_ollama_fallback(self):
        gemini = FakeProvider("gemini", error="quota")
        ollama = FakeProvider("ollama")
        result = AIService(self.repo, [gemini, ollama]).generate("hello", purpose="test")
        self.assertEqual(result.provider, "ollama")
        self.assertEqual(gemini.calls, 1)
        self.assertEqual(ollama.calls, 1)
        summary = self.repo.provider_summary()
        self.assertEqual(summary["gemini"]["successes"], 0)
        self.assertEqual(summary["ollama"]["successes"], 1)

    def test_zero_cost_mode_never_calls_paid_provider(self):
        paid = FakeProvider("claude", paid=True)
        service = AIService(self.repo, [paid], zero_cost_mode=True)
        with self.assertRaises(ProviderError):
            service.generate("hello")
        self.assertEqual(paid.calls, 0)

    def test_paid_provider_requires_explicit_non_zero_cost_mode(self):
        paid = FakeProvider("claude", paid=True)
        result = AIService(self.repo, [paid], zero_cost_mode=False).generate("hello")
        self.assertEqual(result.provider, "claude")
        self.assertEqual(paid.calls, 1)
        self.assertEqual(self.repo.provider_summary()["claude"]["estimated_cost_usd"], 1.25)

    @patch("winston.ai.requests.post")
    def test_ollama_disables_thinking_and_returns_text(self, post):
        response = Mock(status_code=200)
        response.json.return_value = {"response": "Draft ready", "prompt_eval_count": 12, "eval_count": 4}
        post.return_value = response
        result = OllamaProvider(model="qwen3:8b").generate("Write a draft")
        payload = post.call_args.kwargs["json"]
        self.assertFalse(payload["think"])
        self.assertFalse(payload["stream"])
        self.assertEqual(result.text, "Draft ready")


if __name__ == "__main__":
    unittest.main()
