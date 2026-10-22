"""Optional Ollama adapter using only Python's standard library."""
from __future__ import annotations

import json
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


class OllamaProvider:
    """Call an Ollama server on the local machine; remote hosts are rejected."""

    name = "ollama"
    is_local = True

    def __init__(self, model: str, *, base_url: str = "http://127.0.0.1:11434") -> None:
        parsed = urlparse(base_url)
        if (
            parsed.scheme != "http"
            or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Ollama must use a loopback HTTP URL")
        if not model or not model.strip():
            raise ValueError("An Ollama model name is required")
        self.model = model.strip()
        self.base_url = base_url.rstrip("/")

    def generate(self, prompt: str, *, timeout_seconds: float, max_output_tokens: int) -> str:
        payload = json.dumps({
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {"num_predict": max_output_tokens},
        }).encode("utf-8")
        request = Request(
            f"{self.base_url}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=timeout_seconds) as response:
                data = json.loads(response.read().decode("utf-8"))
        except (URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            raise RuntimeError("Local Ollama request failed") from exc
        result = data.get("response")
        if not isinstance(result, str) or not result.strip():
            raise RuntimeError("Ollama returned no response text")
        return result
