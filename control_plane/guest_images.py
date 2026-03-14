from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from control_plane.config import get_settings


ProfileName = Literal["small", "medium", "large"]
CachePolicy = Literal["prefer_warm", "require_warm"]
SourceKind = Literal["manual_local", "remote_cache"]


class LabelPolicy(BaseModel):
    guest_image: str = Field(min_length=1)
    profile: ProfileName
    required_accel: str | None = None
    required_cpu_arch: str | None = None


class ImageCatalogEntry(BaseModel):
    guest_image: str = Field(min_length=1)
    base_image_id: str = Field(min_length=1)
    os_family: str = Field(min_length=1)
    os_flavor: str = Field(min_length=1)
    os_version: str = Field(min_length=1)
    cpu_arch: str = Field(min_length=1)
    source_kind: SourceKind
    source_url: str | None = None
    source_digest: str | None = None
    format: str = Field(default="qcow2", min_length=1)
    cache_policy: CachePolicy = Field(default="require_warm")

    @model_validator(mode="after")
    def validate_source_fields(self) -> "ImageCatalogEntry":
        if self.source_kind == "remote_cache":
            if not self.source_url:
                raise ValueError("remote_cache images require source_url")
            if not self.source_digest:
                raise ValueError("remote_cache images require source_digest")
        return self


class AvailableImage(BaseModel):
    guest_image: str = Field(min_length=1)
    base_image_id: str = Field(min_length=1)
    source_digest: str | None = None
    cpu_arch: str | None = None
    state: str = Field(default="READY")


class ResolvedImageSelection(BaseModel):
    guest_image: str
    profile: ProfileName
    required_accel: str | None = None
    required_cpu_arch: str | None = None
    base_image_id: str
    os_family: str
    os_flavor: str
    os_version: str
    cpu_arch: str
    source_kind: SourceKind
    source_url: str | None = None
    source_digest: str | None = None
    format: str
    cache_policy: CachePolicy


def _json_file_key(path: str | None) -> tuple[str, int]:
    resolved = Path(path) if path else Path()
    if not path:
        return "", -1
    try:
        stat = resolved.stat()
    except FileNotFoundError:
        return str(resolved), -1
    return str(resolved), stat.st_mtime_ns


@lru_cache(maxsize=8)
def _load_label_policies_cached(
    path_str: str, _mtime_ns: int
) -> dict[str, LabelPolicy]:
    if not path_str:
        return {}
    path = Path(path_str)
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("label policy file must contain a JSON object")
    return {label: LabelPolicy.model_validate(policy) for label, policy in raw.items()}


@lru_cache(maxsize=8)
def _load_image_catalog_cached(
    path_str: str, _mtime_ns: int
) -> dict[str, ImageCatalogEntry]:
    if not path_str:
        return {}
    path = Path(path_str)
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("image catalog file must contain a JSON object")
    catalog: dict[str, ImageCatalogEntry] = {}
    for guest_image, entry in raw.items():
        payload = {"guest_image": guest_image, **entry}
        catalog[guest_image] = ImageCatalogEntry.model_validate(payload)
    return catalog


def load_label_policies() -> dict[str, LabelPolicy]:
    settings = get_settings()
    path_str, mtime_ns = _json_file_key(settings.label_policies_file)
    return _load_label_policies_cached(path_str, mtime_ns)


def load_image_catalog() -> dict[str, ImageCatalogEntry]:
    settings = get_settings()
    path_str, mtime_ns = _json_file_key(settings.image_catalog_file)
    return _load_image_catalog_cached(path_str, mtime_ns)


def resolve_label_policy(label: str) -> LabelPolicy | None:
    return load_label_policies().get(label)


def resolve_image_catalog_entry(guest_image: str) -> ImageCatalogEntry | None:
    return load_image_catalog().get(guest_image)


def resolve_image_selection(label: str) -> ResolvedImageSelection | None:
    policy = resolve_label_policy(label)
    if policy is None:
        settings = get_settings()
        if not settings.guest_image_compat_mode:
            return None
        catalog_entry = resolve_image_catalog_entry("default")
        if catalog_entry is None:
            return None
        lowered = label.lower()
        profile: ProfileName
        if "large" in lowered:
            profile = "large"
        elif "medium" in lowered:
            profile = "medium"
        else:
            profile = "small"
        required_accel = None
        if "nvmm" in lowered:
            required_accel = "nvmm"
        elif "kvm" in lowered:
            required_accel = "kvm"
        return ResolvedImageSelection(
            guest_image=catalog_entry.guest_image,
            profile=profile,
            required_accel=required_accel,
            required_cpu_arch=None,
            base_image_id=catalog_entry.base_image_id,
            os_family=catalog_entry.os_family,
            os_flavor=catalog_entry.os_flavor,
            os_version=catalog_entry.os_version,
            cpu_arch=catalog_entry.cpu_arch,
            source_kind=catalog_entry.source_kind,
            source_url=catalog_entry.source_url,
            source_digest=catalog_entry.source_digest,
            format=catalog_entry.format,
            cache_policy=catalog_entry.cache_policy,
        )
    catalog_entry = resolve_image_catalog_entry(policy.guest_image)
    if catalog_entry is None:
        return None
    return ResolvedImageSelection(
        guest_image=policy.guest_image,
        profile=policy.profile,
        required_accel=policy.required_accel,
        required_cpu_arch=policy.required_cpu_arch,
        base_image_id=catalog_entry.base_image_id,
        os_family=catalog_entry.os_family,
        os_flavor=catalog_entry.os_flavor,
        os_version=catalog_entry.os_version,
        cpu_arch=catalog_entry.cpu_arch,
        source_kind=catalog_entry.source_kind,
        source_url=catalog_entry.source_url,
        source_digest=catalog_entry.source_digest,
        format=catalog_entry.format,
        cache_policy=catalog_entry.cache_policy,
    )
