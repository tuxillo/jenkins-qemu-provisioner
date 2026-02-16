import base64
import re
import textwrap
import uuid
from datetime import UTC, datetime, timedelta

from control_plane.clients.jenkins import JenkinsClient
from control_plane.clients.node_agent import NodeAgentClient
from control_plane.config import get_settings
from control_plane.db import session_scope
from control_plane.models import Lease, LeaseState
from control_plane.repositories import now_utc, write_event


NODE_PROFILES = {
    "small": {"vcpu": 2, "ram_mb": 4096, "disk_gb": 40},
    "medium": {"vcpu": 4, "ram_mb": 8192, "disk_gb": 80},
    "large": {"vcpu": 8, "ram_mb": 16384, "disk_gb": 120},
}


def normalize_node_label(label: str) -> str:
    tokens = re.split(r"[^A-Za-z0-9_.:-]+", label)
    cleaned: list[str] = []
    for token in tokens:
        if not token:
            continue
        lowered = token.lower()
        if lowered in {"and", "or", "not", "true", "false"}:
            continue
        if token not in cleaned:
            cleaned.append(token)
    return " ".join(cleaned) if cleaned else "ephemeral"


def _shell_single_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def build_jenkins_cloud_init_user_data(
    *, jenkins_url: str, jenkins_node_name: str, jnlp_secret: str
) -> str:
    normalized_url = jenkins_url.rstrip("/")
    env_path_primary = "/usr/local/etc/jenkins-qemu/jenkins-agent.env"
    env_path_fallback = "/etc/jenkins-agent.env"
    env_file = textwrap.dedent(
        f"""\
        JENKINS_URL={_shell_single_quote(normalized_url)}
        JENKINS_NODE_NAME={_shell_single_quote(jenkins_node_name)}
        JENKINS_JNLP_SECRET={_shell_single_quote(jnlp_secret)}
        """
    )
    bootstrap_script = textwrap.dedent(
        """\
        #!/usr/bin/env bash
        set -eu

        ENV_PRIMARY=/usr/local/etc/jenkins-qemu/jenkins-agent.env
        ENV_FALLBACK=/etc/jenkins-agent.env
        BOOTSTRAP_LOG=/var/log/jenkins-agent-bootstrap.log

        stage() {
          local name="$1"
          local detail="${2:-}"
          local line="BOOTSTRAP_STAGE=${name} NODE=${JENKINS_NODE_NAME:-unknown} DETAIL=${detail}"
          printf '%s\n' "$line" | tee -a "$BOOTSTRAP_LOG"
          if [ -w /dev/console ]; then
            printf '%s\n' "$line" > /dev/console || true
          fi
        }

        stage "start"
        if [ -f "$ENV_PRIMARY" ]; then
          . "$ENV_PRIMARY"
          stage "env_loaded" "$ENV_PRIMARY"
        elif [ -f "$ENV_FALLBACK" ]; then
          . "$ENV_FALLBACK"
          stage "env_loaded" "$ENV_FALLBACK"
        else
          stage "env_missing"
          echo "missing jenkins agent env file" >&2
          exit 1
        fi

        AGENT_DIR=/opt/jenkins-agent
        AGENT_JAR="$AGENT_DIR/agent.jar"
        WORK_DIR=/home/jenkins
        LOG_FILE=/var/log/jenkins-agent.log

        mkdir -p "$AGENT_DIR" "$WORK_DIR"
        touch "$LOG_FILE"
        stage "dirs_ready" "$AGENT_DIR"

        if ! command -v java >/dev/null 2>&1; then
          stage "java_missing"
          echo "java not found in PATH" >&2
          exit 1
        fi
        stage "java_ok" "$(command -v java)"

        if command -v curl >/dev/null 2>&1; then
          curl -fsSL "$JENKINS_URL/jnlpJars/agent.jar" -o "$AGENT_JAR"
          stage "agent_download_ok" "curl"
        elif command -v fetch >/dev/null 2>&1; then
          fetch -o "$AGENT_JAR" "$JENKINS_URL/jnlpJars/agent.jar"
          stage "agent_download_ok" "fetch"
        else
          stage "downloader_missing"
          echo "neither curl nor fetch is available" >&2
          exit 1
        fi

        stage "agent_launch_start" "$JENKINS_URL"
        exec java -jar "$AGENT_JAR" \
          -url "$JENKINS_URL" \
          -name "$JENKINS_NODE_NAME" \
          -secret "$JENKINS_JNLP_SECRET" \
          -workDir "$WORK_DIR" \
          >> "$LOG_FILE" 2>&1
        """
    )
    return textwrap.dedent(
        f"""\
        #cloud-config
        write_files:
          - path: {env_path_primary}
            permissions: '0600'
            content: |
{textwrap.indent(env_file, "              ")}
          - path: {env_path_fallback}
            permissions: '0600'
            content: |
{textwrap.indent(env_file, "              ")}
          - path: /usr/local/bin/start-jenkins-inbound-agent.sh
            permissions: '0755'
            content: |
{textwrap.indent(bootstrap_script, "              ")}
        runcmd:
          - [ /usr/bin/env, bash, -c, "nohup /usr/local/bin/start-jenkins-inbound-agent.sh >> /var/log/jenkins-agent-bootstrap.log 2>&1 &" ]
        """
    )


