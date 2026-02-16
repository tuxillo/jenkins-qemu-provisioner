#!/usr/bin/env bash
set -euo pipefail

IMAGE_PATH=${IMAGE_PATH:-/var/lib/jenkins-qemu/base/default.qcow2}
QEMU_BINARY=${QEMU_BINARY:-qemu-system-x86_64}
CPUS=${CPUS:-2}
MEMORY_MB=${MEMORY_MB:-4096}
DISK_IF=${DISK_IF:-virtio}
ACCEL=${ACCEL:-auto}
NETWORK_BACKEND=${NETWORK_BACKEND:-user}
NETWORK_INTERFACE=${NETWORK_INTERFACE:-}
SSH_FORWARD_PORT=${SSH_FORWARD_PORT:-}
HEADLESS=${HEADLESS:-0}
DRY_RUN=${DRY_RUN:-0}
EXTRA_ARGS=${EXTRA_ARGS:-}

usage() {
  cat <<'EOF'
Usage: deploy/boot-base-image.sh [options]

Boot a base qcow2 image manually for customization.

Options:
  --image PATH             Base image path (default: /var/lib/jenkins-qemu/base/default.qcow2)
  --qemu-binary BIN        QEMU binary (default: qemu-system-x86_64)
  --cpus N                 vCPU count (default: 2)
  --memory-mb N            Memory in MB (default: 4096)
  --disk-if IF             Disk interface (default: virtio)
  --accel NAME             Accelerator (auto|kvm|nvmm|tcg, default: auto)
  --network NAME           Network backend (user|bridge|tap, default: user)
  --network-if IFACE       Bridge/tap interface name
  --ssh-forward PORT       Host port forwarded to guest 22 (user mode only)
  --headless               Run with no graphics (-nographic)
  --extra-args "..."       Extra QEMU arguments appended as-is
  --dry-run                Print command and exit
  -h, --help               Show this help

Examples:
  deploy/boot-base-image.sh --image /var/lib/jenkins-qemu/base/default.qcow2 --ssh-forward 2222
  deploy/boot-base-image.sh --accel nvmm --headless
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --image)
      IMAGE_PATH="$2"
      shift 2
      ;;
    --qemu-binary)
      QEMU_BINARY="$2"
      shift 2
      ;;
    --cpus)
      CPUS="$2"
      shift 2
      ;;
    --memory-mb)
      MEMORY_MB="$2"
      shift 2
      ;;
    --disk-if)
      DISK_IF="$2"
      shift 2
      ;;
    --accel)
      ACCEL="$2"
      shift 2
      ;;
    --network)
      NETWORK_BACKEND="$2"
      shift 2
      ;;
    --network-if)
      NETWORK_INTERFACE="$2"
      shift 2
      ;;
    --ssh-forward)
      SSH_FORWARD_PORT="$2"
      shift 2
      ;;
    --headless)
      HEADLESS=1
      shift
      ;;
    --extra-args)
      EXTRA_ARGS="$2"
      shift 2
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage
      exit 1
      ;;
  esac
done

if [ ! -f "$IMAGE_PATH" ]; then
  echo "Base image not found: $IMAGE_PATH" >&2
  exit 1
fi

if ! command -v "$QEMU_BINARY" >/dev/null 2>&1; then
  echo "QEMU binary not found: $QEMU_BINARY" >&2
  exit 1
fi

detect_default_accel() {
  local uname_s
  uname_s=$(uname -s)
  case "$uname_s" in
    Linux) echo "kvm" ;;
    DragonFly) echo "nvmm" ;;
    *) echo "tcg" ;;
  esac
}

if [ "$ACCEL" = "auto" ]; then
  ACCEL=$(detect_default_accel)
fi

NETWORK_ARGS=()
case "$NETWORK_BACKEND" in
  user)
    if [ -n "$SSH_FORWARD_PORT" ]; then
      NETWORK_ARGS=(
        -netdev "user,id=net0,hostfwd=tcp::${SSH_FORWARD_PORT}-:22"
        -device virtio-net-pci,netdev=net0
      )
    else
      NETWORK_ARGS=(
        -netdev user,id=net0
        -device virtio-net-pci,netdev=net0
      )
    fi
    ;;
  bridge)
    if [ -z "$NETWORK_INTERFACE" ]; then
      echo "--network-if is required when --network bridge is used" >&2
      exit 1
    fi
    NETWORK_ARGS=(
      -netdev "bridge,id=net0,br=${NETWORK_INTERFACE}"
      -device virtio-net-pci,netdev=net0
    )
    ;;
  tap)
    if [ -z "$NETWORK_INTERFACE" ]; then
      echo "--network-if is required when --network tap is used" >&2
      exit 1
    fi
    NETWORK_ARGS=(
      -netdev "tap,id=net0,ifname=${NETWORK_INTERFACE},script=no,downscript=no"
      -device virtio-net-pci,netdev=net0
    )
    ;;
  *)
    echo "Unsupported network backend: $NETWORK_BACKEND" >&2
    exit 1
    ;;
esac

CMD=(
  "$QEMU_BINARY"
  -name "base-image-customize"
  -machine q35,accel="$ACCEL"
  -smp "$CPUS"
  -m "$MEMORY_MB"
  -drive "file=${IMAGE_PATH},format=qcow2,if=${DISK_IF}"
  "${NETWORK_ARGS[@]}"
)

if [ "$HEADLESS" = "1" ]; then
  CMD+=(-nographic)
fi

if [ -n "$EXTRA_ARGS" ]; then
  # shellcheck disable=SC2206
  EXTRA_SPLIT=($EXTRA_ARGS)
  CMD+=("${EXTRA_SPLIT[@]}")
fi

echo "Booting base image for manual customization"
echo "  image: $IMAGE_PATH"
echo "  accel: $ACCEL"
echo "  network: $NETWORK_BACKEND"
if [ -n "$SSH_FORWARD_PORT" ]; then
  echo "  ssh forward: localhost:${SSH_FORWARD_PORT} -> guest:22"
fi
if [ "$(uname -s)" = "DragonFly" ]; then
  echo "  note: if cloud-init fails on datasource import, run ./deploy/apply-cloud-init-nocloud-fix.sh inside the guest"
fi
printf '  command: %q ' "${CMD[@]}"
printf '\n'

if [ "$DRY_RUN" = "1" ]; then
  exit 0
fi

exec "${CMD[@]}"
