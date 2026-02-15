import json
import logging
import os
from pathlib import Path
from datetime import UTC, datetime

import httpx
from fastapi import APIRouter, HTTPException, Query

from node_agent.config import get_agent_settings
from node_agent.qemu_runtime import (
    build_qemu_command,
    create_overlay,
    launch_qemu,
    terminate_pid,
    write_cloud_init_files,
)
from node_agent.schemas import VMEnsureRequest, VMStateResponse
from node_agent.state import delete_vm, get_vm, list_vms, upsert_vm, update_vm_state


router = APIRouter()
logger = logging.getLogger(__name__)


def _report_vm_status(
    settings, vm_id: str, state: str, reason: str | None = None
) -> None:
    payload = {"state": state, "reason": reason}
    try:
        with httpx.Client(base_url=settings.control_plane_url, timeout=5.0) as client:
            client.post(f"/v1/vms/{vm_id}/status", json=payload)
    except Exception:  # noqa: BLE001
        logger.debug("failed posting vm status vm_id=%s state=%s", vm_id, state)


@router.get("/healthz")
def healthz() -> dict:
    settings = get_agent_settings()
    return {
        "status": "ok",
        "host_id": settings.host_id,
        "os_family": settings.os_family,
        "qemu_accel": settings.qemu_accel,
        "generated_at": datetime.now(UTC).isoformat(),
    }


@router.put("/v1/vms/{vm_id}", response_model=VMStateResponse)
def ensure_vm(vm_id: str, req: VMEnsureRequest) -> VMStateResponse:
    settings = get_agent_settings()
    _report_vm_status(settings, vm_id, "PROVISIONING")
    existing = get_vm(vm_id)
    if existing and existing["state"] in {"RUNNING", "BOOTING"}:
        return VMStateResponse(
            vm_id=vm_id,
            state=existing["state"],
            last_transition_at=existing["updated_at"],
            reason=existing.get("reason"),
            lease_expires_at=existing.get("lease_expires_at"),
        )

    base_image_path = str(Path(settings.base_image_dir) / f"{req.base_image_id}.qcow2")
    if not settings.dry_run and not Path(base_image_path).exists():
        _report_vm_status(
            settings, vm_id, "FAILED", f"base image not found: {base_image_path}"
        )
        raise HTTPException(
            status_code=400, detail=f"base image not found: {base_image_path}"
        )

    try:
        runtime_paths = write_cloud_init_files(
            settings=settings,
            vm_id=vm_id,
            user_data_b64=req.cloud_init_user_data_b64,
            node_name=req.jenkins_node_name,
            jenkins_url=req.jenkins_url,
            jnlp_secret=req.jnlp_secret,
        )
    except Exception as exc:  # noqa: BLE001
        _report_vm_status(settings, vm_id, "FAILED", f"cloud-init failed: {exc}")
        logger.exception("failed writing cloud-init vm_id=%s error=%s", vm_id, exc)
        raise HTTPException(
            status_code=500,
            detail=f"cloud-init generation failed for {vm_id}: {exc}",
        ) from exc

    if req.overlay_path:
        runtime_paths.overlay_path = req.overlay_path

    if not settings.dry_run:
        try:
            create_overlay(base_image_path, runtime_paths.overlay_path)
        except Exception as exc:  # noqa: BLE001
            _report_vm_status(
                settings, vm_id, "FAILED", f"overlay creation failed: {exc}"
            )
            logger.exception(
                "failed creating overlay vm_id=%s base=%s overlay=%s error=%s",
                vm_id,
                base_image_path,
                runtime_paths.overlay_path,
                exc,
            )
            raise HTTPException(
                status_code=500,
                detail=f"overlay creation failed for {vm_id}: {exc}",
            ) from exc
    else:
        Path(runtime_paths.overlay_path).parent.mkdir(parents=True, exist_ok=True)
        Path(runtime_paths.overlay_path).touch(exist_ok=True)

    cmd = build_qemu_command(
        settings,
        vm_id=vm_id,
        base_image_path=base_image_path,
        overlay_path=runtime_paths.overlay_path,
        cloud_init_iso=runtime_paths.cloud_init_iso,
        vcpu=req.vcpu,
        ram_mb=req.ram_mb,
        disk_interface=settings.disk_interface,
    )
    try:
        pid = launch_qemu(cmd, dry_run=settings.dry_run)
    except Exception as exc:  # noqa: BLE001
        _report_vm_status(settings, vm_id, "FAILED", f"qemu launch failed: {exc}")
        logger.exception("failed launching qemu vm_id=%s error=%s", vm_id, exc)
        raise HTTPException(
            status_code=500,
            detail=f"qemu launch failed for {vm_id}: {exc}",
        ) from exc

    lease_id = None
    if isinstance(req.metadata, dict):
        lease_id = req.metadata.get("lease_id")

    upsert_vm(
        vm_id=vm_id,
        state="RUNNING" if not settings.dry_run else "BOOTING",
        host_id=settings.host_id,
        lease_id=lease_id,
        qemu_pid=pid,
        overlay_path=runtime_paths.overlay_path,
        cloud_init_iso=runtime_paths.cloud_init_iso,
        connect_deadline=req.connect_deadline.isoformat(),
        lease_expires_at=req.lease_expires_at.isoformat(),
        reason=None,
    )
    state = "RUNNING" if not settings.dry_run else "BOOTING"
    _report_vm_status(settings, vm_id, state)
    return VMStateResponse(
        vm_id=vm_id,
        state=state,
        last_transition_at=datetime.now(UTC).isoformat(),
        lease_expires_at=req.lease_expires_at.isoformat(),
    )


