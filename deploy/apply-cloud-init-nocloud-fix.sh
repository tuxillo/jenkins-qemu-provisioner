#!/usr/bin/env bash
set -euo pipefail

TARGET_DIR=${TARGET_DIR:-/etc/cloud/cloud.cfg.d}
TARGET_FILE=${TARGET_FILE:-$TARGET_DIR/99-datasource-nocloud.cfg}

if [ "$(id -u)" -ne 0 ]; then
  echo "This script must run as root (inside the guest image)." >&2
  exit 1
fi

mkdir -p "$TARGET_DIR"

cat >"$TARGET_FILE" <<'EOF'
datasource_list: [ NoCloud, None ]
EOF

chmod 0644 "$TARGET_FILE"

echo "Wrote $TARGET_FILE"

if command -v cloud-init >/dev/null 2>&1; then
  cloud-init clean --logs || true
  echo "Ran: cloud-init clean --logs"
fi

echo "Done. Reboot the guest image and verify datasource is NoCloud."
