from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx


ACCESSIBLE_TYPES = ("audio/", "video/", "application/octet-stream")


@dataclass(frozen=True)
class RecordingProbe:
    conversation_id: str
    ok: bool
    status_code: int | None
    content_type: str
    content_length: str
    error: str


class ConversationAudioService:
    def __init__(self, repository: Any, *, timeout: float = 20.0):
        self.repository = repository
        self.timeout = timeout

    def probe_recordings(
        self,
        account_key: str,
        limit: int = 25,
        conversation_ids: set[str] | None = None,
    ) -> dict[str, Any]:
        records = self.repository.list_conversation_records(
            account_key,
            statuses=["recording_found", "recording_unavailable"],
            limit=limit,
        )
        if conversation_ids is not None:
            records = [record for record in records if str(record.get("conversation_id")) in conversation_ids]
        probes: list[RecordingProbe] = []
        with httpx.Client(timeout=self.timeout, follow_redirects=True) as client:
            for record in records:
                url = str(record.get("recording_url") or "").strip()
                if not url:
                    continue
                probe = self._probe_recording(client, str(record["conversation_id"]), url)
                probes.append(probe)
                next_status = "audio_accessible" if probe.ok else "recording_unavailable"
                self.repository.update_conversation_record_status(
                    account_key,
                    str(record["conversation_id"]),
                    status=next_status,
                    metadata_patch={
                        "recording_probe": {
                            "ok": probe.ok,
                            "status_code": probe.status_code,
                            "content_type": probe.content_type,
                            "content_length": probe.content_length,
                            "error": probe.error,
                        }
                    },
                )
        return {
            "checked": len(probes),
            "accessible": sum(1 for probe in probes if probe.ok),
            "unavailable": sum(1 for probe in probes if not probe.ok),
            "items": [probe.__dict__ for probe in probes],
        }

    def download_accessible_recordings(
        self,
        account_key: str,
        output_dir: Path,
        limit: int = 10,
        conversation_ids: set[str] | None = None,
    ) -> dict[str, Any]:
        output_dir.mkdir(parents=True, exist_ok=True)
        records = self.repository.list_conversation_records(
            account_key,
            statuses=["audio_accessible", "audio_download_failed", "audio_downloaded", "transcription_failed"],
            limit=limit,
        )
        if conversation_ids is not None:
            records = [record for record in records if str(record.get("conversation_id")) in conversation_ids]
        items = []
        with httpx.Client(timeout=self.timeout, follow_redirects=True) as client:
            for record in records:
                if _download_path_exists(record):
                    self.repository.update_conversation_record_status(
                        account_key,
                        str(record["conversation_id"]),
                        status="audio_downloaded",
                        metadata_patch={"recording_download": (record.get("metadata") or {}).get("recording_download") or {}},
                    )
                    continue
                url = str(record.get("recording_url") or "").strip()
                if not url:
                    continue
                conversation_id = str(record["conversation_id"])
                target_path = output_dir / _recording_filename(conversation_id, url)
                result = self._download_recording(client, url, target_path)
                status = "audio_downloaded" if result["ok"] else "audio_download_failed"
                self.repository.update_conversation_record_status(
                    account_key,
                    conversation_id,
                    status=status,
                    metadata_patch={"recording_download": result},
                )
                items.append({"conversation_id": conversation_id, **result})
        return {
            "checked": len(items),
            "downloaded": sum(1 for item in items if item["ok"]),
            "failed": sum(1 for item in items if not item["ok"]),
            "items": items,
        }

    def _probe_recording(self, client: httpx.Client, conversation_id: str, url: str) -> RecordingProbe:
        try:
            response = client.get(url, headers={"Range": "bytes=0-1023"})
            content_type = response.headers.get("content-type", "")
            content_length = response.headers.get("content-length", "")
            ok = response.status_code in {200, 206} and content_type.startswith(ACCESSIBLE_TYPES)
            return RecordingProbe(
                conversation_id=conversation_id,
                ok=ok,
                status_code=response.status_code,
                content_type=content_type,
                content_length=content_length,
                error="" if ok else response.text[:160],
            )
        except Exception as exc:
            return RecordingProbe(
                conversation_id=conversation_id,
                ok=False,
                status_code=None,
                content_type="",
                content_length="",
                error=str(exc)[:160],
            )

    def _download_recording(self, client: httpx.Client, url: str, target_path: Path) -> dict[str, Any]:
        try:
            with client.stream("GET", url) as response:
                content_type = response.headers.get("content-type", "")
                if response.status_code != 200 or not content_type.startswith(ACCESSIBLE_TYPES):
                    body = response.read()[:160]
                    return {
                        "ok": False,
                        "path": str(target_path),
                        "status_code": response.status_code,
                        "content_type": content_type,
                        "bytes": 0,
                        "error": body.decode("utf-8", errors="replace"),
                    }
                bytes_written = 0
                with target_path.open("wb") as file:
                    for chunk in response.iter_bytes():
                        if not chunk:
                            continue
                        file.write(chunk)
                        bytes_written += len(chunk)
                return {
                    "ok": True,
                    "path": str(target_path),
                    "status_code": response.status_code,
                    "content_type": content_type,
                    "bytes": bytes_written,
                    "error": "",
                }
        except Exception as exc:
            return {
                "ok": False,
                "path": str(target_path),
                "status_code": None,
                "content_type": "",
                "bytes": 0,
                "error": str(exc)[:160],
            }


def _recording_filename(conversation_id: str, url: str) -> str:
    parsed = urlparse(url)
    suffix = Path(parsed.path).suffix.lower()
    if not suffix or len(suffix) > 8:
        suffix = ".audio"
    safe_id = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in conversation_id)
    return f"{safe_id}{suffix}"


def _download_path_exists(record: dict[str, Any]) -> bool:
    download = (record.get("metadata") or {}).get("recording_download") or {}
    raw_path = str(download.get("path") or "").strip()
    return bool(raw_path and Path(raw_path).is_file())
