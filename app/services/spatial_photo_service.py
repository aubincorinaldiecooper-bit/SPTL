from __future__ import annotations

import json
import logging
import math
import os
import shlex
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any, Iterable


MAX_FILE_SIZE_BYTES = 20 * 1024 * 1024
MIN_IMAGE_DIMENSION = 256
MAX_IMAGE_DIMENSION = 4096

logger = logging.getLogger(__name__)
BASE_DIR = Path(__file__).resolve().parent.parent.parent


class SpatialPhotoConversionError(RuntimeError):
    """Raised when conversion fails."""


class UploadValidationError(ValueError):
    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class RateLimitError(RuntimeError):
    def __init__(self, retry_after: int) -> None:
        super().__init__("Too many uploads")
        self.retry_after = retry_after


class RateLimiter:
    def __init__(
        self,
        per_ip_limit: int = 10,
        global_limit: int = 50,
        window_seconds: int = 60,
        now_fn: Any | None = None,
    ) -> None:
        self.per_ip_limit = per_ip_limit
        self.global_limit = global_limit
        self.window_seconds = window_seconds
        self._now_fn = now_fn or time.monotonic
        self._lock = threading.Lock()
        self._global = deque()
        self._per_ip: dict[str, deque[float]] = {}

    def check_and_record(self, ip: str) -> None:
        now = float(self._now_fn())
        with self._lock:
            self._prune(now)
            ip_key = ip or "unknown"
            ip_queue = self._per_ip.setdefault(ip_key, deque())

            retries: list[float] = []
            if len(ip_queue) >= self.per_ip_limit:
                retries.append((ip_queue[0] + self.window_seconds) - now)
            if len(self._global) >= self.global_limit:
                retries.append((self._global[0] + self.window_seconds) - now)

            if retries:
                retry_after = max(1, int(math.ceil(max(retries))))
                raise RateLimitError(retry_after=retry_after)

            ip_queue.append(now)
            self._global.append(now)

    def _prune(self, now: float) -> None:
        cutoff = now - self.window_seconds
        while self._global and self._global[0] <= cutoff:
            self._global.popleft()

        empty_ips: list[str] = []
        for ip, queue in self._per_ip.items():
            while queue and queue[0] <= cutoff:
                queue.popleft()
            if not queue:
                empty_ips.append(ip)

        for ip in empty_ips:
            self._per_ip.pop(ip, None)


@dataclass(frozen=True)
class SpatialPhotoArtifact:
    name: str
    path: Path


@dataclass(frozen=True)
class SpatialPhotoResult:
    job_id: str
    artifacts: tuple[SpatialPhotoArtifact, ...]
    metadata: dict[str, str]


