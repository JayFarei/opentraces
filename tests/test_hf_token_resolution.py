"""Part B tests: HF token resolution robustness.

Mirrors pi-share-hf's 3-source token discovery:
  1. HF_TOKEN environment variable
  2. HUGGINGFACE_TOKEN environment variable
  3. ~/.cache/huggingface/token file

Surfaces a clear diagnostic message when no token is found so users
know exactly what to run (``hf auth login`` or ``opentraces login``).
"""

from __future__ import annotations

import pytest

from opentraces.publish.huggingface.upload import verify_hf_token


class TestVerifyHfToken:
    def test_hf_token_env_preferred(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setenv("HF_TOKEN", "hf_env_token_123")
        monkeypatch.delenv("HUGGINGFACE_TOKEN", raising=False)
        monkeypatch.setattr(
            "opentraces.publish.huggingface.upload._HF_CACHE_TOKEN_PATH",
            tmp_path / "nope",
        )
        token, source = verify_hf_token()
        assert token == "hf_env_token_123"
        assert "HF_TOKEN" in source

    def test_huggingface_token_env_fallback(self, monkeypatch, tmp_path) -> None:
        monkeypatch.delenv("HF_TOKEN", raising=False)
        monkeypatch.setenv("HUGGINGFACE_TOKEN", "hf_alt_456")
        monkeypatch.setattr(
            "opentraces.publish.huggingface.upload._HF_CACHE_TOKEN_PATH",
            tmp_path / "nope",
        )
        token, source = verify_hf_token()
        assert token == "hf_alt_456"
        assert "HUGGINGFACE_TOKEN" in source

    def test_cache_file_fallback(self, monkeypatch, tmp_path) -> None:
        monkeypatch.delenv("HF_TOKEN", raising=False)
        monkeypatch.delenv("HUGGINGFACE_TOKEN", raising=False)
        cache_file = tmp_path / "token"
        cache_file.write_text("hf_file_789\n")
        monkeypatch.setattr(
            "opentraces.publish.huggingface.upload._HF_CACHE_TOKEN_PATH", cache_file,
        )
        token, source = verify_hf_token()
        assert token == "hf_file_789"
        assert "cache" in source.lower() or "token" in source.lower()

    def test_no_token_returns_none_with_diagnostic(self, monkeypatch, tmp_path) -> None:
        monkeypatch.delenv("HF_TOKEN", raising=False)
        monkeypatch.delenv("HUGGINGFACE_TOKEN", raising=False)
        monkeypatch.setattr(
            "opentraces.publish.huggingface.upload._HF_CACHE_TOKEN_PATH",
            tmp_path / "missing",
        )
        token, diagnostic = verify_hf_token()
        assert token is None
        # Diagnostic must mention how to log in so users can self-recover.
        assert "login" in diagnostic.lower()

    def test_cache_file_whitespace_stripped(self, monkeypatch, tmp_path) -> None:
        monkeypatch.delenv("HF_TOKEN", raising=False)
        monkeypatch.delenv("HUGGINGFACE_TOKEN", raising=False)
        cache_file = tmp_path / "token"
        cache_file.write_text("   hf_with_ws  \n\n")
        monkeypatch.setattr(
            "opentraces.publish.huggingface.upload._HF_CACHE_TOKEN_PATH", cache_file,
        )
        token, _ = verify_hf_token()
        assert token == "hf_with_ws"

    def test_empty_cache_file_is_no_token(self, monkeypatch, tmp_path) -> None:
        monkeypatch.delenv("HF_TOKEN", raising=False)
        monkeypatch.delenv("HUGGINGFACE_TOKEN", raising=False)
        cache_file = tmp_path / "token"
        cache_file.write_text("   \n")
        monkeypatch.setattr(
            "opentraces.publish.huggingface.upload._HF_CACHE_TOKEN_PATH", cache_file,
        )
        token, _ = verify_hf_token()
        assert token is None

    def test_empty_env_ignored(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setenv("HF_TOKEN", "")
        monkeypatch.setenv("HUGGINGFACE_TOKEN", "hf_fallback")
        monkeypatch.setattr(
            "opentraces.publish.huggingface.upload._HF_CACHE_TOKEN_PATH",
            tmp_path / "nope",
        )
        token, source = verify_hf_token()
        assert token == "hf_fallback"
        assert "HUGGINGFACE_TOKEN" in source
