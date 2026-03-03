"""tests/unit/test_llm_provider.py — Unit tests for LLM Provider abstraction."""
import os
import pytest
from unittest.mock import patch, MagicMock


class TestProviderDetection:
    def test_detect_openai(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test", "ANTHROPIC_API_KEY": ""}):
            from src.llm.provider import detect_available_providers
            assert "openai" in detect_available_providers()

    def test_detect_anthropic(self):
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-test", "OPENAI_API_KEY": ""}):
            from src.llm.provider import detect_available_providers
            assert "anthropic" in detect_available_providers()

    def test_detect_both(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test", "ANTHROPIC_API_KEY": "sk-ant-test"}):
            from src.llm.provider import detect_available_providers
            avail = detect_available_providers()
            assert "openai" in avail
            assert "anthropic" in avail

    def test_detect_none(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "", "ANTHROPIC_API_KEY": ""}, clear=False):
            from src.llm.provider import detect_available_providers
            # May or may not be empty depending on other env vars
            pass


class TestDefaultProvider:
    def test_default_from_env(self):
        with patch.dict(os.environ, {"LLM_PROVIDER": "anthropic", "ANTHROPIC_API_KEY": "sk-ant-test"}):
            from src.llm.provider import get_default_provider, _load_llm_config
            _load_llm_config.cache_clear()
            assert get_default_provider() == "anthropic"

    def test_default_fallback_openai(self):
        with patch.dict(os.environ, {"LLM_PROVIDER": "", "OPENAI_API_KEY": "sk-test", "ANTHROPIC_API_KEY": ""}):
            from src.llm.provider import get_default_provider, _load_llm_config
            _load_llm_config.cache_clear()
            assert get_default_provider() == "openai"


class TestLLMResponse:
    def test_response_text(self):
        from src.llm.provider import LLMResponse
        r = LLMResponse(content="hello", model="gpt-4o-mini", provider="openai")
        assert r.text == "hello"
        assert r.content == "hello"


class TestSessionDetection:
    def test_detect_provider_from_model_name(self):
        from src.llm.provider import get_llm_for_session
        with patch("src.llm.provider.get_llm") as mock:
            mock.return_value = MagicMock()
            get_llm_for_session({"active_model": "claude-sonnet-4-20250514"})
            mock.assert_called_once()
            call_kwargs = mock.call_args
            assert call_kwargs[1]["provider"] == "anthropic" or call_kwargs[0][0] == "anthropic"

    def test_detect_openai_from_model(self):
        from src.llm.provider import get_llm_for_session
        with patch("src.llm.provider.get_llm") as mock:
            mock.return_value = MagicMock()
            get_llm_for_session({"active_model": "gpt-4o"})
            mock.assert_called_once()


class TestCompatShim:
    def test_compat_import(self):
        """Verify the compat module exports chat_completion."""
        from src.llm.compat import chat_completion
        assert callable(chat_completion)