@router.get("/v1/vms/{vm_id}", response_model=VMStateResponse)
def vm_state(vm_id: str) -> VMStateResponse:
    row = get_vm(vm_id)
    if not row:
        raise HTTPException(status_code=404, detail="unknown vm")
    return VMStateResponse(
        vm_id=vm_id,
        state=row["state"],
        last_transition_at=row["updated_at"],
        reason=row.get("reason"),
        lease_expires_at=row.get("lease_expires_at"),
    )


@router.get("/v1/vms")
def vm_list(
    state: str | None = Query(default=None),
    host_id: str | None = Query(default=None),
) -> list[dict]:
    rows = list_vms()
    if state:
        rows = [r for r in rows if r.get("state") == state]
    if host_id:
        rows = [r for r in rows if r.get("host_id") == host_id]
    return rows


@router.delete("/v1/vms/{vm_id}")
def terminate_vm(vm_id: str, reason: str = "requested", force: bool = False) -> dict:
    settings = get_agent_settings()
    row = get_vm(vm_id)
    if not row:
        return {"vm_id": vm_id, "state": "TERMINATED", "deleted_overlay": False}

    pid = int(row.get("qemu_pid") or 0)
    terminate_pid(pid, dry_run=settings.dry_run and not force)

    overlay_path = row.get("overlay_path")
    cloud_init_iso = row.get("cloud_init_iso")

    deleted_overlay = False
    if overlay_path and Path(overlay_path).exists():
        Path(overlay_path).unlink(missing_ok=True)
        deleted_overlay = True
    if cloud_init_iso and Path(cloud_init_iso).exists():
        Path(cloud_init_iso).unlink(missing_ok=True)

    update_vm_state(vm_id, "TERMINATED", reason=reason, qemu_pid=0)
    delete_vm(vm_id)

    return {"vm_id": vm_id, "state": "TERMINATED", "deleted_overlay": deleted_overlay}


@router.get("/v1/capacity")
def capacity() -> dict:
    settings = get_agent_settings()
    cpu_total = os.cpu_count() or 1
    load_1 = os.getloadavg()[0] if hasattr(os, "getloadavg") else 0.0
    cpu_free = max(int(cpu_total - load_1), 0)

    ram_total_mb = 0
    ram_free_mb = 0
    meminfo = Path("/proc/meminfo")
    if meminfo.exists():
        values = {}
        for line in meminfo.read_text(encoding="utf-8").splitlines():
            parts = line.split(":", 1)
            if len(parts) == 2:
                values[parts[0].strip()] = parts[1].strip()
        total_kb = int(values.get("MemTotal", "0 kB").split()[0])
        avail_kb = int(values.get("MemAvailable", "0 kB").split()[0])
        ram_total_mb = total_kb // 1024
        ram_free_mb = avail_kb // 1024

    rows = list_vms()
    running = [
        r for r in rows if r.get("state") in {"RUNNING", "BOOTING", "PROVISIONING"}
    ]
    return {
        "host_id": settings.host_id,
        "os_family": settings.os_family,
        "selected_accel": settings.qemu_accel,
        "supported_accels": [settings.qemu_accel, "tcg"],
        "cpu_free": cpu_free,
        "cpu_total": cpu_total,
        "ram_total_mb": ram_total_mb,
        "ram_free_mb": ram_free_mb,
        "io_pressure": 0.0,
        "running_vms": len(running),
    }
