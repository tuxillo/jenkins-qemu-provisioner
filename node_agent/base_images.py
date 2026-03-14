from __future__ import annotations

import hashlib
import json
import os
import threading
from pathlib import Path

import httpx

from node_agent.schemas import AvailableImageResponse, BaseImageRequest


_image_locks: dict[str, threading.Lock] = {}
_image_locks_guard = threading.Lock()


def available_images(settings) -> list[AvailableImageResponse]:
    base_dir = Path(settings.base_image_dir)
    images: list[AvailableImageResponse] = []
    for image_path in sorted(base_dir.glob("*.qcow2")):
        base_image_id = image_path.stem
        metadata = _read_metadata(_metadata_path(settings, base_image_id))
        images.append(
            AvailableImageResponse(
                guest_image=str(metadata.get("guest_image") or base_image_id),
                base_image_id=base_image_id,
                source_digest=_as_optional_str(metadata.get("source_digest")),
                cpu_arch=_as_optional_str(metadata.get("cpu_arch"))
                or settings.cpu_arch,
                state="READY",
            )
        )
    return images


def ensure_base_image(settings, base_image: BaseImageRequest) -> str:
    if base_image.format != "qcow2":
        raise ValueError(f"unsupported base image format: {base_image.format}")

    image_path = _image_path(settings, base_image.base_image_id)
    metadata_path = _metadata_path(settings, base_image.base_image_id)

    with _lock_for(base_image.base_image_id):
        if image_path.exists() and _matches_requested_image(
            image_path=image_path,
            metadata_path=metadata_path,
            base_image=base_image,
            settings=settings,
        ):
            _write_metadata(settings, base_image, metadata_path)
            return str(image_path)

        if base_image.source_kind == "manual_local":
            raise FileNotFoundError(f"manual-local base image not found: {image_path}")

        assert base_image.source_url is not None
        assert base_image.source_digest is not None
        image_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = image_path.with_suffix(image_path.suffix + ".download")
        try:
            digest = _download_image(base_image.source_url, temp_path)
            _verify_digest(digest, base_image.source_digest)
            os.replace(temp_path, image_path)
        finally:
            if temp_path.exists():
                temp_path.unlink(missing_ok=True)
        _write_metadata(settings, base_image, metadata_path)
        return str(image_path)


def _lock_for(base_image_id: str) -> threading.Lock:
    with _image_locks_guard:
        return _image_locks.setdefault(base_image_id, threading.Lock())


def _image_path(settings, base_image_id: str) -> Path:
    return Path(settings.base_image_dir) / f"{base_image_id}.qcow2"


def _metadata_path(settings, base_image_id: str) -> Path:
    return Path(settings.base_image_dir) / f"{base_image_id}.json"


def _read_metadata(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    return data if isinstance(data, dict) else {}


def _write_metadata(
    settings, base_image: BaseImageRequest, metadata_path: Path
) -> None:
    payload = {
        "guest_image": base_image.guest_image,
        "base_image_id": base_image.base_image_id,
        "source_kind": base_image.source_kind,
        "source_url": base_image.source_url,
        "source_digest": base_image.source_digest,
        "format": base_image.format,
        "cpu_arch": settings.cpu_arch,
    }
    metadata_path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def _matches_requested_image(
    *,
    image_path: Path,
    metadata_path: Path,
    base_image: BaseImageRequest,
    settings,
) -> bool:
    metadata = _read_metadata(metadata_path)
    if base_image.source_kind == "manual_local":
        return True
    expected_digest = base_image.source_digest
    if not expected_digest:
        return True
    actual_digest = _as_optional_str(metadata.get("source_digest"))
    if actual_digest == expected_digest:
        return True
    calculated = _calculate_sha256(image_path)
    if calculated == _normalized_digest(expected_digest):
        _write_metadata(settings, base_image, metadata_path)
        return True
    return False


def _download_image(source_url: str, destination: Path) -> str:
    hasher = hashlib.sha256()
    with httpx.stream(
        "GET", source_url, follow_redirects=True, timeout=60.0
    ) as response:
        response.raise_for_status()
        with destination.open("wb") as handle:
            for chunk in response.iter_bytes():
                if not chunk:
                    continue
                handle.write(chunk)
                hasher.update(chunk)
    return f"sha256:{hasher.hexdigest()}"


def _calculate_sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            hasher.update(chunk)
    return _normalized_digest(f"sha256:{hasher.hexdigest()}")


def _verify_digest(actual_digest: str, expected_digest: str) -> None:
    if _normalized_digest(actual_digest) != _normalized_digest(expected_digest):
        raise ValueError("downloaded base image digest mismatch")


def _normalized_digest(value: str) -> str:
    if value.startswith("sha256:"):
        return value
    return f"sha256:{value}"


def _as_optional_str(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