class ProvisioningError(RuntimeError):
    def __init__(
        self,
        *,
        lease_id: str,
        vm_id: str,
        host_id: str,
        label: str,
        stage: str,
        detail: str,
    ):
        self.lease_id = lease_id
        self.vm_id = vm_id
        self.host_id = host_id
        self.label = label
        self.stage = stage
        self.detail = detail
        super().__init__(
            f"provisioning failed lease_id={lease_id} vm_id={vm_id} host_id={host_id} stage={stage}: {detail}"
        )


def choose_profile(label: str) -> str:
    if "large" in label:
        return "large"
    if "medium" in label:
        return "medium"
    return "small"


def create_lease(label: str) -> Lease:
    settings = get_settings()
    now = datetime.now(UTC).replace(tzinfo=None)
    lease_id = uuid.uuid4().hex
    vm_id = f"vm-{lease_id[:12]}"
    node_name = f"ephemeral-{lease_id[:12]}"
    return Lease(
        lease_id=lease_id,
        vm_id=vm_id,
        label=label,
        jenkins_node=node_name,
        state=LeaseState.REQUESTED.value,
        created_at=now,
        updated_at=now,
        connect_deadline=now + timedelta(seconds=settings.connect_deadline_sec),
        ttl_deadline=now + timedelta(seconds=settings.vm_ttl_sec),
    )


def provision_one(
    label: str,
    host_id: str,
    jenkins: JenkinsClient,
    node_agent: NodeAgentClient,
    base_image_id: str = "default",
    lease_id: str | None = None,
) -> str:
    settings = get_settings()
    lease = create_lease(label)
    if lease_id:
        lease.lease_id = lease_id
        lease.vm_id = f"vm-{lease_id[:12]}"
        lease.jenkins_node = f"ephemeral-{lease_id[:12]}"
    profile = NODE_PROFILES[choose_profile(label)]
    node_label = normalize_node_label(label)
    with session_scope() as session:
        existing = session.get(Lease, lease.lease_id)
        if existing and existing.state in {
            LeaseState.BOOTING.value,
            LeaseState.CONNECTED.value,
            LeaseState.RUNNING.value,
            LeaseState.TERMINATING.value,
            LeaseState.TERMINATED.value,
        }:
            return existing.lease_id

        lease.host_id = host_id
        persisted_lease = session.merge(lease)
        session.flush()
        write_event(
            session,
            "lease.created",
            {"label": label, "host_id": host_id},
            persisted_lease.lease_id,
        )

    try:
        jenkins.create_ephemeral_node(lease.jenkins_node, node_label)
        secret = jenkins.get_inbound_secret(lease.jenkins_node)
        user_data = build_jenkins_cloud_init_user_data(
            jenkins_url=settings.jenkins_url,
            jenkins_node_name=lease.jenkins_node,
            jnlp_secret=secret,
        )
        payload = {
            "vm_id": lease.vm_id,
            "label": label,
            "base_image_id": base_image_id,
            "overlay_path": f"/var/lib/jenkins-qemu/{lease.vm_id}.qcow2",
            "vcpu": profile["vcpu"],
            "ram_mb": profile["ram_mb"],
            "disk_gb": profile["disk_gb"],
            "lease_expires_at": lease.ttl_deadline.isoformat(),
            "connect_deadline": lease.connect_deadline.isoformat(),
            "jenkins_url": settings.jenkins_url,
            "jenkins_node_name": lease.jenkins_node,
            "jnlp_secret": secret,
            "cloud_init_user_data_b64": base64.b64encode(
                user_data.encode("utf-8")
            ).decode("ascii"),
            "metadata": {"lease_id": lease.lease_id},
        }
        node_agent.ensure_vm(lease.vm_id, payload)
        with session_scope() as session:
            db_lease = session.get(Lease, lease.lease_id)
            if db_lease:
                db_lease.state = LeaseState.BOOTING.value
                db_lease.updated_at = now_utc()
                write_event(
                    session, "lease.booting", {"host_id": host_id}, lease.lease_id
                )
        return lease.lease_id
    except Exception as exc:  # noqa: BLE001
        with session_scope() as session:
            db_lease = session.get(Lease, lease.lease_id)
            if db_lease:
                db_lease.state = LeaseState.FAILED.value
                db_lease.last_error = str(exc)
                db_lease.updated_at = now_utc()
                write_event(
                    session, "lease.failed", {"error": str(exc)}, lease.lease_id
                )
        try:
            jenkins.delete_node(lease.jenkins_node)
        except Exception:  # noqa: BLE001
            pass
        raise ProvisioningError(
            lease_id=lease.lease_id,
            vm_id=lease.vm_id,
            host_id=host_id,
            label=label,
            stage="ensure_vm",
            detail=str(exc),
        ) from exc
