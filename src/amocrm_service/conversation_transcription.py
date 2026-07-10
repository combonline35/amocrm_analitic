from __future__ import annotations

import base64
import os
from pathlib import Path
from typing import Any

import httpx


OPENAI_TRANSCRIPTION_URL = "https://api.openai.com/v1/audio/transcriptions"
OPENROUTER_TRANSCRIPTION_URL = "https://openrouter.ai/api/v1/audio/transcriptions"


class TranscriptionConfigError(RuntimeError):
    pass


class OpenAITranscriptionService:
    def __init__(
        self,
        repository: Any,
        *,
        api_key: str | None = None,
        model: str | None = None,
        timeout: float = 180.0,
    ):
        self.repository = repository
        self.api_key = api_key or os.getenv("OPENAI_API_KEY", "").strip()
        self.model = model or os.getenv("OPENAI_TRANSCRIBE_MODEL", "gpt-4o-transcribe").strip()
        self.timeout = timeout

    def transcribe_downloaded(
        self,
        account_key: str,
        limit: int = 5,
        conversation_ids: set[str] | None = None,
    ) -> dict[str, Any]:
        if not self.api_key:
            raise TranscriptionConfigError("OPENAI_API_KEY is required for transcription")
        records = self.repository.list_conversation_records(
            account_key,
            statuses=["audio_accessible", "audio_downloaded", "transcription_failed"],
            limit=limit,
        )
        if conversation_ids is not None:
            records = [record for record in records if str(record.get("conversation_id")) in conversation_ids]
        items = []
        for record in records:
            conversation_id = str(record["conversation_id"])
            download = (record.get("metadata") or {}).get("recording_download") or {}
            raw_path = str(download.get("path") or "").strip()
            path = Path(raw_path) if raw_path else None
            if not path or not path.is_file():
                self.repository.update_conversation_record_status(
                    account_key,
                    conversation_id,
                    status="transcription_failed",
                    metadata_patch={"transcription": {"ok": False, "error": f"audio file not found: {raw_path or '<empty>'}"}},
                )
                items.append({"conversation_id": conversation_id, "ok": False, "error": "audio file not found"})
                continue
            result = self._transcribe_file(path)
            if result["ok"]:
                self.repository.set_conversation_transcript(
                    account_key,
                    conversation_id,
                    transcript_text=str(result["text"]),
                    status="transcribed",
                    metadata_patch={"transcription": result},
                )
            else:
                self.repository.update_conversation_record_status(
                    account_key,
                    conversation_id,
                    status="transcription_failed",
                    metadata_patch={"transcription": result},
                )
            items.append({"conversation_id": conversation_id, **result})
        return {
            "checked": len(items),
            "transcribed": sum(1 for item in items if item["ok"]),
            "failed": sum(1 for item in items if not item["ok"]),
            "items": items,
        }

    def _transcribe_file(self, path: Path) -> dict[str, Any]:
        try:
            filename = _api_filename(path)
            with path.open("rb") as audio:
                response = httpx.post(
                    OPENAI_TRANSCRIPTION_URL,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    data={
                        "model": self.model,
                        "response_format": "text",
                        "language": "ru",
                    },
                    files={"file": (filename, audio, _content_type(filename))},
                    timeout=self.timeout,
                )
            if response.status_code >= 400:
                return {
                    "ok": False,
                    "provider": "openai",
                    "model": self.model,
                    "path": str(path),
                    "status_code": response.status_code,
                    "text": "",
                    "error": response.text[:500],
                }
            text = response.text.strip()
            return {
                "ok": True,
                "provider": "openai",
                "model": self.model,
                "path": str(path),
                "status_code": response.status_code,
                "text": text,
                "text_chars": len(text),
                "error": "",
            }
        except Exception as exc:
            return {
                "ok": False,
                "provider": "openai",
                "model": self.model,
                "path": str(path),
                "status_code": None,
                "text": "",
                "error": str(exc)[:500],
            }


