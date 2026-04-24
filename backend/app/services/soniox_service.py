"""Soniox Speech-to-Text API wrapper for async file transcription.

Uses the REST API directly (not the archived Python SDK).
Supports Arabic + English code-switching with speaker diarization.
"""

import logging
import time
from pathlib import Path

import requests

from app.config import settings

logger = logging.getLogger(__name__)

SONIOX_BASE_URL = "https://api.soniox.com/v1"
DEFAULT_MODEL = "stt-async-v4"
POLL_INTERVAL = 3.0  # seconds
POLL_TIMEOUT = 600.0  # 10 minutes max


class SonioxError(Exception):
    pass


class SonioxService:
    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or settings.soniox_api_key
        if not self.api_key:
            raise SonioxError("SONIOX_API_KEY not configured")
        self.session = requests.Session()
        self.session.headers["Authorization"] = f"Bearer {self.api_key}"

    def upload_file(self, audio_path: Path) -> str:
        """Upload an audio file, return file_id."""
        logger.info(f"Uploading {audio_path.name} ({audio_path.stat().st_size / 1024 / 1024:.1f} MB)")
        with open(audio_path, "rb") as f:
            resp = self.session.post(
                f"{SONIOX_BASE_URL}/files",
                files={"file": (audio_path.name, f)},
            )
        resp.raise_for_status()
        file_id = resp.json()["id"]
        logger.info(f"Uploaded → file_id={file_id}")
        return file_id

    def create_transcription(
        self,
        file_id: str,
        language_hints: list[str] | None = None,
        enable_diarization: bool = True,
        enable_language_id: bool = True,
    ) -> str:
        """Create an async transcription job, return transcription_id."""
        payload = {
            "model": DEFAULT_MODEL,
            "file_id": file_id,
            "enable_speaker_diarization": enable_diarization,
            "enable_language_identification": enable_language_id,
        }
        if language_hints:
            payload["language_hints"] = language_hints
        resp = self.session.post(f"{SONIOX_BASE_URL}/transcriptions", json=payload)
        resp.raise_for_status()
        txn_id = resp.json()["id"]
        logger.info(f"Created transcription {txn_id}")
        return txn_id

    def wait_for_completion(self, transcription_id: str) -> dict:
        """Poll until transcription completes or errors. Returns status dict."""
        start = time.time()
        while True:
            resp = self.session.get(f"{SONIOX_BASE_URL}/transcriptions/{transcription_id}")
            resp.raise_for_status()
            data = resp.json()
            status = data["status"]

            if status == "completed":
                duration_ms = data.get("audio_duration_ms", 0)
                logger.info(f"Transcription {transcription_id} completed ({duration_ms / 1000:.0f}s audio)")
                return data

            if status == "error":
                raise SonioxError(f"Transcription failed: {data.get('error_message', 'unknown')}")

            elapsed = time.time() - start
            if elapsed > POLL_TIMEOUT:
                raise SonioxError(f"Transcription {transcription_id} timed out after {POLL_TIMEOUT}s")

            logger.debug(f"Status: {status}, elapsed {elapsed:.0f}s, polling again in {POLL_INTERVAL}s")
            time.sleep(POLL_INTERVAL)

    def get_transcript(self, transcription_id: str) -> dict:
        """Get full transcript with tokens (timestamps, speaker, language)."""
        resp = self.session.get(f"{SONIOX_BASE_URL}/transcriptions/{transcription_id}/transcript")
        resp.raise_for_status()
        return resp.json()

    def delete_file(self, file_id: str) -> None:
        """Clean up uploaded file."""
        resp = self.session.delete(f"{SONIOX_BASE_URL}/files/{file_id}")
        if resp.status_code != 204:
            logger.warning(f"Failed to delete file {file_id}: {resp.status_code}")

    def delete_transcription(self, transcription_id: str) -> None:
        """Clean up transcription."""
        resp = self.session.delete(f"{SONIOX_BASE_URL}/transcriptions/{transcription_id}")
        if resp.status_code != 204:
            logger.warning(f"Failed to delete transcription {transcription_id}: {resp.status_code}")

    def transcribe_file(
        self,
        audio_path: Path,
        language_hints: list[str] | None = None,
    ) -> dict:
        """Convenience: upload → transcribe → wait → get transcript → cleanup.

        Returns the full transcript dict with tokens.
        """
        if language_hints is None:
            language_hints = ["ar", "en"]

        file_id = self.upload_file(audio_path)
        try:
            txn_id = self.create_transcription(
                file_id=file_id,
                language_hints=language_hints,
            )
            self.wait_for_completion(txn_id)
            transcript = self.get_transcript(txn_id)
            self.delete_transcription(txn_id)
            return transcript
        finally:
            self.delete_file(file_id)
