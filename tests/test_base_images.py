import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from node_agent.base_images import available_images, ensure_base_image
from node_agent.schemas import BaseImageRequest


def _settings(tmp_path: Path):
    base_dir = tmp_path / "base"
    base_dir.mkdir(parents=True, exist_ok=True)
    return SimpleNamespace(base_image_dir=str(base_dir), cpu_arch="x86_64")


def test_available_images_reads_metadata_and_sidecarless_images(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    base_dir = Path(settings.base_image_dir)
    (base_dir / "default.qcow2").write_bytes(b"default")
    (base_dir / "freebsd.qcow2").write_bytes(b"freebsd")
    (base_dir / "freebsd.json").write_text(
        json.dumps(
            {
                "guest_image": "freebsd-14-stable",
                "base_image_id": "freebsd",
                "source_digest": "sha256:deadbeef",
                "cpu_arch": "x86_64",
            }
        ),
        encoding="utf-8",
    )

    images = available_images(settings)

    assert [image.base_image_id for image in images] == ["default", "freebsd"]
    assert images[0].guest_image == "default"
    assert images[1].guest_image == "freebsd-14-stable"


def test_ensure_base_image_requires_manual_local_artifact(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    with pytest.raises(FileNotFoundError):
        ensure_base_image(
            settings,
            BaseImageRequest(
                guest_image="dragonflybsd-6.4-ci",
                base_image_id="dragonflybsd-6.4-20260301",
                source_kind="manual_local",
                format="qcow2",
            ),
        )


def test_ensure_base_image_downloads_and_writes_metadata(
    monkeypatch, tmp_path: Path
) -> None:
    settings = _settings(tmp_path)
    body = b"remote-qcow2"
    digest = f"sha256:{hashlib.sha256(body).hexdigest()}"

    class _FakeStream:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def raise_for_status(self) -> None:
            return None

        def iter_bytes(self):
            yield body

    monkeypatch.setattr(
        "node_agent.base_images.httpx.stream", lambda *args, **kwargs: _FakeStream()
    )

    image_path = ensure_base_image(
        settings,
        BaseImageRequest(
            guest_image="debian-12-ci",
            base_image_id="debian-12-20260301",
            source_kind="remote_cache",
            source_url="https://example.invalid/debian.qcow2",
            source_digest=digest,
            format="qcow2",
        ),
    )

    assert Path(image_path).read_bytes() == body
    metadata = json.loads(
        Path(settings.base_image_dir, "debian-12-20260301.json").read_text(
            encoding="utf-8"
        )
    )
    assert metadata["guest_image"] == "debian-12-ci"
    assert metadata["source_digest"] == digest