class OpenRouterTranscriptionService:
    def __init__(
        self,
        repository: Any,
        *,
        api_key: str | None = None,
        model: str | None = None,
        timeout: float = 180.0,
    ):
        self.repository = repository
        self.api_key = api_key or os.getenv("OPENROUTER_API_KEY", "").strip()
        self.model = model or os.getenv("OPENROUTER_TRANSCRIBE_MODEL", "openai/whisper-large-v3").strip()
        self.timeout = timeout

    def transcribe_downloaded(
        self,
        account_key: str,
        limit: int = 5,
        conversation_ids: set[str] | None = None,
    ) -> dict[str, Any]:
        if not self.api_key:
            raise TranscriptionConfigError("OPENROUTER_API_KEY is required for OpenRouter transcription")
        records = self.repository.list_conversation_records(
            account_key,
            statuses=["audio_accessible", "audio_downloaded", "transcription_failed"],
            limit=limit,
        )
        if conversation_ids is not None:
            records = [record for record in records if str(record.get("conversation_id")) in conversation_ids]
        items = []
        for record in records:
            conversation_id = str(record["conversation_id"])
            download = (record.get("metadata") or {}).get("recording_download") or {}
            raw_path = str(download.get("path") or "").strip()
            path = Path(raw_path) if raw_path else None
            if not path or not path.is_file():
                self.repository.update_conversation_record_status(
                    account_key,
                    conversation_id,
                    status="transcription_failed",
                    metadata_patch={"transcription": {"ok": False, "error": f"audio file not found: {raw_path or '<empty>'}"}},
                )
                items.append({"conversation_id": conversation_id, "ok": False, "error": "audio file not found"})
                continue
            result = self._transcribe_file(path)
            if result["ok"]:
                self.repository.set_conversation_transcript(
                    account_key,
                    conversation_id,
                    transcript_text=str(result["text"]),
                    status="transcribed",
                    metadata_patch={"transcription": result},
                )
            else:
                self.repository.update_conversation_record_status(
                    account_key,
                    conversation_id,
                    status="transcription_failed",
                    metadata_patch={"transcription": result},
                )
            items.append({"conversation_id": conversation_id, **result})
        return {
            "checked": len(items),
            "transcribed": sum(1 for item in items if item["ok"]),
            "failed": sum(1 for item in items if not item["ok"]),
            "items": items,
        }

    def _transcribe_file(self, path: Path) -> dict[str, Any]:
        try:
            filename = _api_filename(path)
            audio_b64 = base64.b64encode(path.read_bytes()).decode("ascii")
            response = httpx.post(
                OPENROUTER_TRANSCRIPTION_URL,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                    "X-OpenRouter-Title": "amoCRM Conversation Intelligence",
                },
                json={
                    "model": self.model,
                    "language": "ru",
                    "input_audio": {
                        "data": audio_b64,
                        "format": _audio_format(filename),
                    },
                },
                timeout=self.timeout,
            )
            if response.status_code >= 400:
                return {
                    "ok": False,
                    "provider": "openrouter",
                    "model": self.model,
                    "path": str(path),
                    "status_code": response.status_code,
                    "text": "",
                    "error": response.text[:500],
                }
            payload = response.json()
            text = str(payload.get("text") or "").strip()
            return {
                "ok": bool(text),
                "provider": "openrouter",
                "model": self.model,
                "path": str(path),
                "status_code": response.status_code,
                "text": text,
                "text_chars": len(text),
                "usage": payload.get("usage") or {},
                "error": "" if text else "empty transcription text",
            }
        except Exception as exc:
            return {
                "ok": False,
                "provider": "openrouter",
                "model": self.model,
                "path": str(path),
                "status_code": None,
                "text": "",
                "error": str(exc)[:500],
            }


