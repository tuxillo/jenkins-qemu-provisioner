#!/usr/bin/env bash
set -euo pipefail

PREFIX=${PREFIX:-/opt/jenkins-qemu-node-agent}
ETC_DIR=${ETC_DIR:-/etc/jenkins-qemu-node-agent}
DATA_DIR=${DATA_DIR:-/var/lib/jenkins-qemu}
SERVICE_USER=${SERVICE_USER:-jenkins-qemu-agent}

if ! id "$SERVICE_USER" >/dev/null 2>&1; then
  sudo useradd --system --home "$PREFIX" --shell /usr/sbin/nologin "$SERVICE_USER"
fi

sudo mkdir -p "$PREFIX" "$ETC_DIR" "$DATA_DIR/base" "$DATA_DIR/overlays" "$DATA_DIR/cloud-init"
sudo chown -R "$SERVICE_USER":"$SERVICE_USER" "$PREFIX" "$DATA_DIR"

python3 -m venv "$PREFIX/venv"
"$PREFIX/venv/bin/pip" install --upgrade pip
"$PREFIX/venv/bin/pip" install .

if [ ! -f "$ETC_DIR/env" ]; then
  cat <<'EOF' | sudo tee "$ETC_DIR/env" >/dev/null
NODE_AGENT_HOST_ID=host-1
NODE_AGENT_BOOTSTRAP_TOKEN=replace-me
NODE_AGENT_CONTROL_PLANE_URL=http://127.0.0.1:8000
NODE_AGENT_OS_FAMILY=linux
NODE_AGENT_QEMU_ACCEL=kvm
NODE_AGENT_SERVICE_MANAGER=systemd
NODE_AGENT_STATE_DB_PATH=/var/lib/jenkins-qemu/node_agent.db
NODE_AGENT_BASE_IMAGE_DIR=/var/lib/jenkins-qemu/base
NODE_AGENT_OVERLAY_DIR=/var/lib/jenkins-qemu/overlays
NODE_AGENT_CLOUD_INIT_DIR=/var/lib/jenkins-qemu/cloud-init
EOF
fi

echo "Install complete. Configure $ETC_DIR/env and install service unit for your OS."
