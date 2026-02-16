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


def _tail_text(path: str | None, line_count: int = 120) -> str | None:
    if not path:
        return None
    p = Path(path)
    if not p.exists() or not p.is_file():
        return None
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(lines[-line_count:])


def _read_text(path: str | None, limit: int = 20000) -> str | None:
    if not path:
        return None
    p = Path(path)
    if not p.exists() or not p.is_file():
        return None
    return p.read_text(encoding="utf-8", errors="replace")[:limit]


def _sanitize_env_text(text: str | None) -> str | None:
    if text is None:
        return None
    out = []
    for line in text.splitlines():
        if line.startswith("JENKINS_JNLP_SECRET="):
            out.append("JENKINS_JNLP_SECRET=***")
        else:
            out.append(line)
    return "\n".join(out)


def _report_vm_status(
    settings, vm_id: str, state: str, reason: str | None = None
) -> None:
    payload = {"state": state, "reason": reason}
    try:
        with httpx.Client(base_url=settings.control_plane_url, timeout=5.0) as client:
            client.post(f"/v1/vms/{vm_id}/status", json=payload)
    except Exception:  # noqa: BLE001
        logger.debug("failed posting vm status vm_id=%s state=%s", vm_id, state)


def _raise_launch_error(
    *,
    settings,
    vm_id: str,
    lease_id: str | None,
    stage: str,
    status_code: int,
    reason: str,
) -> None:
    logger.error(
        "ensure_vm failed vm_id=%s lease_id=%s host_id=%s stage=%s reason=%s",
        vm_id,
        lease_id,
        settings.host_id,
        stage,
        reason,
    )
    _report_vm_status(settings, vm_id, "FAILED", f"{stage}: {reason}")
    raise HTTPException(
        status_code=status_code,
        detail={"vm_id": vm_id, "stage": stage, "reason": reason},
    )


@router.get("/healthz")
def healthz() -> dict:
    settings = get_agent_settings()
    return {
        "status": "ok",
        "host_id": settings.host_id,
        "os_family": settings.os_family,
        "os_flavor": settings.os_flavor,
        "cpu_arch": settings.cpu_arch,
        "qemu_accel": settings.qemu_accel,
        "generated_at": datetime.now(UTC).isoformat(),
    }


