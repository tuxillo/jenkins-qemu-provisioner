import json
from pathlib import Path
from types import SimpleNamespace

from control_plane.guest_images import (
    load_image_catalog,
    load_label_policies,
    resolve_image_selection,
)


def test_guest_image_loaders_resolve_exact_label_policy(
    monkeypatch, tmp_path: Path
) -> None:
    label_policies = tmp_path / "label-policies.json"
    image_catalog = tmp_path / "image-catalog.json"
    label_policies.write_text(
        json.dumps(
            {
                "freebsd-medium": {
                    "guest_image": "freebsd-14-stable",
                    "profile": "medium",
                    "required_accel": "nvmm",
                }
            }
        ),
        encoding="utf-8",
    )
    image_catalog.write_text(
        json.dumps(
            {
                "freebsd-14-stable": {
                    "base_image_id": "freebsd-14.1-20260301",
                    "os_family": "bsd",
                    "os_flavor": "freebsd",
                    "os_version": "14.1",
                    "cpu_arch": "x86_64",
                    "source_kind": "manual_local",
                    "format": "qcow2",
                    "cache_policy": "require_warm",
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "control_plane.guest_images.get_settings",
        lambda: SimpleNamespace(
            label_policies_file=str(label_policies),
            image_catalog_file=str(image_catalog),
        ),
    )

    policies = load_label_policies()
    catalog = load_image_catalog()
    selection = resolve_image_selection("freebsd-medium")

    assert policies["freebsd-medium"].profile == "medium"
    assert catalog["freebsd-14-stable"].base_image_id == "freebsd-14.1-20260301"
    assert selection is not None
    assert selection.guest_image == "freebsd-14-stable"
    assert selection.base_image_id == "freebsd-14.1-20260301"
