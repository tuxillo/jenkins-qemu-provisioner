#!/usr/bin/env bash
set -euo pipefail

RELEASE_INDEX_URL=${RELEASE_INDEX_URL:-https://avalon.dragonflybsd.org/snapshots/x86_64/assets/releases/}
ROOT_PARENT=${ROOT_PARENT:-/build/jails}
SERVICE_SUBNET=${SERVICE_SUBNET:-10.200.0.0/24}
LOOPBACK_SUBNET=${LOOPBACK_SUBNET:-127.0.0.0/24}
RC_CONF_PATH=${RC_CONF_PATH:-/etc/rc.conf}
FSTAB_PATH=${FSTAB_PATH:-/etc/fstab}
ARTIFACT_NAME=${ARTIFACT_NAME:-}
JAIL_NAME=${JAIL_NAME:-}
JAIL_HOSTNAME=${JAIL_HOSTNAME:-}
JAIL_INTERFACE=${JAIL_INTERFACE:-}
JAIL_SERVICE_IP=${JAIL_SERVICE_IP:-}
JAIL_LOOPBACK_IP=${JAIL_LOOPBACK_IP:-}
JAIL_FSTAB_PATH=${JAIL_FSTAB_PATH:-}
DRY_RUN=${DRY_RUN:-0}
BOOTSTRAP_PKG=${BOOTSTRAP_PKG:-0}

declare -a PACKAGES=()
declare -a RESOLVERS=()

SCRIPT_NAME=$(basename "$0")

usage() {
  cat <<'EOF'
Usage: deploy/provision-dfly-jail.sh [options]

Prepare a DragonFly jail root from the latest published world artifact.

This script:
  - discovers the newest world artifact from the release index
  - creates a HAMMER2 PFS under /build/jails by default
  - mounts the PFS and persists the jail root mount in /etc/fstab.NAME
  - extracts the world tarball into the jail root
  - writes host /etc/rc.conf jail configuration
  - writes minimal jail-local /etc/rc.conf and /etc/resolv.conf
  - optionally bootstraps pkg and installs packages inside the prepared root

Options:
  --name NAME                 Jail name / HAMMER2 PFS label (required)
  --hostname HOSTNAME         Jail hostname (default: NAME)
  --interface IFACE           Host interface for rc.conf jail aliasing (required)
  --root-parent PATH          Parent directory on mounted HAMMER2 fs (default: /build/jails)
  --service-subnet CIDR       Auto-allocation subnet for jail service IPs (default: 10.200.0.0/24)
  --loopback-subnet CIDR      Auto-allocation subnet for jail loopback IPs (default: 127.0.0.0/24)
  --service-ip IP             Explicit non-loopback jail IP
  --loopback-ip IP            Explicit jail loopback IP
  --fstab-path PATH           Per-jail fstab path (default: /etc/fstab.NAME)
  --artifact-name NAME        Specific world artifact filename to use
  --release-index-url URL     Release index URL (default: avalon snapshots releases URL)
  --resolver IP               Nameserver to write into jail resolv.conf (repeatable)
  --bootstrap-pkg             Bootstrap pkg inside the jail root
  --package NAME              Package to install after pkg bootstrap (repeatable)
  --packages "a b c"          Space-separated package list
  --dry-run                   Print intended actions without changing the system
  -h, --help                  Show this help

Examples:
  deploy/provision-dfly-jail.sh --name web01 --interface em0
  deploy/provision-dfly-jail.sh --name ci01 --hostname ci01.example.net --interface em0 --bootstrap-pkg --packages "bash curl tmux"
EOF
}

log() {
  printf '[%s] %s\n' "$SCRIPT_NAME" "$*"
}

warn() {
  printf '[%s] warning: %s\n' "$SCRIPT_NAME" "$*" >&2
}

die() {
  printf '[%s] error: %s\n' "$SCRIPT_NAME" "$*" >&2
  exit 1
}

run_cmd() {
  if [ "$DRY_RUN" = "1" ]; then
    printf '[dry-run]'
    printf ' %q' "$@"
    printf '\n'
    return 0
  fi
  "$@"
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "required command not found: $1"
}

ip_to_int() {
  local ip=$1 a b c d
  IFS=. read -r a b c d <<<"$ip"
  printf '%u\n' $(( (a << 24) | (b << 16) | (c << 8) | d ))
}

int_to_ip() {
  local value=$1
  printf '%u.%u.%u.%u\n' \
    $(( (value >> 24) & 255 )) \
    $(( (value >> 16) & 255 )) \
    $(( (value >> 8) & 255 )) \
    $(( value & 255 ))
}

cidr_network_int() {
  local cidr=$1 ip=${cidr%/*} prefix=${cidr#*/}
  local value mask
  value=$(ip_to_int "$ip")
  if [ "$prefix" -eq 0 ]; then
    mask=0
  else
    mask=$(( (0xFFFFFFFF << (32 - prefix)) & 0xFFFFFFFF ))
  fi
  printf '%u\n' $(( value & mask ))
}

cidr_prefix() {
  printf '%s\n' "${1#*/}"
}

read_release_index() {
  fetch -qo - "$RELEASE_INDEX_URL"
}

latest_world_artifact() {
  local index_html candidates candidate
  index_html=$(read_release_index)
  mapfile -t candidates < <(
    printf '%s' "$index_html" \
      | grep -o 'DragonFly-x86_64-[^"]*\.world\.tar\.gz' \
      | sort -ur
  )
  [ ${#candidates[@]} -gt 0 ] || die "no world artifacts found at $RELEASE_INDEX_URL"
  for candidate in "${candidates[@]}"; do
    if printf '%s' "$index_html" | grep -q "${candidate}\.sha256"; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  die "no world artifact with matching sha256 found at $RELEASE_INDEX_URL"
}

find_hammer2_mount() {
  local target=$1
  local df_line special fstype mountpoint

  df_line=$(df -T "$target" 2>/dev/null | awk 'NR == 2 { print $1 "\t" $2 "\t" $NF }')
  [ -n "$df_line" ] || die "could not determine filesystem for $target"

  special=${df_line%%$'\t'*}
  df_line=${df_line#*$'\t'}
  fstype=${df_line%%$'\t'*}
  mountpoint=${df_line#*$'\t'}

  [ "$fstype" = "hammer2" ] || die "$target is not on a mounted HAMMER2 filesystem"
  printf '%s\t%s\n' "$special" "$mountpoint"
}

is_mountpoint() {
  local path=$1 fstype=${2:-}
  if [ -n "$fstype" ]; then
    mount -p | awk -v path="$path" -v fstype="$fstype" '$2 == path && $3 == fstype { found = 1 } END { exit(found ? 0 : 1) }'
  else
    mount -p | awk -v path="$path" '$2 == path { found = 1 } END { exit(found ? 0 : 1) }'
  fi
}

mounted_special_for_path() {
  local path=$1 fstype=${2:-}
  if [ -n "$fstype" ]; then
    mount -p | awk -v path="$path" -v fstype="$fstype" '$2 == path && $3 == fstype { print $1; exit }'
  else
    mount -p | awk -v path="$path" '$2 == path { print $1; exit }'
  fi
}

pfs_exists() {
  local hammer_mount=$1 label=$2
  hammer2 -s "$hammer_mount" pfs-list | grep -Eq "(^|[[:space:]])${label}([[:space:]]|$)"
}

ensure_parent_directory() {
  [ -d "$ROOT_PARENT" ] || die "root parent does not exist: $ROOT_PARENT"
  find_hammer2_mount "$ROOT_PARENT" >/dev/null
}

append_unique_line() {
  local file=$1 marker=$2 line=$3 tmp
  tmp=$(mktemp)
  if [ -f "$file" ]; then
    awk -v marker="$marker" 'index($0, marker) == 0 { print }' "$file" > "$tmp"
  fi
  printf '%s\n' "$line" >> "$tmp"
  if [ "$DRY_RUN" = "1" ]; then
    log "would update $file with marker $marker"
    sed -n '1,200p' "$tmp"
    rm -f "$tmp"
    return 0
  fi
  cp "$tmp" "$file"
  rm -f "$tmp"
}

replace_managed_block() {
  local file=$1 tag=$2 content=$3 tmp
  tmp=$(mktemp)
  [ -f "$file" ] || : > "$file"
  awk -v begin="# BEGIN ${tag}" -v end="# END ${tag}" '
    $0 == begin { skip = 1; next }
    $0 == end { skip = 0; next }
    !skip { print }
  ' "$file" > "$tmp"
  {
    cat "$tmp"
    printf '\n# BEGIN %s\n' "$tag"
    printf '%s\n' "$content"
    printf '# END %s\n' "$tag"
  } > "${tmp}.new"
  if [ "$DRY_RUN" = "1" ]; then
    log "would replace managed block $tag in $file"
    sed -n '1,240p' "${tmp}.new"
    rm -f "$tmp" "${tmp}.new"
    return 0
  fi
  cp "${tmp}.new" "$file"
  rm -f "$tmp" "${tmp}.new"
}

collect_used_ips() {
  local file=$1
  [ -f "$file" ] || return 0
  awk -F'"' '/^jail_.*_ip="/ { print $2 }' "$file" \
    | tr ',' '\n' \
    | sed '/^[[:space:]]*$/d' \
    | sort -u
}

allocate_ip_from_cidr() {
  local cidr=$1 skip_count=$2
  local network prefix size first last candidate_int candidate_ip
  network=$(cidr_network_int "$cidr")
  prefix=$(cidr_prefix "$cidr")
  [ "$prefix" -le 30 ] || die "CIDR too small for auto allocation: $cidr"
  size=$(( 1 << (32 - prefix) ))
  first=$(( network + skip_count ))
  last=$(( network + size - 2 ))

  while IFS= read -r candidate_ip; do
    if [ -n "$candidate_ip" ] && ! ip_in_use "$candidate_ip"; then
      USED_IPS+=("$candidate_ip")
    fi
  done < <(collect_used_ips "$RC_CONF_PATH")

  for ((candidate_int = first; candidate_int <= last; candidate_int++)); do
    candidate_ip=$(int_to_ip "$candidate_int")
    if ! ip_in_use "$candidate_ip"; then
      printf '%s\n' "$candidate_ip"
      return 0
    fi
  done
  die "no free addresses left in $cidr"
}

ip_in_use() {
  local candidate=$1 used_ip
  for used_ip in "${USED_IPS[@]:-}"; do
    if [ "$used_ip" = "$candidate" ]; then
      return 0
    fi
  done
  return 1
}

ensure_jail_pfs() {
  local mount_info hammer_special hammer_mount base_special jail_root pfs_special existing_special
  mount_info=$(find_hammer2_mount "$ROOT_PARENT")
  hammer_special=${mount_info%%$'\t'*}
  hammer_mount=${mount_info#*$'\t'}
  base_special=${hammer_special%@*}
  if [ "$base_special" = "$hammer_special" ]; then
    die "could not determine base HAMMER2 special from mount source: $hammer_special"
  fi

  JAIL_ROOT=${ROOT_PARENT%/}/${JAIL_NAME}
  PFS_SPECIAL="${base_special}@${JAIL_NAME}"

  if is_mountpoint "$JAIL_ROOT" hammer2; then
    existing_special=$(mounted_special_for_path "$JAIL_ROOT" hammer2)
    [ "$existing_special" = "$PFS_SPECIAL" ] || die "$JAIL_ROOT is already mounted from $existing_special, expected $PFS_SPECIAL"
  else
    if [ -d "$JAIL_ROOT" ] && [ -n "$(find "$JAIL_ROOT" -mindepth 1 -maxdepth 1 2>/dev/null)" ]; then
      die "$JAIL_ROOT exists and is not empty before HAMMER2 mount"
    fi
    run_cmd mkdir -p "$JAIL_ROOT"
    if ! pfs_exists "$hammer_mount" "$JAIL_NAME"; then
      run_cmd hammer2 -s "$hammer_mount" pfs-create "$JAIL_NAME"
    fi
    write_jail_fstab
    run_cmd mount_hammer2 "$PFS_SPECIAL" "$JAIL_ROOT"
  fi
}

write_jail_fstab() {
  append_unique_line "$JAIL_FSTAB_PATH" "# dfly-jail-provisioner:${JAIL_NAME}:root" "${PFS_SPECIAL} ${JAIL_ROOT} hammer2 rw 2 2 # dfly-jail-provisioner:${JAIL_NAME}:root"
}

artifact_url_for() {
  printf '%s%s\n' "$RELEASE_INDEX_URL" "$1"
}

write_resolv_conf() {
  local target=$1 tmp
  tmp=$(mktemp)
  if [ ${#RESOLVERS[@]} -gt 0 ]; then
    for resolver in "${RESOLVERS[@]}"; do
      printf 'nameserver %s\n' "$resolver" >> "$tmp"
    done
  elif [ -f /etc/resolv.conf ]; then
    cp /etc/resolv.conf "$tmp"
  else
    printf 'nameserver 1.1.1.1\n' > "$tmp"
  fi
  if [ "$DRY_RUN" = "1" ]; then
    log "would write $target"
    sed -n '1,80p' "$tmp"
    rm -f "$tmp"
    return 0
  fi
  cp "$tmp" "$target"
  rm -f "$tmp"
}

write_jail_local_rc_conf() {
  local target=$1 content
  content=$(cat <<EOF
hostname="${JAIL_HOSTNAME}"
rpcbind_enable="NO"
network_interfaces=""
EOF
)
  replace_managed_block "$target" "dfly-jail-provisioner:${JAIL_NAME}" "$content"
}

write_host_rc_conf() {
  local content
  content=$(cat <<EOF
jail_enable="YES"
jail_list="\${jail_list:+\${jail_list} }${JAIL_NAME}"
jail_${JAIL_NAME}_rootdir="${JAIL_ROOT}"
jail_${JAIL_NAME}_hostname="${JAIL_HOSTNAME}"
jail_${JAIL_NAME}_ip="${JAIL_LOOPBACK_IP},${JAIL_SERVICE_IP}"
jail_${JAIL_NAME}_interface="${JAIL_INTERFACE}"
jail_${JAIL_NAME}_mount_enable="YES"
jail_${JAIL_NAME}_fstab="${JAIL_FSTAB_PATH}"
jail_${JAIL_NAME}_devfs_enable="YES"
jail_${JAIL_NAME}_procfs_enable="NO"
EOF
)
  replace_managed_block "$RC_CONF_PATH" "dfly-jail-provisioner:${JAIL_NAME}" "$content"
}

download_and_extract_world() {
  local workdir artifact_url sha_url artifact_path sha_path expected_hash actual_hash
  workdir=$(mktemp -d)
  trap 'rm -rf "$workdir"' RETURN

  artifact_url=$(artifact_url_for "$ARTIFACT_NAME")
  sha_url=$(artifact_url_for "${ARTIFACT_NAME}.sha256")
  artifact_path="$workdir/$ARTIFACT_NAME"
  sha_path="$workdir/${ARTIFACT_NAME}.sha256"

  log "using world artifact: $ARTIFACT_NAME"
  log "artifact url: $artifact_url"

  run_cmd fetch -o "$artifact_path" "$artifact_url"
  run_cmd fetch -o "$sha_path" "$sha_url"

  if [ "$DRY_RUN" != "1" ]; then
    expected_hash=$(awk '{ print $1; exit }' "$sha_path")
    actual_hash=$(sha256 -q "$artifact_path")
    [ "$expected_hash" = "$actual_hash" ] || die "sha256 mismatch for $ARTIFACT_NAME"
  fi

  if [ "$DRY_RUN" != "1" ] && [ -n "$(find "$JAIL_ROOT" -mindepth 1 -maxdepth 1 ! -path "$JAIL_ROOT/dev" ! -path "$JAIL_ROOT/proc" 2>/dev/null)" ]; then
    die "$JAIL_ROOT is not empty before extraction"
  fi

  run_cmd mkdir -p "$JAIL_ROOT/dev" "$JAIL_ROOT/proc"
  run_cmd tar -xpf "$artifact_path" -C "$JAIL_ROOT"
}

bootstrap_pkg_and_install() {
  local mounted_here=0
  [ "$BOOTSTRAP_PKG" = "1" ] || [ ${#PACKAGES[@]} -gt 0 ] || return 0
  if ! is_mountpoint "$JAIL_ROOT/dev" devfs; then
    run_cmd mount -t devfs devfs "$JAIL_ROOT/dev"
    mounted_here=1
  fi
  if [ "$DRY_RUN" != "1" ] && [ "$mounted_here" = "1" ]; then
    trap 'umount "$JAIL_ROOT/dev" >/dev/null 2>&1 || true' RETURN
  fi
  run_cmd chroot "$JAIL_ROOT" /usr/bin/env ASSUME_ALWAYS_YES=yes /bin/sh -lc 'cd /usr && make pkg-bootstrap-force'
  if [ ${#PACKAGES[@]} -gt 0 ]; then
    run_cmd chroot "$JAIL_ROOT" /usr/bin/env ASSUME_ALWAYS_YES=yes PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin /usr/local/sbin/pkg install -y "${PACKAGES[@]}"
  fi
  if [ "$DRY_RUN" != "1" ] && [ "$mounted_here" = "1" ]; then
    trap - RETURN
    umount "$JAIL_ROOT/dev" >/dev/null 2>&1 || true
  fi
}

parse_args() {
  while [ $# -gt 0 ]; do
    case "$1" in
      --name)
        JAIL_NAME=$2
        shift 2
        ;;
      --hostname)
        JAIL_HOSTNAME=$2
        shift 2
        ;;
      --interface)
        JAIL_INTERFACE=$2
        shift 2
        ;;
      --root-parent)
        ROOT_PARENT=$2
        shift 2
        ;;
      --service-subnet)
        SERVICE_SUBNET=$2
        shift 2
        ;;
      --loopback-subnet)
        LOOPBACK_SUBNET=$2
        shift 2
        ;;
      --service-ip)
        JAIL_SERVICE_IP=$2
        shift 2
        ;;
      --loopback-ip)
        JAIL_LOOPBACK_IP=$2
        shift 2
        ;;
      --fstab-path)
        JAIL_FSTAB_PATH=$2
        shift 2
        ;;
      --artifact-name)
        ARTIFACT_NAME=$2
        shift 2
        ;;
      --release-index-url)
        RELEASE_INDEX_URL=$2
        shift 2
        ;;
      --resolver)
        RESOLVERS+=("$2")
        shift 2
        ;;
      --bootstrap-pkg)
        BOOTSTRAP_PKG=1
        shift
        ;;
      --package)
        PACKAGES+=("$2")
        shift 2
        ;;
      --packages)
        read -r -a more_packages <<<"$2"
        PACKAGES+=("${more_packages[@]}")
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
        die "unknown option: $1"
        ;;
    esac
  done
}

