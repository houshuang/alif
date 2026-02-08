import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.services.tts import (
    AUDIO_DIR,
    TTSError,
    TTSKeyMissing,
    _get_api_key,
    cache_key_for,
    filter_arabic_compatible_voices,
    generate_and_cache,
    generate_audio,
    list_voices,
    update_index,
)


SAMPLE_VOICES = [
    {
        "voice_id": "abc123",
        "name": "Sarah",
        "category": "premade",
        "labels": {"accent": "american", "language": "en"},
    },
    {
        "voice_id": "def456",
        "name": "Custom Voice",
        "category": "cloned",
        "labels": {"language": "multilingual"},
    },
    {
        "voice_id": "ghi789",
        "name": "Arabic Speaker",
        "category": "cloned",
        "labels": {"accent": "Arabic"},
    },
    {
        "voice_id": "jkl012",
        "name": "Random Clone",
        "category": "cloned",
        "labels": {"accent": "french"},
    },
]

FAKE_MP3 = b"\xff\xfb\x90\x00" + b"\x00" * 100


def _mock_async_client(method: str, mock_resp: httpx.Response):
    """Create a patched httpx.AsyncClient context manager returning mock_resp."""
    mock_client = AsyncMock()
    setattr(mock_client, method, AsyncMock(return_value=mock_resp))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    return mock_client


class TestGetApiKey:
    def test_returns_key_when_set(self, monkeypatch):
        monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key-123")
        assert _get_api_key() == "test-key-123"

    def test_raises_when_missing(self, monkeypatch):
        monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
        with pytest.raises(TTSKeyMissing):
            _get_api_key()

    def test_raises_when_empty(self, monkeypatch):
        monkeypatch.setenv("ELEVENLABS_API_KEY", "")
        with pytest.raises(TTSKeyMissing):
            _get_api_key()


class TestCacheKey:
    def test_deterministic(self):
        k1 = cache_key_for("hello", "voice1")
        k2 = cache_key_for("hello", "voice1")
        assert k1 == k2

    def test_differs_for_different_text(self):
        k1 = cache_key_for("hello", "voice1")
        k2 = cache_key_for("world", "voice1")
        assert k1 != k2

    def test_differs_for_different_voice(self):
        k1 = cache_key_for("hello", "voice1")
        k2 = cache_key_for("hello", "voice2")
        assert k1 != k2

    def test_is_hex_string(self):
        k = cache_key_for("test", "v")
        assert all(c in "0123456789abcdef" for c in k)
        assert len(k) == 64


class TestFilterArabicVoices:
    def test_includes_premade(self):
        result = filter_arabic_compatible_voices(SAMPLE_VOICES)
        names = [v["name"] for v in result]
        assert "Sarah" in names

    def test_includes_multilingual(self):
        result = filter_arabic_compatible_voices(SAMPLE_VOICES)
        names = [v["name"] for v in result]
        assert "Custom Voice" in names

    def test_includes_arabic_accent(self):
        result = filter_arabic_compatible_voices(SAMPLE_VOICES)
        names = [v["name"] for v in result]
        assert "Arabic Speaker" in names

    def test_excludes_unrelated_clone(self):
        result = filter_arabic_compatible_voices(SAMPLE_VOICES)
        names = [v["name"] for v in result]
        assert "Random Clone" not in names


