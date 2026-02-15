#!/usr/bin/env bash
set -euo pipefail

PREFIX=${PREFIX:-/opt/jenkins-qemu-node-agent}
ETC_DIR=${ETC_DIR:-/etc/jenkins-qemu-node-agent}
DATA_DIR=${DATA_DIR:-/var/lib/jenkins-qemu}
SERVICE_USER=${SERVICE_USER:-jenkins-qemu-agent}
NODE_AGENT_PIP_CONSTRAINT=${NODE_AGENT_PIP_CONSTRAINT:-}

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)

uname_s=$(uname -s)
case "$uname_s" in
  Linux)
    default_os_family="linux"
    default_qemu_accel="kvm"
    default_service_manager="systemd"
    ;;
  DragonFly)
    default_os_family="dragonflybsd"
    default_qemu_accel="nvmm"
    default_service_manager="rcd"
    ;;
  *)
    echo "Unsupported OS: $uname_s" >&2
    echo "This installer supports Linux and DragonFlyBSD." >&2
    exit 1
    ;;
esac

NODE_AGENT_OS_FAMILY=${NODE_AGENT_OS_FAMILY:-$default_os_family}
NODE_AGENT_QEMU_ACCEL=${NODE_AGENT_QEMU_ACCEL:-$default_qemu_accel}
NODE_AGENT_SERVICE_MANAGER=${NODE_AGENT_SERVICE_MANAGER:-$default_service_manager}

if [ -z "$NODE_AGENT_PIP_CONSTRAINT" ] && [ "$uname_s" = "DragonFly" ]; then
  NODE_AGENT_PIP_CONSTRAINT="$script_dir/constraints-dragonfly.txt"
fi

if [ -x /usr/sbin/nologin ]; then
  NOLOGIN_SHELL=/usr/sbin/nologin
elif [ -x /sbin/nologin ]; then
  NOLOGIN_SHELL=/sbin/nologin
else
  NOLOGIN_SHELL=/usr/bin/false
fi

if ! id "$SERVICE_USER" >/dev/null 2>&1; then
  case "$uname_s" in
    Linux)
      sudo useradd --system --home "$PREFIX" --shell "$NOLOGIN_SHELL" "$SERVICE_USER"
      ;;
    DragonFly)
      sudo pw useradd "$SERVICE_USER" -d "$PREFIX" -s "$NOLOGIN_SHELL" -c "Jenkins QEMU node agent"
      ;;
  esac
fi

sudo mkdir -p "$PREFIX" "$ETC_DIR" "$DATA_DIR/base" "$DATA_DIR/overlays" "$DATA_DIR/cloud-init"
sudo chown -R "$SERVICE_USER":"$SERVICE_USER" "$PREFIX" "$DATA_DIR"

python3 -m venv "$PREFIX/venv"
"$PREFIX/venv/bin/pip" install --upgrade pip
if [ -n "$NODE_AGENT_PIP_CONSTRAINT" ]; then
  PIP_CONSTRAINT="$NODE_AGENT_PIP_CONSTRAINT" "$PREFIX/venv/bin/pip" install .
else
  "$PREFIX/venv/bin/pip" install .
fi

if [ ! -f "$ETC_DIR/env" ]; then
  cat <<EOF | sudo tee "$ETC_DIR/env" >/dev/null
NODE_AGENT_HOST_ID=host-1
NODE_AGENT_BOOTSTRAP_TOKEN=replace-me
NODE_AGENT_CONTROL_PLANE_URL=http://127.0.0.1:8000
NODE_AGENT_OS_FAMILY=$NODE_AGENT_OS_FAMILY
NODE_AGENT_QEMU_ACCEL=$NODE_AGENT_QEMU_ACCEL
NODE_AGENT_SERVICE_MANAGER=$NODE_AGENT_SERVICE_MANAGER
NODE_AGENT_STATE_DB_PATH=/var/lib/jenkins-qemu/node_agent.db
NODE_AGENT_BASE_IMAGE_DIR=/var/lib/jenkins-qemu/base
NODE_AGENT_OVERLAY_DIR=/var/lib/jenkins-qemu/overlays
NODE_AGENT_CLOUD_INIT_DIR=/var/lib/jenkins-qemu/cloud-init
EOF
fi

echo "Install complete."
echo "Detected OS: $uname_s"
echo "Generated defaults: NODE_AGENT_OS_FAMILY=$NODE_AGENT_OS_FAMILY NODE_AGENT_QEMU_ACCEL=$NODE_AGENT_QEMU_ACCEL NODE_AGENT_SERVICE_MANAGER=$NODE_AGENT_SERVICE_MANAGER"
if [ -n "$NODE_AGENT_PIP_CONSTRAINT" ]; then
  echo "Using pip build constraint: $NODE_AGENT_PIP_CONSTRAINT"
fi
echo "Configure $ETC_DIR/env and install service unit for your OS."

if [ "$NODE_AGENT_SERVICE_MANAGER" = "systemd" ]; then
  echo ""
  echo "Linux service setup:"
  echo "  sudo cp deploy/systemd/jenkins-qemu-node-agent.service /etc/systemd/system/"
  echo "  sudo systemctl daemon-reload"
  echo "  sudo systemctl enable --now jenkins-qemu-node-agent"
  echo "  sudo systemctl status jenkins-qemu-node-agent"
elif [ "$NODE_AGENT_SERVICE_MANAGER" = "rcd" ]; then
  echo ""
  echo "DragonFlyBSD service setup:"
  echo "  sudo cp deploy/rc.d/jenkins_qemu_node_agent /usr/local/etc/rc.d/"
  echo "  sudo chmod +x /usr/local/etc/rc.d/jenkins_qemu_node_agent"
  echo "  echo 'jenkins_qemu_node_agent_enable=\"YES\"' | sudo tee -a /etc/rc.conf"
  echo "  sudo service jenkins_qemu_node_agent start"
  echo "  sudo service jenkins_qemu_node_agent status"
fi
