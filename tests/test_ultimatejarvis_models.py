import unittest

from ultimatejarvis.models import ModelGateway, ModelGatewayError
from ultimatejarvis.ollama_provider import OllamaProvider


class FakeProvider:
    def __init__(self, name, *, is_local, answer=None, error=None):
        self.name = name
        self.model = "test-model"
        self.is_local = is_local
        self.answer = answer
        self.error = error
        self.calls = []

    def generate(self, prompt, *, timeout_seconds, max_output_tokens):
        self.calls.append((prompt, timeout_seconds, max_output_tokens))
        if self.error:
            raise self.error
        return self.answer


class ModelGatewayTests(unittest.TestCase):
    def test_local_provider_is_tried_before_remote(self):
        remote = FakeProvider("remote", is_local=False, answer="cloud")
        local = FakeProvider("local", is_local=True, answer="local")
        result = ModelGateway([remote, local]).generate("hello")
        self.assertEqual(result.text, "local")
        self.assertTrue(result.is_local)
        self.assertEqual(remote.calls, [])

    def test_remote_is_fallback_after_local_failure(self):
        local = FakeProvider("local", is_local=True, error=RuntimeError())
        remote = FakeProvider("remote", is_local=False, answer="fallback")
        result = ModelGateway([remote, local]).generate("hello")
        self.assertEqual(result.text, "fallback")
        self.assertFalse(result.is_local)

    def test_no_provider_failure_is_summarized(self):
        gateway = ModelGateway([FakeProvider("local", is_local=True, error=OSError())])
        with self.assertRaises(ModelGatewayError):
            gateway.generate("hello")

    def test_prompt_and_output_limits(self):
        provider = FakeProvider("local", is_local=True, answer="ok")
        gateway = ModelGateway([provider], max_prompt_chars=4, max_output_tokens=8)
        with self.assertRaises(ValueError):
            gateway.generate("12345")
        gateway.generate("hey", max_output_tokens=100)
        self.assertEqual(provider.calls[0][2], 8)

    def test_empty_prompt_rejected(self):
        with self.assertRaises(ValueError):
            ModelGateway().generate("  ")

    def test_ollama_rejects_non_loopback_host(self):
        with self.assertRaises(ValueError):
            OllamaProvider("model", base_url="http://example.com:11434")

    def test_ollama_accepts_loopback_default(self):
        provider = OllamaProvider("qwen2.5:3b")
        self.assertEqual(provider.name, "ollama")
        self.assertTrue(provider.is_local)


if __name__ == "__main__":
    unittest.main()