class SpatialPhotoService:
    """In-process async-style job orchestration for ML Sharp spatial-photo conversion."""

    def __init__(self, ml_sharp_command: str, output_root: Path) -> None:
        if not ml_sharp_command.strip():
            raise ValueError("ml_sharp_command must not be empty")

        self._command_tokens = shlex.split(ml_sharp_command)
        self._output_root = output_root if output_root.is_absolute() else (BASE_DIR / output_root)
        self._output_root = self._output_root.resolve()
        self._output_root.mkdir(parents=True, exist_ok=True)
        self._staging_root = self._output_root / "_staging"
        self._staging_root.mkdir(parents=True, exist_ok=True)

        self._data_root = (BASE_DIR / "data").resolve()
        self._data_root.mkdir(parents=True, exist_ok=True)
        self._uploads_root = self._data_root / "uploads"
        self._uploads_root.mkdir(parents=True, exist_ok=True)
        self._jobs_jsonl = self._data_root / "jobs.jsonl"

        self.rate_limiter = RateLimiter()

        self.jobs: dict[str, dict[str, Any]] = {}
        self._load_jobs_from_jsonl()

    @property
    def output_root(self) -> Path:
        return self._output_root

    def validate_and_stage_upload(
        self,
        filename: str,
        image_bytes: bytes,
        content_length: int | None,
    ) -> Path:
        """Validation order: file size -> magic bytes -> dimensions -> filename sanitization -> disk write."""
        self._validate_file_size(content_length, image_bytes)
        self._validate_magic_bytes(image_bytes)
        self._validate_dimensions(image_bytes)

        safe_filename = self._sanitize_filename(filename)
        return self._stage_bytes_atomically(safe_filename, image_bytes)

    def create_pending_job_from_staged(self, staged_path: Path, user_id: str | None = None) -> tuple[str, Path]:
        """Final step in flow: job creation after disk write."""
        job_id = uuid.uuid4().hex
        suffix = staged_path.suffix or ".bin"
        upload_path = self._uploads_root / f"{job_id}{suffix}"
        os.replace(staged_path, upload_path)

        self.jobs[job_id] = {
            "status": "pending",
            "user_id": user_id,
            "filename": staged_path.name,
            "created_at": self._utc_now_iso(),
            "spz_url": None,
            "depth_map_url": None,
            "error": None,
            "step": None,
        }
        return job_id, upload_path

    def process_job(self, job_id: str, input_path: Path, user_id: str | None = None) -> None:
        job_dir = self._output_root / job_id
        job_dir.mkdir(parents=True, exist_ok=True)

        if job_id not in self.jobs:
            self.jobs[job_id] = {
                "status": "pending",
                "user_id": user_id,
                "filename": input_path.name,
                "created_at": self._utc_now_iso(),
                "spz_url": None,
                "depth_map_url": None,
                "error": None,
                "step": None,
            }

        try:
            self.jobs[job_id]["status"] = "processing"
            if user_id is not None:
                self.jobs[job_id]["user_id"] = user_id

            command = [
                *self._command_tokens,
                "--input",
                str(input_path),
                "--output",
                str(job_dir),
            ]
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=False,
            )

            metadata = {
                "source_image": str(self.jobs[job_id].get("filename") or input_path.name),
                "ml_sharp_stdout": completed.stdout.strip(),
                "ml_sharp_stderr": completed.stderr.strip(),
            }
            if self.jobs[job_id].get("user_id"):
                metadata["user_id"] = str(self.jobs[job_id]["user_id"])
            (job_dir / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")

            manifest = self._read_manifest(job_dir)
            if completed.returncode != 0 and manifest.get("status") != "failed":
                manifest = {
                    "status": "failed",
                    "error": completed.stderr.strip() or completed.stdout.strip() or "ML Sharp command failed",
                    "step": "run_inference",
                }
                self._write_manifest(job_dir / "manifest.json", manifest)

            status = manifest.get("status")
            if status == "done":
                spz_name = str(manifest.get("spz", "output.spz"))
                depth_name = str(manifest.get("depth_map", "depth.png"))
                self.jobs[job_id].update(
                    {
                        "status": "done",
                        "spz_url": f"/generated/{job_id}/{spz_name}",
                        "depth_map_url": f"/generated/{job_id}/{depth_name}",
                        "error": None,
                        "step": None,
                    }
                )
                self._append_job_record(job_id, self.jobs[job_id])
            elif status == "failed":
                self.jobs[job_id].update(
                    {
                        "status": "failed",
                        "error": str(manifest.get("error", "Conversion failed")),
                        "step": str(manifest.get("step", "unknown")),
                        "spz_url": None,
                        "depth_map_url": None,
                    }
                )
                self._append_job_record(job_id, self.jobs[job_id])
            elif completed.returncode == 0 and tuple(self._discover_artifacts(job_dir)):
                self._write_manifest(
                    job_dir / "manifest.json",
                    {"status": "done", "spz": "output.spz", "depth_map": "depth.png"},
                )
                self.jobs[job_id].update(
                    {
                        "status": "done",
                        "spz_url": f"/generated/{job_id}/output.spz",
                        "depth_map_url": f"/generated/{job_id}/depth.png",
                        "error": None,
                        "step": None,
                    }
                )
                self._append_job_record(job_id, self.jobs[job_id])
            else:
                self._write_manifest(
                    job_dir / "manifest.json",
                    {
                        "status": "failed",
                        "error": "Manifest missing or invalid status",
                        "step": "emit_manifest",
                    },
                )
                self.jobs[job_id].update(
                    {
                        "status": "failed",
                        "error": "Manifest missing or invalid status",
                        "step": "emit_manifest",
                        "spz_url": None,
                        "depth_map_url": None,
                    }
                )
                self._append_job_record(job_id, self.jobs[job_id])

        except Exception as exc:
            self.jobs[job_id].update(
                {
                    "status": "failed",
                    "error": str(exc),
                    "step": "background_task",
                    "spz_url": None,
                    "depth_map_url": None,
                }
            )
            self._write_manifest(
                job_dir / "manifest.json",
                {"status": "failed", "error": str(exc), "step": "background_task"},
            )
            self._append_job_record(job_id, self.jobs[job_id])

    def delete_job(self, job_id: str) -> tuple[bool, str | None, int]:
        job = self.jobs.get(job_id)
        if job is None:
            return False, "Job not found.", 404

        if job.get("status") == "processing":
            return False, "Cannot delete a job that is currently processing.", 409

        self.jobs.pop(job_id, None)

        shutil.rmtree(self._output_root / job_id, ignore_errors=True)
        for upload_path in self._uploads_root.glob(f"{job_id}.*"):
            try:
                upload_path.unlink(missing_ok=True)
            except Exception:
                pass

        try:
            self._rewrite_jobs_jsonl_without(job_id)
        except Exception:
            logger.warning("Failed rewriting jobs history while deleting job_id=%s", job_id, exc_info=True)

        return True, None, 200

    def get_job_status(self, job_id: str) -> dict[str, Any] | None:
        job = self.jobs.get(job_id)
        job_dir = self._output_root / job_id
        manifest = self._read_manifest(job_dir)

        if job is None and not manifest:
            return None

        if job and job.get("status") in {"pending", "processing"}:
            return {"job_id": job_id, "status": str(job["status"])}

        status = str(manifest.get("status")) if manifest else (str(job.get("status")) if job else "")

        if status == "done":
            spz_url = str(job.get("spz_url")) if job and job.get("spz_url") else f"/generated/{job_id}/{manifest.get('spz', 'output.spz')}"
            depth_map_url = str(job.get("depth_map_url")) if job and job.get("depth_map_url") else f"/generated/{job_id}/{manifest.get('depth_map', 'depth.png')}"
            return {
                "job_id": job_id,
                "status": "done",
                "spz_url": spz_url,
                "depth_map_url": depth_map_url,
            }

        if status == "failed":
            error = str(manifest.get("error", "Conversion failed")) if manifest else str(job.get("error", "Conversion failed"))
            step = str(manifest.get("step", "unknown")) if manifest else str(job.get("step", "unknown"))
            return {
                "job_id": job_id,
                "status": "failed",
                "error": error,
                "step": step,
            }

        if job:
            return {"job_id": job_id, "status": str(job.get("status", "pending"))}

        return None

    def get_feed(self, page: int, page_size: int, user_id: str | None, include_pending: bool) -> dict[str, Any]:
        items = [{"job_id": job_id, **meta} for job_id, meta in self.jobs.items()]
        if not include_pending:
            items = [item for item in items if item.get("status") == "done"]
        if user_id is not None:
            items = [item for item in items if item.get("user_id") == user_id]

        items.sort(key=lambda item: str(item.get("created_at", "")), reverse=True)

        total = len(items)
        start = (page - 1) * page_size
        end = start + page_size
        page_items = items[start:end]

        response_items = [
            {
                "job_id": item.get("job_id"),
                "user_id": item.get("user_id"),
                "status": item.get("status"),
                "spz_url": item.get("spz_url"),
                "depth_map_url": item.get("depth_map_url"),
                "created_at": item.get("created_at"),
                "filename": item.get("filename"),
            }
            for item in page_items
        ]

        return {"page": page, "page_size": page_size, "total": total, "has_more": end < total, "items": response_items}

    def convert_image(self, image_path: Path, user_id: str | None = None) -> SpatialPhotoResult:
        if not image_path.exists() or not image_path.is_file():
            raise FileNotFoundError(f"Image file not found: {image_path}")

        staged_name = self._sanitize_filename(image_path.name)
        staged = self._stage_bytes_atomically(staged_name, image_path.read_bytes())
        job_id, input_path = self.create_pending_job_from_staged(staged, user_id=user_id)
        self.process_job(job_id, input_path, user_id=user_id)

        status_payload = self.get_job_status(job_id)
        if not status_payload or status_payload.get("status") != "done":
            manifest = self._read_manifest(self._output_root / job_id)
            raise SpatialPhotoConversionError(str(manifest.get("error", "Conversion failed")))

        job_dir = self._output_root / job_id
        artifacts = tuple(a for a in self._discover_artifacts(job_dir) if a.name != input_path.name)
        metadata_path = job_dir / "metadata.json"
        metadata: dict[str, str] = {}
        if metadata_path.exists():
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                metadata = {}

        return SpatialPhotoResult(job_id=job_id, artifacts=artifacts, metadata=metadata)

    def cleanup_job(self, job_id: str) -> None:
        self.delete_job(job_id)

    @staticmethod
    def _discover_artifacts(job_dir: Path) -> Iterable[SpatialPhotoArtifact]:
        supported_suffixes = {".spz", ".usdz", ".heic", ".heif", ".jpg", ".jpeg", ".png", ".mp4", ".ply"}
        if not job_dir.exists():
            return
        for path in sorted(job_dir.iterdir()):
            if (
                path.is_file()
                and path.suffix.lower() in supported_suffixes
                and path.name not in {"metadata.json", "manifest.json"}
                and not path.name.startswith("input")
            ):
                yield SpatialPhotoArtifact(name=path.name, path=path)

    def _validate_file_size(self, content_length: int | None, image_bytes: bytes) -> None:
        if content_length is not None and content_length > MAX_FILE_SIZE_BYTES:
            raise UploadValidationError("File too large. Maximum size is 20MB.", status_code=413)
        if len(image_bytes) > MAX_FILE_SIZE_BYTES:
            raise UploadValidationError("File too large. Maximum size is 20MB.", status_code=413)

    def _validate_magic_bytes(self, image_bytes: bytes) -> None:
        if self._is_jpeg(image_bytes) or self._is_png(image_bytes) or self._is_webp(image_bytes) or self._is_heic_heif(image_bytes):
            return
        raise UploadValidationError("Unsupported file type. Accepted: JPEG, PNG, WebP, HEIC/HEIF", status_code=415)

    def _validate_dimensions(self, image_bytes: bytes) -> None:
        from PIL import Image

        try:
            with Image.open(BytesIO(image_bytes)) as img:
                width, height = img.size
        except Exception as exc:
            raise UploadValidationError("Invalid image data.", status_code=422) from exc

        if width < MIN_IMAGE_DIMENSION or height < MIN_IMAGE_DIMENSION:
            raise UploadValidationError("Image too small. Minimum size is 256x256 pixels.", status_code=422)
        if width > MAX_IMAGE_DIMENSION or height > MAX_IMAGE_DIMENSION:
            raise UploadValidationError("Image too large. Maximum size is 4096x4096 pixels.", status_code=422)

    def _stage_bytes_atomically(self, safe_filename: str, image_bytes: bytes) -> Path:
        self._staging_root.mkdir(parents=True, exist_ok=True)
        final_path = self._staging_root / safe_filename

        temp_file: tempfile.NamedTemporaryFile[bytes] | None = None
        try:
            temp_file = tempfile.NamedTemporaryFile(dir=self._staging_root, delete=False)
            temp_file.write(image_bytes)
            temp_file.flush()
            os.fsync(temp_file.fileno())
            temp_name = temp_file.name
            temp_file.close()
            os.replace(temp_name, final_path)
            return final_path
        except Exception:
            if temp_file is not None:
                try:
                    Path(temp_file.name).unlink(missing_ok=True)
                except Exception:
                    pass
            raise

    @staticmethod
    def _sanitize_filename(filename: str) -> str:
        import re

        base_name = Path(filename or "").name
        try:
            from werkzeug.utils import secure_filename  # type: ignore

            sanitized = secure_filename(base_name)
        except Exception:
            sanitized = re.sub(r"[^A-Za-z0-9_.-]", "_", base_name).strip("._")

        if not sanitized:
            sanitized = f"upload-{uuid.uuid4().hex}.bin"
        return sanitized

    @staticmethod
    def _is_jpeg(data: bytes) -> bool:
        return len(data) >= 3 and data[:3] == b"\xFF\xD8\xFF"

    @staticmethod
    def _is_png(data: bytes) -> bool:
        return len(data) >= 8 and data[:8] == b"\x89PNG\r\n\x1a\n"

    @staticmethod
    def _is_webp(data: bytes) -> bool:
        return len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP"

    @staticmethod
    def _is_heic_heif(data: bytes) -> bool:
        if len(data) < 12:
            return False
        if data[4:8] != b"ftyp":
            return False
        brand = data[8:12].decode("ascii", errors="ignore").lower()
        return brand in {"heic", "heix", "mif1", "msf1"}

    @staticmethod
    def _read_manifest(job_dir: Path) -> dict[str, Any]:
        manifest_path = job_dir / "manifest.json"
        if not manifest_path.exists():
            return {}
        try:
            return json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}

    @staticmethod
    def _write_manifest(path: Path, payload: dict[str, Any]) -> None:
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _append_job_record(self, job_id: str, job: dict[str, Any]) -> None:
        self._data_root.mkdir(parents=True, exist_ok=True)
        row = {"job_id": job_id, **job}
        with self._jobs_jsonl.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    def _rewrite_jobs_jsonl_without(self, job_id: str) -> None:
        self._data_root.mkdir(parents=True, exist_ok=True)
        tmp_path = self._jobs_jsonl.with_suffix(".jsonl.tmp")

        lines: list[str] = []
        if self._jobs_jsonl.exists():
            for raw_line in self._jobs_jsonl.read_text(encoding="utf-8").splitlines():
                if not raw_line.strip():
                    continue
                keep = True
                try:
                    entry = json.loads(raw_line)
                    if isinstance(entry, dict) and entry.get("job_id") == job_id:
                        keep = False
                except json.JSONDecodeError:
                    keep = True
                if keep:
                    lines.append(raw_line)

        payload = "\n".join(lines)
        if payload:
            payload += "\n"
        tmp_path.write_text(payload, encoding="utf-8")
        os.replace(tmp_path, self._jobs_jsonl)

    def _load_jobs_from_jsonl(self) -> None:
        if not self._jobs_jsonl.exists():
            return

        for line in self._jobs_jsonl.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                logger.warning("Skipping malformed line in %s", self._jobs_jsonl)
                continue

            if not isinstance(entry, dict):
                logger.warning("Skipping non-object line in %s", self._jobs_jsonl)
                continue

            job_id = entry.get("job_id")
            if not isinstance(job_id, str) or not job_id:
                logger.warning("Skipping line without valid job_id in %s", self._jobs_jsonl)
                continue

            self.jobs[job_id] = {
                "status": entry.get("status", "failed"),
                "user_id": entry.get("user_id"),
                "filename": entry.get("filename", ""),
                "created_at": entry.get("created_at", self._utc_now_iso()),
                "spz_url": entry.get("spz_url"),
                "depth_map_url": entry.get("depth_map_url"),
                "error": entry.get("error"),
                "step": entry.get("step"),
            }

    @staticmethod
    def _utc_now_iso() -> str:
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