validate_inputs() {
  [ "$(uname -s)" = "DragonFly" ] || die "this script must run on DragonFly BSD"
  [ "$(id -u)" -eq 0 ] || die "this script must run as root"
  [ -n "$JAIL_NAME" ] || die "--name is required"
  [ -n "$JAIL_INTERFACE" ] || die "--interface is required"
  if [ -z "$JAIL_HOSTNAME" ]; then
    JAIL_HOSTNAME=$JAIL_NAME
  fi
  if [ -z "$JAIL_FSTAB_PATH" ]; then
    JAIL_FSTAB_PATH="/etc/fstab.${JAIL_NAME}"
  fi
  [ "$JAIL_SERVICE_IP" != "$JAIL_LOOPBACK_IP" ] || [ -z "$JAIL_SERVICE_IP" ] || die "service and loopback IPs must be different"
  require_command fetch
  require_command tar
  require_command sha256
  require_command hammer2
  require_command mount_hammer2
  require_command mount
  require_command chroot
  require_command awk
  require_command sed
  require_command stat
  require_command find
  ensure_parent_directory
}

print_summary() {
  printf '\n'
  log "prepared jail ${JAIL_NAME}"
  log "root: ${JAIL_ROOT}"
  log "artifact: ${ARTIFACT_NAME}"
  log "loopback ip: ${JAIL_LOOPBACK_IP}"
  log "service ip: ${JAIL_SERVICE_IP}"
  log "rc.conf block tag: dfly-jail-provisioner:${JAIL_NAME}"
  log "jail fstab: ${JAIL_FSTAB_PATH}"
  log "fstab marker: dfly-jail-provisioner:${JAIL_NAME}:root"
  if ! grep -Eq '^jail_default_allow_listen_override="YES"' "$RC_CONF_PATH" 2>/dev/null; then
    warn "jail_default_allow_listen_override is not set to YES in $RC_CONF_PATH; jailed listeners may conflict with host wildcard listeners"
  fi
  log "next step: service jail start ${JAIL_NAME}"
}