@router.put("/v1/vms/{vm_id}", response_model=VMStateResponse)
def ensure_vm(vm_id: str, req: VMEnsureRequest) -> VMStateResponse:
    settings = get_agent_settings()
    _report_vm_status(settings, vm_id, "PROVISIONING")
    lease_id = None
    if isinstance(req.metadata, dict):
        lease_id = req.metadata.get("lease_id")
    existing = get_vm(vm_id)
    if existing and existing["state"] in {"RUNNING", "BOOTING"}:
        return VMStateResponse(
            vm_id=vm_id,
            state=existing["state"],
            last_transition_at=existing["updated_at"],
            reason=existing.get("reason"),
            lease_expires_at=existing.get("lease_expires_at"),
            serial_log_path=existing.get("serial_log_path"),
        )

    base_image_path = str(Path(settings.base_image_dir) / f"{req.base_image_id}.qcow2")
    if not settings.dry_run and not Path(base_image_path).exists():
        _raise_launch_error(
            settings=settings,
            vm_id=vm_id,
            lease_id=lease_id,
            stage="base_image",
            status_code=400,
            reason=f"base image not found: {base_image_path}",
        )

    runtime_paths = None
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
        _raise_launch_error(
            settings=settings,
            vm_id=vm_id,
            lease_id=lease_id,
            stage="cloud_init",
            status_code=500,
            reason=f"cloud-init generation failed: {exc}",
        )
    if runtime_paths is None:
        _raise_launch_error(
            settings=settings,
            vm_id=vm_id,
            lease_id=lease_id,
            stage="cloud_init",
            status_code=500,
            reason="cloud-init generation failed: unknown error",
        )
    assert runtime_paths is not None

    if req.overlay_path:
        runtime_paths.overlay_path = req.overlay_path

    if not settings.dry_run:
        try:
            create_overlay(base_image_path, runtime_paths.overlay_path)
        except Exception as exc:  # noqa: BLE001
            _raise_launch_error(
                settings=settings,
                vm_id=vm_id,
                lease_id=lease_id,
                stage="overlay",
                status_code=500,
                reason=f"overlay creation failed: {exc}",
            )
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
        serial_log_path=runtime_paths.serial_log_path,
    )
    launch_cmd_path = str(
        Path(runtime_paths.serial_log_path).with_name("launch-command.txt")
    )
    Path(launch_cmd_path).write_text(" ".join(cmd) + "\n", encoding="utf-8")
    pid = 0
    try:
        pid = launch_qemu(cmd, dry_run=settings.dry_run)
    except Exception as exc:  # noqa: BLE001
        _raise_launch_error(
            settings=settings,
            vm_id=vm_id,
            lease_id=lease_id,
            stage="qemu_launch",
            status_code=500,
            reason=f"qemu launch failed: {exc}",
        )
    if pid <= 0 and not settings.dry_run:
        _raise_launch_error(
            settings=settings,
            vm_id=vm_id,
            lease_id=lease_id,
            stage="qemu_launch",
            status_code=500,
            reason="qemu launch failed: missing process pid",
        )

    upsert_vm(
        vm_id=vm_id,
        state="RUNNING" if not settings.dry_run else "BOOTING",
        host_id=settings.host_id,
        lease_id=lease_id,
        qemu_pid=pid,
        overlay_path=runtime_paths.overlay_path,
        cloud_init_iso=runtime_paths.cloud_init_iso,
        serial_log_path=runtime_paths.serial_log_path,
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
        serial_log_path=runtime_paths.serial_log_path,
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
        serial_log_path=row.get("serial_log_path"),
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


@router.get("/v1/vms/{vm_id}/debug")
def vm_debug(vm_id: str, tail: int = Query(default=120, ge=10, le=2000)) -> dict:
    row = get_vm(vm_id)
    if not row:
        raise HTTPException(status_code=404, detail="unknown vm")

    serial_log_path = row.get("serial_log_path")
    vm_dir = None
    if serial_log_path:
        vm_dir = str(Path(serial_log_path).parent)
    launch_cmd_path = str(Path(vm_dir) / "launch-command.txt") if vm_dir else None
    user_data_path = str(Path(vm_dir) / "user-data") if vm_dir else None
    env_path = str(Path(vm_dir) / "jenkins-agent.env") if vm_dir else None

    return {
        "vm_id": vm_id,
        "state": row.get("state"),
        "qemu_pid": row.get("qemu_pid"),
        "lease_id": row.get("lease_id"),
        "host_id": row.get("host_id"),
        "paths": {
            "serial_log_path": serial_log_path,
            "cloud_init_iso": row.get("cloud_init_iso"),
            "overlay_path": row.get("overlay_path"),
            "launch_command_path": launch_cmd_path,
            "user_data_path": user_data_path,
            "jenkins_env_path": env_path,
        },
        "launch_command": _read_text(launch_cmd_path, limit=16000),
        "serial_tail": _tail_text(serial_log_path, line_count=tail),
        "user_data": _read_text(user_data_path, limit=16000),
        "jenkins_env": _sanitize_env_text(_read_text(env_path, limit=4000)),
    }


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
        "os_flavor": settings.os_flavor,
        "cpu_arch": settings.cpu_arch,
        "selected_accel": settings.qemu_accel,
        "supported_accels": settings.supported_accels,
        "cpu_free": cpu_free,
        "cpu_total": cpu_total,
        "ram_total_mb": ram_total_mb,
        "ram_free_mb": ram_free_mb,
        "io_pressure": 0.0,
        "running_vms": len(running),
    }