class FallbackTranscriptionService:
    def __init__(self, repository: Any, services: list[OpenRouterTranscriptionService | OpenAITranscriptionService]):
        self.repository = repository
        self.services = services

    def transcribe_downloaded(
        self,
        account_key: str,
        limit: int = 5,
        conversation_ids: set[str] | None = None,
    ) -> dict[str, Any]:
        if not self.services:
            raise TranscriptionConfigError("OPENROUTER_API_KEY or OPENAI_API_KEY is required for transcription")
        records = self.repository.list_conversation_records(
            account_key,
            statuses=["audio_accessible", "audio_downloaded", "transcription_failed"],
            limit=limit,
        )
        if conversation_ids is not None:
            records = [record for record in records if str(record.get("conversation_id")) in conversation_ids]
        items = []
        for record in records:
            conversation_id = str(record["conversation_id"])
            download = (record.get("metadata") or {}).get("recording_download") or {}
            raw_path = str(download.get("path") or "").strip()
            path = Path(raw_path) if raw_path else None
            if not path or not path.is_file():
                result = {"ok": False, "text": "", "error": f"audio file not found: {raw_path or '<empty>'}"}
                self.repository.update_conversation_record_status(
                    account_key,
                    conversation_id,
                    status="transcription_failed",
                    metadata_patch={"transcription": result},
                )
                items.append({"conversation_id": conversation_id, **result})
                continue

            attempts = []
            result: dict[str, Any] | None = None
            for service in self.services:
                attempt = service._transcribe_file(path)
                attempts.append({key: value for key, value in attempt.items() if key != "text"})
                if attempt.get("ok"):
                    result = {**attempt, "attempts": attempts}
                    break
            if result is None:
                last = attempts[-1] if attempts else {"ok": False, "error": "no transcription service configured"}
                result = {
                    "ok": False,
                    "provider": "fallback",
                    "path": str(path),
                    "text": "",
                    "attempts": attempts,
                    "error": str(last.get("error") or "transcription failed")[:500],
                }

            if result.get("ok"):
                self.repository.set_conversation_transcript(
                    account_key,
                    conversation_id,
                    transcript_text=str(result["text"]),
                    status="transcribed",
                    metadata_patch={"transcription": result},
                )
            else:
                self.repository.update_conversation_record_status(
                    account_key,
                    conversation_id,
                    status="transcription_failed",
                    metadata_patch={"transcription": result},
                )
            items.append({"conversation_id": conversation_id, **result})
        return {
            "checked": len(items),
            "transcribed": sum(1 for item in items if item["ok"]),
            "failed": sum(1 for item in items if not item["ok"]),
            "items": items,
        }


def build_transcription_service(
    repository: Any,
) -> OpenRouterTranscriptionService | OpenAITranscriptionService | FallbackTranscriptionService:
    services: list[OpenRouterTranscriptionService | OpenAITranscriptionService] = []
    if os.getenv("OPENROUTER_API_KEY", "").strip():
        services.append(OpenRouterTranscriptionService(repository))
    if os.getenv("OPENAI_API_KEY", "").strip():
        services.append(OpenAITranscriptionService(repository))
    if len(services) == 1:
        return services[0]
    return FallbackTranscriptionService(repository, services)


def _api_filename(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".mp3", ".mp4", ".mpeg", ".mpga", ".m4a", ".wav", ".webm"}:
        return path.name
    with path.open("rb") as file:
        header = file.read(16)
    if header.startswith(b"ID3") or header[:2] in {b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"}:
        return f"{path.stem}.mp3"
    if b"ftyp" in header:
        return f"{path.stem}.m4a"
    return f"{path.stem}.mp3"


def _audio_format(filename: str) -> str:
    suffix = Path(filename).suffix.lower().lstrip(".")
    if suffix == "m4a":
        return "mp3"
    if suffix in {"mp3", "wav"}:
        return suffix
    return "mp3"


def _content_type(filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix == ".wav":
        return "audio/wav"
    if suffix in {".m4a", ".mp4"}:
        return "audio/mp4"
    if suffix == ".webm":
        return "audio/webm"
    return "audio/mpeg"