declare -a USED_IPS=()

main() {
  parse_args "$@"
  validate_inputs

  if [ -z "$ARTIFACT_NAME" ]; then
    ARTIFACT_NAME=$(latest_world_artifact)
  fi
  if [ -z "$JAIL_LOOPBACK_IP" ]; then
    JAIL_LOOPBACK_IP=$(allocate_ip_from_cidr "$LOOPBACK_SUBNET" 2)
  fi
  if ! ip_in_use "$JAIL_LOOPBACK_IP"; then
    USED_IPS+=("$JAIL_LOOPBACK_IP")
  fi
  if [ -z "$JAIL_SERVICE_IP" ]; then
    JAIL_SERVICE_IP=$(allocate_ip_from_cidr "$SERVICE_SUBNET" 2)
  fi
  [ "$JAIL_SERVICE_IP" != "$JAIL_LOOPBACK_IP" ] || die "service and loopback IPs must be different"

  ensure_jail_pfs
  download_and_extract_world
  run_cmd mkdir -p "$JAIL_ROOT/etc" "$JAIL_ROOT/dev" "$JAIL_ROOT/proc"
  write_jail_local_rc_conf "$JAIL_ROOT/etc/rc.conf"
  write_resolv_conf "$JAIL_ROOT/etc/resolv.conf"
  write_host_rc_conf
  bootstrap_pkg_and_install
  print_summary
}

main "$@"