class TestListVoices:
    def test_success(self):
        mock_resp = httpx.Response(
            200,
            json={"voices": SAMPLE_VOICES},
            request=httpx.Request("GET", "https://api.elevenlabs.io/v1/voices"),
        )
        with patch("app.services.tts.httpx.AsyncClient") as mock_cls:
            mock_client = _mock_async_client("get", mock_resp)
            mock_cls.return_value = mock_client

            voices = asyncio.run(list_voices(api_key="test-key"))

        assert len(voices) == 4
        mock_client.get.assert_called_once()
        call_kwargs = mock_client.get.call_args
        assert call_kwargs[1]["headers"]["xi-api-key"] == "test-key"

    def test_api_error(self):
        mock_resp = httpx.Response(
            401,
            text="Unauthorized",
            request=httpx.Request("GET", "https://api.elevenlabs.io/v1/voices"),
        )
        with patch("app.services.tts.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_async_client("get", mock_resp)

            with pytest.raises(TTSError, match="401"):
                asyncio.run(list_voices(api_key="bad-key"))


class TestGenerateAudio:
    def test_success(self):
        mock_resp = httpx.Response(
            200,
            content=FAKE_MP3,
            request=httpx.Request("POST", "https://api.elevenlabs.io/v1/text-to-speech/voice1"),
        )
        with patch("app.services.tts.httpx.AsyncClient") as mock_cls:
            mock_client = _mock_async_client("post", mock_resp)
            mock_cls.return_value = mock_client

            audio = asyncio.run(generate_audio("مرحبا", "voice1", api_key="test-key"))

        assert audio == FAKE_MP3
        call_kwargs = mock_client.post.call_args
        body = call_kwargs[1]["json"]
        assert body["text"] == "مرحبا"
        assert body["model_id"] == "eleven_turbo_v2_5"
        assert body["voice_settings"]["stability"] == 0.5

    def test_rate_limit(self):
        mock_resp = httpx.Response(
            429,
            text="Rate limit exceeded",
            request=httpx.Request("POST", "https://api.elevenlabs.io/v1/text-to-speech/voice1"),
        )
        with patch("app.services.tts.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_async_client("post", mock_resp)

            with pytest.raises(TTSError, match="rate limit"):
                asyncio.run(generate_audio("مرحبا", "voice1", api_key="test-key"))

    def test_api_error(self):
        mock_resp = httpx.Response(
            500,
            text="Internal error",
            request=httpx.Request("POST", "https://api.elevenlabs.io/v1/text-to-speech/voice1"),
        )
        with patch("app.services.tts.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_async_client("post", mock_resp)

            with pytest.raises(TTSError, match="500"):
                asyncio.run(generate_audio("مرحبا", "voice1", api_key="test-key"))


class TestGenerateAndCache:
    def test_caches_new_audio(self, tmp_path, monkeypatch):
        monkeypatch.setattr("app.services.tts.AUDIO_DIR", tmp_path)

        mock_resp = httpx.Response(
            200,
            content=FAKE_MP3,
            request=httpx.Request("POST", "https://api.elevenlabs.io/v1/text-to-speech/voice1"),
        )
        with patch("app.services.tts.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_async_client("post", mock_resp)

            path = asyncio.run(generate_and_cache("مرحبا", "voice1", api_key="test-key"))

        assert path.exists()
        assert path.read_bytes() == FAKE_MP3
        assert path.suffix == ".mp3"

    def test_returns_cached_without_api_call(self, tmp_path, monkeypatch):
        monkeypatch.setattr("app.services.tts.AUDIO_DIR", tmp_path)

        key = cache_key_for("مرحبا", "voice1")
        cached_file = tmp_path / f"{key}.mp3"
        cached_file.write_bytes(FAKE_MP3)

        with patch("app.services.tts.generate_audio") as mock_gen:
            path = asyncio.run(generate_and_cache("مرحبا", "voice1", api_key="test-key"))

        mock_gen.assert_not_called()
        assert path == cached_file


class TestUpdateIndex:
    def test_creates_index(self, tmp_path, monkeypatch):
        monkeypatch.setattr("app.services.tts.AUDIO_DIR", tmp_path)
        monkeypatch.setattr("app.services.tts.INDEX_FILE", tmp_path / "index.json")

        update_index(42, "abc123.mp3")

        index = json.loads((tmp_path / "index.json").read_text())
        assert index["42"] == "abc123.mp3"

    def test_appends_to_existing(self, tmp_path, monkeypatch):
        index_file = tmp_path / "index.json"
        index_file.write_text(json.dumps({"1": "old.mp3"}))
        monkeypatch.setattr("app.services.tts.AUDIO_DIR", tmp_path)
        monkeypatch.setattr("app.services.tts.INDEX_FILE", index_file)

        update_index(2, "new.mp3")

        index = json.loads(index_file.read_text())
        assert index["1"] == "old.mp3"
        assert index["2"] == "new.mp3"
