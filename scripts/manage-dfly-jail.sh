#!/usr/bin/env bash
set -euo pipefail

RELEASE_INDEX_URL=${RELEASE_INDEX_URL:-https://avalon.dragonflybsd.org/snapshots/x86_64/assets/releases/}
ROOT_PARENT=${ROOT_PARENT:-/build/jails}
SERVICE_SUBNET=${SERVICE_SUBNET:-10.200.0.0/24}
LOOPBACK_SUBNET=${LOOPBACK_SUBNET:-127.0.0.0/24}
PRIVATE_IFACE=${PRIVATE_IFACE:-lo1}
LOOPBACK_IFACE=${LOOPBACK_IFACE:-lo0}
LOOPBACK_ALIAS_MASK=${LOOPBACK_ALIAS_MASK:-0xff000000}
NETWORK_MODE=${NETWORK_MODE:-private-loopback}
SERVICE_IFACE=${SERVICE_IFACE:-}
RC_CONF_PATH=${RC_CONF_PATH:-/etc/rc.conf}
CACHE_DIR=${CACHE_DIR:-/var/cache/dfly-jails}
CACHE_KEEP=${CACHE_KEEP:-3}
BACKUP_DIR=${BACKUP_DIR:-/var/backups/dfly-jail-manager}
DRY_RUN=${DRY_RUN:-0}
NO_CACHE=${NO_CACHE:-0}

SCRIPT_NAME=$(basename "$0")
TAG_PREFIX="dfly-jail-manager"
LEGACY_TAG_PREFIX="dfly-jail-provisioner"

JAIL_NAME=${JAIL_NAME:-}
JAIL_HOSTNAME=${JAIL_HOSTNAME:-}
JAIL_INTERFACE=${JAIL_INTERFACE:-}
JAIL_SERVICE_IP=${JAIL_SERVICE_IP:-}
JAIL_LOOPBACK_IP=${JAIL_LOOPBACK_IP:-}
JAIL_FSTAB_PATH=${JAIL_FSTAB_PATH:-}
JAIL_ROOT=${JAIL_ROOT:-}
ARTIFACT_NAME=${ARTIFACT_NAME:-}
BOOTSTRAP_PKG=${BOOTSTRAP_PKG:-0}

declare -a PACKAGES=()
declare -a RESOLVERS=()
declare -a USED_IPS=()

usage() {
  cat <<'EOF'
Usage: scripts/manage-dfly-jail.sh <command> [options]

Manage DragonFly jails backed by HAMMER2 PFSes and world artifacts.

Commands:
  create   Create and configure a jail root from the latest world artifact
  destroy  Remove a managed jail, its config, and its HAMMER2 PFS
  start    Start a managed jail via service(8)
  stop     Stop a managed jail via service(8)
  status   Show status for one managed jail
  list     List all managed jails
  verify   Validate manager-owned jail state in rc.conf
  rebuild-network  Rebuild only the manager-owned network block

Global options:
  --dry-run                   Print intended actions without changing the system
  -h, --help                  Show this help

Create options:
  --name NAME                 Jail name / HAMMER2 PFS label (required)
  --hostname HOSTNAME         Jail hostname (default: NAME)
  --interface IFACE           Deprecated alias for --service-iface
  --network-mode MODE         private-loopback or interface-alias (default: private-loopback)
  --service-iface IFACE       Real interface for interface-alias mode
  --root-parent PATH          Parent directory on mounted HAMMER2 fs (default: /build/jails)
  --cache-dir PATH            Local world artifact cache directory (default: /var/cache/dfly-jails)
  --cache-keep N              Number of cached world artifacts to keep (default: 3)
  --no-cache                  Always download instead of reusing local cache
  --service-subnet CIDR       Auto-allocation subnet for jail service IPs (default: 10.200.0.0/24)
  --loopback-subnet CIDR      Auto-allocation subnet for jail loopback IPs (default: 127.0.0.0/24)
  --private-iface IFACE       Shared host interface for private jail subnet (default: lo1)
  --loopback-iface IFACE      Host loopback interface for jail-local IPs (default: lo0)
  --service-ip IP             Explicit non-loopback jail IP
  --loopback-ip IP            Explicit jail loopback IP
  --fstab-path PATH           Per-jail fstab path (default: /etc/fstab.NAME)
  --artifact-name NAME        Specific world artifact filename to use
  --release-index-url URL     Release index URL override
  --resolver IP               Nameserver to write into jail resolv.conf (repeatable)
  --bootstrap-pkg             Bootstrap pkg inside the jail root
  --package NAME              Package to install after pkg bootstrap (repeatable)
  --packages "a b c"          Space-separated package list

Other command options:
  --name NAME                 Required for destroy, start, stop, and status

Examples:
  scripts/manage-dfly-jail.sh create --name web01 --bootstrap-pkg --packages "bash curl tmux"
  scripts/manage-dfly-jail.sh create --name web02 --network-mode interface-alias --service-iface re0 --service-ip 192.168.5.50
  scripts/manage-dfly-jail.sh status --name web01
  scripts/manage-dfly-jail.sh destroy --name web01
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

current_tag() {
  printf '%s:%s\n' "$TAG_PREFIX" "$1"
}

legacy_tag() {
  printf '%s:%s\n' "$LEGACY_TAG_PREFIX" "$1"
}

network_tag() {
  printf '%s:network\n' "$TAG_PREFIX"
}

normalize_network_mode() {
  case "$1" in
    private-loopback|interface-alias)
      printf '%s\n' "$1"
      ;;
    "")
      printf 'private-loopback\n'
      ;;
    *)
      die "unknown network mode: $1"
      ;;
  esac
}

validate_jail_name() {
  local name=$1
  [ -n "$name" ] || die "jail name is required"
  [ "$name" != "network" ] || die "jail name 'network' is reserved"
  case "$name" in
    *[!A-Za-z0-9_]*)
      die "invalid jail name '$name': use only letters, numbers, and underscores"
      ;;
  esac
}

current_root_marker() {
  printf '# %s:root\n' "$(current_tag "$1")"
}

legacy_root_marker() {
  printf '# %s:root\n' "$(legacy_tag "$1")"
}

require_dragonfly_root() {
  [ "$(uname -s)" = "DragonFly" ] || die "this script must run on DragonFly BSD"
  [ "$(id -u)" -eq 0 ] || die "this script must run as root"
}

ip_to_int() {
  local ip=$1 a b c d
  IFS=. read -r a b c d <<<"$ip"
  printf '%u\n' $(((a << 24) | (b << 16) | (c << 8) | d))
}

int_to_ip() {
  local value=$1
  printf '%u.%u.%u.%u\n' \
    $(((value >> 24) & 255)) \
    $(((value >> 16) & 255)) \
    $(((value >> 8) & 255)) \
    $((value & 255))
}

cidr_network_int() {
  local cidr=$1 ip=${cidr%/*} prefix=${cidr#*/}
  local value mask
  value=$(ip_to_int "$ip")
  if [ "$prefix" -eq 0 ]; then
    mask=0
  else
    mask=$(((0xFFFFFFFF << (32 - prefix)) & 0xFFFFFFFF))
  fi
  printf '%u\n' $((value & mask))
}

cidr_prefix() {
  printf '%s\n' "${1#*/}"
}

increment_ipv4() {
  local ip=$1 a b c d
  IFS=. read -r a b c d <<<"$ip"
  d=$((d + 1))
  if [ "$d" -gt 255 ]; then
    d=0
    c=$((c + 1))
  fi
  if [ "$c" -gt 255 ]; then
    c=0
    b=$((b + 1))
  fi
  if [ "$b" -gt 255 ]; then
    b=0
    a=$((a + 1))
  fi
  [ "$a" -le 255 ] || die "IPv4 increment overflow for $ip"
  printf '%s.%s.%s.%s\n' "$a" "$b" "$c" "$d"
}

cidr_first_host_ip() {
  increment_ipv4 "${1%/*}"
}

cidr_netmask_hex() {
  local prefix mask
  prefix=$(cidr_prefix "$1")
  if [ "$prefix" -eq 0 ]; then
    mask=0
  else
    mask=$(((0xFFFFFFFF << (32 - prefix)) & 0xFFFFFFFF))
  fi
  printf '0x%08x\n' "$mask"
}

read_release_index() {
  fetch -qo - "$RELEASE_INDEX_URL"
}

latest_world_artifact() {
  local index_html candidates=() candidate
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

df_fs_info() {
  local target=$1
  df -T "$target" 2>/dev/null | awk 'NR == 2 { print $1 "\t" $2 "\t" $NF }'
}

find_hammer2_mount() {
  local target=$1
  local df_line special fstype mountpoint
  df_line=$(df_fs_info "$target")
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
  local df_line actual_fstype mountpoint
  [ -e "$path" ] || return 1
  df_line=$(df_fs_info "$path") || return 1
  [ -n "$df_line" ] || return 1
  df_line=${df_line#*$'\t'}
  actual_fstype=${df_line%%$'\t'*}
  mountpoint=${df_line#*$'\t'}
  [ "$mountpoint" = "$path" ] || return 1
  if [ -n "$fstype" ]; then
    [ "$actual_fstype" = "$fstype" ] || return 1
  fi
}

mounted_special_for_path() {
  local path=$1
  local df_line
  df_line=$(df_fs_info "$path") || return 1
  [ -n "$df_line" ] || return 1
  printf '%s\n' "${df_line%%$'\t'*}"
}

pfs_exists() {
  local hammer_mount=$1 label=$2
  hammer2 -s "$hammer_mount" pfs-list | grep -Eq "(^|[[:space:]])${label}([[:space:]]|$)"
}

ensure_parent_directory() {
  [ -d "$ROOT_PARENT" ] || die "root parent does not exist: $ROOT_PARENT"
  find_hammer2_mount "$ROOT_PARENT" >/dev/null
}

ensure_cache_directory() {
  [ "$NO_CACHE" = "1" ] && return 0
  if [ "$DRY_RUN" = "1" ]; then
    log "would ensure cache directory $CACHE_DIR"
    return 0
  fi
  mkdir -p "$CACHE_DIR"
}

replace_managed_block() {
  local file=$1 tag=$2 content=$3 tmp
  tmp=$(mktemp)
  [ -f "$file" ] || : > "$file"
  awk -v begin="# BEGIN ${tag}" -v end="# END ${tag}" '
    {
      line = $0
      sub(/[[:space:]]+$/, "", line)
    }
    line == begin { skip = 1; next }
    line == end { skip = 0; next }
    !skip { print }
  ' "$file" > "$tmp"
  normalize_managed_file "$tmp" "$tag" "$content" > "${tmp}.new"
  if [ "$DRY_RUN" = "1" ]; then
    log "would replace managed block $tag in $file"
    sed -n '1,240p' "${tmp}.new"
    rm -f "$tmp" "${tmp}.new"
    return 0
  fi
  backup_file_if_exists "$file"
  cp "${tmp}.new" "$file"
  rm -f "$tmp" "${tmp}.new"
}

normalize_managed_file() {
  local source_file=$1 tag=${2:-} content=${3:-} tmp_output
  tmp_output=$(mktemp)
  {
    cat "$source_file"
    if [ -n "$content" ]; then
      if [ -s "$source_file" ]; then
        printf '\n'
      fi
      printf '# BEGIN %s\n' "$tag"
      printf '%s\n' "$content"
      printf '# END %s\n' "$tag"
    fi
  } | awk '
    BEGIN { blank = 0; emitted = 0 }
    /^[[:space:]]*$/ {
      if (emitted && !blank) {
        print ""
        blank = 1
      }
      next
    }
    { print; blank = 0; emitted = 1 }
  ' > "$tmp_output"
  cat "$tmp_output"
  rm -f "$tmp_output"
}

remove_managed_blocks_for_name() {
  local file=$1 name=$2 tmp current legacy
  current=$(current_tag "$name")
  legacy=$(legacy_tag "$name")
  tmp=$(mktemp)
  [ -f "$file" ] || return 0
  awk -v cbegin="# BEGIN ${current}" -v cend="# END ${current}" -v lbegin="# BEGIN ${legacy}" -v lend="# END ${legacy}" '
    {
      line = $0
      sub(/[[:space:]]+$/, "", line)
    }
    line == cbegin || line == lbegin { skip = 1; next }
    line == cend || line == lend { skip = 0; next }
    !skip { print }
  ' "$file" > "$tmp"
  if cmp -s "$file" "$tmp"; then
    rm -f "$tmp"
    return 0
  fi
  if [ "$DRY_RUN" = "1" ]; then
    log "would remove managed blocks for $name from $file"
    normalize_managed_file "$tmp" | sed -n '1,240p'
    rm -f "$tmp"
    return 0
  fi
  normalize_managed_file "$tmp" > "${tmp}.new"
  backup_file_if_exists "$file"
  cp "${tmp}.new" "$file"
  rm -f "$tmp"
  rm -f "${tmp}.new"
}

set_root_fstab_line() {
  local file=$1 line=$2 tmp current legacy
  current=$(current_root_marker "$JAIL_NAME")
  legacy=$(legacy_root_marker "$JAIL_NAME")
  tmp=$(mktemp)
  if [ -f "$file" ]; then
    awk -v current="$current" -v legacy="$legacy" 'index($0, current) == 0 && index($0, legacy) == 0 { print }' "$file" > "$tmp"
  fi
  printf '%s\n' "$line" >> "$tmp"
  if [ "$DRY_RUN" = "1" ]; then
    log "would update $file with managed root mount"
    sed -n '1,160p' "$tmp"
    rm -f "$tmp"
    return 0
  fi
  backup_file_if_exists "$file"
  cp "$tmp" "$file"
  rm -f "$tmp"
}

remove_root_fstab_line() {
  local file=$1 tmp current legacy
  current=$(current_root_marker "$JAIL_NAME")
  legacy=$(legacy_root_marker "$JAIL_NAME")
  [ -f "$file" ] || return 0
  tmp=$(mktemp)
  awk -v current="$current" -v legacy="$legacy" 'index($0, current) == 0 && index($0, legacy) == 0 { print }' "$file" > "$tmp"
  if [ "$DRY_RUN" = "1" ]; then
    log "would remove managed root mount from $file"
    sed -n '1,160p' "$tmp"
    rm -f "$tmp"
    return 0
  fi
  backup_file_if_exists "$file"
  if [ ! -s "$tmp" ]; then
    rm -f "$file" "$tmp"
  else
    cp "$tmp" "$file"
    rm -f "$tmp"
  fi
}

managed_jail_names() {
  managed_jail_records | awk -F'\t' '{ print $1 }'
}

rc_conf_value() {
  local key=$1
  [ -f "$RC_CONF_PATH" ] || return 1
  awk -F'"' -v key="$key" '$1 == key"=" { print $2; exit }' "$RC_CONF_PATH"
}

ensure_backup_dir() {
  if [ "$DRY_RUN" = "1" ]; then
    log "would ensure backup directory $BACKUP_DIR"
    return 0
  fi
  mkdir -p "$BACKUP_DIR"
}

backup_file_if_exists() {
  local file=$1 timestamp base backup_path
  [ -f "$file" ] || return 0
  ensure_backup_dir
  timestamp=$(date +%Y%m%d-%H%M%S)
  base=$(basename "$file")
  backup_path="$BACKUP_DIR/${base}.${timestamp}"
  if [ "$DRY_RUN" = "1" ]; then
    log "would back up $file to $backup_path"
    return 0
  fi
  cp "$file" "$backup_path"
}

managed_jail_records() {
  [ -f "$RC_CONF_PATH" ] || return 0
  local line in_block=0 block_name="" network_mode="" service_iface="" rootdir="" hostname="" ip_pair="" fstab="" loopback_ip="" service_ip=""
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in
      '# BEGIN dfly-jail-manager:'*|'# BEGIN dfly-jail-provisioner:'*)
      if [ "$in_block" = "1" ] && [ -n "$block_name" ] && [ "$block_name" != "network" ]; then
        IFS=, read -r loopback_ip service_ip <<<"$ip_pair"
        printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$block_name" "$network_mode" "$service_iface" "$rootdir" "$hostname" "$loopback_ip" "$service_ip" "$fstab"
      fi
      in_block=1
      block_name=${line##*:}
      block_name=${block_name%${block_name##*[![:space:]]}}
      network_mode=""
      service_iface=""
      rootdir=""
      hostname=""
      ip_pair=""
      fstab=""
      continue
        ;;
    esac

    case "$line" in
      '# END dfly-jail-manager:'*|'# END dfly-jail-provisioner:'*)
      if [ "$in_block" = "1" ] && [ -n "$block_name" ] && [ "$block_name" != "network" ]; then
        IFS=, read -r loopback_ip service_ip <<<"$ip_pair"
        printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$block_name" "$network_mode" "$service_iface" "$rootdir" "$hostname" "$loopback_ip" "$service_ip" "$fstab"
      fi
      in_block=0
      block_name=""
      continue
        ;;
    esac

    [ "$in_block" = "1" ] || continue

    case "$line" in
      '# network_mode='*)
        network_mode=${line#'# network_mode='}
        continue
        ;;
      '# service_iface='*)
        service_iface=${line#'# service_iface='}
        continue
        ;;
      jail_*_rootdir=*)
        rootdir=${line#*\"}
        rootdir=${rootdir%%\"*}
        continue
        ;;
      jail_*_hostname=*)
        hostname=${line#*\"}
        hostname=${hostname%%\"*}
        continue
        ;;
      jail_*_ip=*)
        ip_pair=${line#*\"}
        ip_pair=${ip_pair%%\"*}
        continue
        ;;
      jail_*_fstab=*)
        fstab=${line#*\"}
        fstab=${fstab%%\"*}
      continue
        ;;
    esac
  done < "$RC_CONF_PATH"
  return 0
}

managed_jail_record_by_name() {
  local name=$1
  local record record_name
  while IFS= read -r record; do
    [ -n "$record" ] || continue
    IFS=$'\t' read -r record_name _rest <<<"$record"
    if [ "$record_name" = "$name" ]; then
      printf '%s\n' "$record"
      return 0
    fi
  done < <(managed_jail_records)
  return 1
}

validate_managed_state() {
  local -A seen_names=() seen_loopbacks=() seen_services=()
  local line name mode iface rootdir hostname loopback_ip service_ip fstab
  while IFS=$'\t' read -r name mode iface rootdir hostname loopback_ip service_ip fstab; do
    [ -n "$name" ] || continue
    validate_jail_name "$name"
    if [ -n "${seen_names[$name]:-}" ]; then
      die "duplicate managed jail block found for $name"
    fi
    seen_names[$name]=1
    mode=$(normalize_network_mode "$mode")
    [ -n "$rootdir" ] || die "managed jail $name is missing rootdir"
    [ -n "$hostname" ] || die "managed jail $name is missing hostname"
    [ -n "$loopback_ip" ] || die "managed jail $name is missing loopback IP"
    [ -n "$service_ip" ] || die "managed jail $name is missing service IP"
    [ -n "$fstab" ] || die "managed jail $name is missing fstab path"
    if [ -n "${seen_loopbacks[$loopback_ip]:-}" ]; then
      die "managed jail config is inconsistent: $name and ${seen_loopbacks[$loopback_ip]} both use $loopback_ip"
    fi
    seen_loopbacks[$loopback_ip]=$name
    if [ -n "${seen_services[$service_ip]:-}" ]; then
      die "managed jail config is inconsistent: $name and ${seen_services[$service_ip]} both use $service_ip"
    fi
    seen_services[$service_ip]=$name
    case "$mode" in
      private-loopback)
        [ -n "$iface" ] || true
        ;;
      interface-alias)
        [ -n "$iface" ] || die "managed jail $name is missing service interface metadata"
        ;;
    esac
  done < <(managed_jail_records)
}

managed_block_metadata() {
  local name=$1 key=$2 current legacy
  current=$(current_tag "$name")
  legacy=$(legacy_tag "$name")
  [ -f "$RC_CONF_PATH" ] || return 1
  awk -v cbegin="# BEGIN ${current}" -v cend="# END ${current}" -v lbegin="# BEGIN ${legacy}" -v lend="# END ${legacy}" -v key="# ${key}=" '
    {
      line = $0
      sub(/[[:space:]]+$/, "", line)
    }
    line == cbegin || line == lbegin { in_block = 1; next }
    line == cend || line == lend { in_block = 0; next }
    in_block && index(line, key) == 1 { print substr(line, length(key) + 1); exit }
  ' "$RC_CONF_PATH"
}

managed_block_content() {
  local tag=$1
  [ -f "$RC_CONF_PATH" ] || return 0
  awk -v begin="# BEGIN ${tag}" -v end="# END ${tag}" '
    {
      line = $0
      sub(/[[:space:]]+$/, "", line)
    }
    line == begin {
      if (seen++) {
        exit 2
      }
      in_block = 1
      next
    }
    line == end {
      in_block = 0
      exit
    }
    in_block { print line }
  ' "$RC_CONF_PATH"
  case $? in
    0)
      return 0
      ;;
    2)
      die "duplicate manager-owned block found for ${tag}"
      ;;
    *)
      return 1
      ;;
  esac
}

service_iface_mask_hex() {
  local iface=$1 mask
  mask=$(ifconfig "$iface" 2>/dev/null | awk '/^[[:space:]]*inet / { for (i = 1; i <= NF; i++) if ($i == "netmask") { print $(i + 1); exit } }')
  [ -n "$mask" ] || die "could not determine IPv4 netmask for interface $iface"
  printf '%s\n' "$mask"
}

resolve_existing_config() {
  local record
  record=$(managed_jail_record_by_name "$JAIL_NAME" || true)
  if [ -z "$record" ]; then
    JAIL_ROOT=
    JAIL_HOSTNAME=
    JAIL_INTERFACE=
    JAIL_FSTAB_PATH=
    JAIL_LOOPBACK_IP=
    JAIL_SERVICE_IP=
    NETWORK_MODE=private-loopback
    SERVICE_IFACE=
    return 0
  fi
  IFS=$'\t' read -r _record_name NETWORK_MODE SERVICE_IFACE JAIL_ROOT JAIL_HOSTNAME JAIL_LOOPBACK_IP JAIL_SERVICE_IP JAIL_FSTAB_PATH <<<"$record"
  NETWORK_MODE=$(normalize_network_mode "$NETWORK_MODE")
  if [ -z "$JAIL_FSTAB_PATH" ]; then
    JAIL_FSTAB_PATH="/etc/fstab.${JAIL_NAME}"
  fi
  if [ -z "$SERVICE_IFACE" ] && [ "$NETWORK_MODE" = "private-loopback" ]; then
    SERVICE_IFACE=$PRIVATE_IFACE
  fi
}

managed_network_content() {
  local name content="" prefix_content="" loopback_mask private_gateway private_mask mode iface service_mask rootdir hostname loopback_ip service_ip fstab
  local saved_name saved_root saved_host saved_iface saved_service_ip saved_loopback_ip saved_fstab saved_mode saved_service_iface
  local -A alias_indexes=() private_iface_seen=()
  local -a cloned_ifaces=()
  saved_name=$JAIL_NAME
  saved_root=$JAIL_ROOT
  saved_host=$JAIL_HOSTNAME
  saved_iface=$JAIL_INTERFACE
  saved_service_ip=$JAIL_SERVICE_IP
  saved_loopback_ip=$JAIL_LOOPBACK_IP
  saved_fstab=$JAIL_FSTAB_PATH
  saved_mode=$NETWORK_MODE
  saved_service_iface=$SERVICE_IFACE
  private_gateway=$(cidr_first_host_ip "$SERVICE_SUBNET")
  private_mask=$(cidr_netmask_hex "$SERVICE_SUBNET")
  loopback_mask=$LOOPBACK_ALIAS_MASK

  while IFS=$'\t' read -r name mode iface rootdir hostname loopback_ip service_ip fstab; do
    [ -n "$name" ] || continue
    mode=$(normalize_network_mode "$mode")
    if [ -z "$iface" ] && [ "$mode" = "private-loopback" ]; then
      iface=$PRIVATE_IFACE
    fi
    [ -n "$loopback_ip" ] || continue
    [ -n "$service_ip" ] || continue

    if [ -z "${alias_indexes[$LOOPBACK_IFACE]:-}" ]; then
      alias_indexes[$LOOPBACK_IFACE]=0
    fi
    content+="ifconfig_${LOOPBACK_IFACE}_alias${alias_indexes[$LOOPBACK_IFACE]}=\"inet ${loopback_ip} netmask ${loopback_mask}\""$'\n'
    alias_indexes[$LOOPBACK_IFACE]=$((alias_indexes[$LOOPBACK_IFACE] + 1))

    case "$mode" in
      private-loopback)
        [ -n "$iface" ] || iface=$PRIVATE_IFACE
        if [ "$iface" != "$LOOPBACK_IFACE" ] && [ -z "${private_iface_seen[$iface]:-}" ]; then
          cloned_ifaces+=("$iface")
          prefix_content+="ifconfig_${iface}=\"inet ${private_gateway} netmask ${private_mask}\""$'\n'
          private_iface_seen[$iface]=1
        fi
        if [ -z "${alias_indexes[$iface]:-}" ]; then
          alias_indexes[$iface]=0
        fi
        content+="ifconfig_${iface}_alias${alias_indexes[$iface]}=\"inet ${service_ip} netmask ${private_mask}\""$'\n'
        alias_indexes[$iface]=$((alias_indexes[$iface] + 1))
        ;;
      interface-alias)
        [ -n "$iface" ] || die "missing service interface metadata for jail $name"
        service_mask=$(service_iface_mask_hex "$iface")
        if [ -z "${alias_indexes[$iface]:-}" ]; then
          alias_indexes[$iface]=0
        fi
        content+="ifconfig_${iface}_alias${alias_indexes[$iface]}=\"inet ${service_ip} netmask ${service_mask}\""$'\n'
        alias_indexes[$iface]=$((alias_indexes[$iface] + 1))
        ;;
    esac
  done < <(managed_jail_records)

  if [ ${#cloned_ifaces[@]} -gt 0 ]; then
    prefix_content="cloned_interfaces=\"\${cloned_interfaces:+\${cloned_interfaces} }${cloned_ifaces[*]}\""$'\n'"${prefix_content}"
  fi
  content="${prefix_content}${content}"

  JAIL_NAME=$saved_name
  JAIL_ROOT=$saved_root
  JAIL_HOSTNAME=$saved_host
  JAIL_INTERFACE=$saved_iface
  JAIL_SERVICE_IP=$saved_service_ip
  JAIL_LOOPBACK_IP=$saved_loopback_ip
  JAIL_FSTAB_PATH=$saved_fstab
  NETWORK_MODE=$saved_mode
  SERVICE_IFACE=$saved_service_iface

  printf '%s' "$content"
}

rebuild_managed_network_block() {
  local content
  validate_managed_state
  content=$(managed_network_content)
  if [ -n "$content" ]; then
    replace_managed_block "$RC_CONF_PATH" "$(network_tag)" "$content"
  else
    remove_managed_blocks_for_name "$RC_CONF_PATH" network
  fi
  validate_managed_state
  validate_managed_network_block
}

iface_has_ipv4() {
  local iface=$1 ip=$2
  ifconfig "$iface" 2>/dev/null | grep -Eq "inet ${ip}([[:space:]]|$)"
}

host_iface_for_ipv4() {
  local ip=$1
  ifconfig -a | awk -v ip="$ip" '
    /^[[:alnum:]_][^:[:space:]]*:/ {
      iface = $1
      sub(":$", "", iface)
      next
    }
    /^[[:space:]]*inet / {
      for (i = 1; i <= NF; i++) {
        if ($i == "inet" && $(i + 1) == ip) {
          print iface
          exit
        }
      }
    }
  '
}

managed_jail_has_ip_on_iface() {
  local expected_iface=$1 expected_ip=$2 name mode iface rootdir hostname loopback_ip service_ip fstab
  while IFS=$'\t' read -r name mode iface rootdir hostname loopback_ip service_ip fstab; do
    [ -n "$name" ] || continue
    mode=$(normalize_network_mode "$mode")
    if [ -n "$loopback_ip" ] && [ "$expected_iface" = "$LOOPBACK_IFACE" ] && [ "$expected_ip" = "$loopback_ip" ]; then
      return 0
    fi
    case "$mode" in
      private-loopback)
        [ -n "$iface" ] || iface=$PRIVATE_IFACE
        ;;
      interface-alias)
        [ -n "$iface" ] || continue
        ;;
    esac
    if [ -n "$service_ip" ] && [ "$expected_iface" = "$iface" ] && [ "$expected_ip" = "$service_ip" ]; then
      return 0
    fi
  done < <(managed_jail_records)
  return 1
}

validate_managed_network_block() {
  local expected actual
  expected=$(managed_network_content)
  actual=$(managed_block_content "$(network_tag)")
  if [ "$expected" != "$actual" ]; then
    die "manager-owned network block is out of sync with managed jail metadata; run rebuild-network"
  fi
}

assert_ip_not_configured_on_host() {
  local ip=$1 role=$2 iface
  [ -n "$ip" ] || return 0
  iface=$(host_iface_for_ipv4 "$ip" || true)
  if [ -n "$iface" ]; then
    die "${role} IP is already configured on host interface ${iface}: ${ip}"
  fi
}

ensure_private_iface_live() {
  local iface=${1:-$PRIVATE_IFACE} private_gateway private_mask
  private_gateway=$(cidr_first_host_ip "$SERVICE_SUBNET")
  private_mask=$(cidr_netmask_hex "$SERVICE_SUBNET")
  if [ "$iface" != "$LOOPBACK_IFACE" ]; then
    if ! ifconfig "$iface" >/dev/null 2>&1; then
      run_cmd ifconfig "$iface" create
    fi
    if ! iface_has_ipv4 "$iface" "$private_gateway"; then
      run_cmd ifconfig "$iface" inet "$private_gateway" netmask "$private_mask"
    fi
  fi
}

ensure_alias_live() {
  local iface=$1 ip=$2 mask=$3
  local existing_iface
  existing_iface=$(host_iface_for_ipv4 "$ip" || true)
  if [ -n "$existing_iface" ]; then
    if [ "$existing_iface" != "$iface" ]; then
      die "refusing to manage ${ip} on ${iface}: address is already configured on ${existing_iface}"
    fi
    if managed_jail_has_ip_on_iface "$iface" "$ip"; then
      return 0
    fi
    die "refusing to manage ${ip} on ${iface}: address exists but is not declared in managed jail metadata"
  fi
  run_cmd ifconfig "$iface" alias "$ip" netmask "$mask"
}

remove_alias_live() {
  local iface=$1 ip=$2
  local existing_iface
  existing_iface=$(host_iface_for_ipv4 "$ip" || true)
  if [ -z "$existing_iface" ]; then
    return 0
  fi
  if [ "$existing_iface" != "$iface" ]; then
    warn "not removing ${ip} from ${iface}: address is configured on ${existing_iface}"
    return 0
  fi
  if ! managed_jail_has_ip_on_iface "$iface" "$ip"; then
    warn "not removing ${ip} from ${iface}: address is not declared in managed jail metadata"
    return 0
  fi
  if ! run_cmd ifconfig "$iface" -alias "$ip"; then
    warn "unable to remove ${ip} from ${iface}; address remained configured"
  fi
}

ensure_runtime_network_for_jail() {
  local mode effective_service_iface private_mask loopback_mask service_mask
  mode=$(normalize_network_mode "$NETWORK_MODE")
  effective_service_iface=$SERVICE_IFACE
  loopback_mask=$LOOPBACK_ALIAS_MASK
  ensure_alias_live "$LOOPBACK_IFACE" "$JAIL_LOOPBACK_IP" "$loopback_mask"
  case "$mode" in
    private-loopback)
      [ -n "$effective_service_iface" ] || effective_service_iface=$PRIVATE_IFACE
      ensure_private_iface_live "$effective_service_iface"
      private_mask=$(cidr_netmask_hex "$SERVICE_SUBNET")
      ensure_alias_live "$effective_service_iface" "$JAIL_SERVICE_IP" "$private_mask"
      ;;
    interface-alias)
      [ -n "$effective_service_iface" ] || die "interface-alias mode requires a service interface"
      service_mask=$(service_iface_mask_hex "$effective_service_iface")
      ensure_alias_live "$effective_service_iface" "$JAIL_SERVICE_IP" "$service_mask"
      ;;
  esac
}

remove_runtime_network_for_jail() {
  local mode effective_service_iface
  mode=$(normalize_network_mode "$NETWORK_MODE")
  effective_service_iface=$SERVICE_IFACE
  remove_alias_live "$LOOPBACK_IFACE" "$JAIL_LOOPBACK_IP"
  case "$mode" in
    private-loopback)
      [ -n "$effective_service_iface" ] || effective_service_iface=$PRIVATE_IFACE
      remove_alias_live "$effective_service_iface" "$JAIL_SERVICE_IP"
      ;;
    interface-alias)
      [ -n "$effective_service_iface" ] || return 0
      remove_alias_live "$effective_service_iface" "$JAIL_SERVICE_IP"
      ;;
  esac
}

verify_cached_artifact() {
  local artifact_path=$1 sha_path=$2 expected_hash actual_hash
  [ -f "$artifact_path" ] || return 1
  [ -f "$sha_path" ] || return 1
  expected_hash=$(awk '{ print $1; exit }' "$sha_path")
  [ -n "$expected_hash" ] || return 1
  actual_hash=$(sha256 -q "$artifact_path")
  [ "$expected_hash" = "$actual_hash" ]
}

prune_cached_artifacts() {
  local cache_keep=$1
  local -a cached_worlds=()
  local world_path artifact_basename
  [ "$NO_CACHE" = "1" ] && return 0
  [ "$DRY_RUN" = "1" ] && return 0
  [ -d "$CACHE_DIR" ] || return 0
  mapfile -t cached_worlds < <(find "$CACHE_DIR" -maxdepth 1 -type f -name 'DragonFly-x86_64-*.world.tar.gz' | sort -r)
  if [ ${#cached_worlds[@]} -le "$cache_keep" ]; then
    return 0
  fi
  for world_path in "${cached_worlds[@]:cache_keep}"; do
    artifact_basename=$(basename "$world_path")
    rm -f "$world_path" "$CACHE_DIR/${artifact_basename}.sha256"
  done
}

prepare_cached_artifact() {
  local artifact_url=$1 sha_url=$2 workdir=$3
  local artifact_path sha_path cache_artifact_path cache_sha_path
  artifact_path="$workdir/$ARTIFACT_NAME"
  sha_path="$workdir/${ARTIFACT_NAME}.sha256"
  cache_artifact_path="$CACHE_DIR/$ARTIFACT_NAME"
  cache_sha_path="$CACHE_DIR/${ARTIFACT_NAME}.sha256"
  if [ "$NO_CACHE" = "1" ]; then
    run_cmd fetch -o "$artifact_path" "$artifact_url"
    run_cmd fetch -o "$sha_path" "$sha_url"
    return 0
  fi
  ensure_cache_directory
  if [ "$DRY_RUN" != "1" ] && verify_cached_artifact "$cache_artifact_path" "$cache_sha_path"; then
    log "using cached artifact: $cache_artifact_path"
    cp "$cache_artifact_path" "$artifact_path"
    cp "$cache_sha_path" "$sha_path"
    return 0
  fi
  if [ "$DRY_RUN" != "1" ] && { [ -f "$cache_artifact_path" ] || [ -f "$cache_sha_path" ]; }; then
    log "refreshing stale cached artifact: $cache_artifact_path"
    rm -f "$cache_artifact_path" "$cache_sha_path"
  fi
  run_cmd fetch -o "$artifact_path" "$artifact_url"
  run_cmd fetch -o "$sha_path" "$sha_url"
  if [ "$DRY_RUN" != "1" ]; then
    cp "$artifact_path" "$cache_artifact_path"
    cp "$sha_path" "$cache_sha_path"
    prune_cached_artifacts "$CACHE_KEEP"
  fi
}

artifact_url_for() {
  printf '%s%s\n' "$RELEASE_INDEX_URL" "$1"
}

write_resolv_conf() {
  local target=$1 tmp
  tmp=$(mktemp)
  if [ ${#RESOLVERS[@]} -gt 0 ]; then
    local resolver
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
  local target=$1 content tag
  tag=$(current_tag "$JAIL_NAME")
  remove_managed_blocks_for_name "$target" "$JAIL_NAME"
  content=$(cat <<EOF
hostname="${JAIL_HOSTNAME}"
rpcbind_enable="NO"
network_interfaces=""
EOF
)
  replace_managed_block "$target" "$tag" "$content"
}

write_host_rc_conf() {
  local content tag effective_service_iface
  tag=$(current_tag "$JAIL_NAME")
  remove_managed_blocks_for_name "$RC_CONF_PATH" "$JAIL_NAME"
  effective_service_iface=$SERVICE_IFACE
  if [ -z "$effective_service_iface" ] && [ "$NETWORK_MODE" = "private-loopback" ]; then
    effective_service_iface=$PRIVATE_IFACE
  fi
  content=$(cat <<EOF
# network_mode=${NETWORK_MODE}
# service_iface=${effective_service_iface}
jail_enable="YES"
jail_list="\${jail_list:+\${jail_list} }${JAIL_NAME}"
jail_${JAIL_NAME}_rootdir="${JAIL_ROOT}"
jail_${JAIL_NAME}_hostname="${JAIL_HOSTNAME}"
jail_${JAIL_NAME}_ip="${JAIL_LOOPBACK_IP},${JAIL_SERVICE_IP}"
jail_${JAIL_NAME}_mount_enable="YES"
jail_${JAIL_NAME}_fstab="${JAIL_FSTAB_PATH}"
jail_${JAIL_NAME}_devfs_enable="YES"
jail_${JAIL_NAME}_procfs_enable="NO"
EOF
)
  replace_managed_block "$RC_CONF_PATH" "$tag" "$content"
}

collect_used_ips() {
  local name mode iface rootdir hostname loopback_ip service_ip fstab
  while IFS=$'\t' read -r name mode iface rootdir hostname loopback_ip service_ip fstab; do
    [ "$name" = "$JAIL_NAME" ] && continue
    [ -n "$loopback_ip" ] && printf '%s\n' "$loopback_ip"
    [ -n "$service_ip" ] && printf '%s\n' "$service_ip"
  done < <(managed_jail_records)
}

prime_used_ips() {
  local candidate_ip
  USED_IPS=()
  while IFS= read -r candidate_ip; do
    if [ -n "$candidate_ip" ] && ! ip_in_use "$candidate_ip"; then
      USED_IPS+=("$candidate_ip")
    fi
  done < <(collect_used_ips)
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

allocate_ip_from_cidr() {
  local cidr=$1 skip_count=$2
  local network prefix size first last candidate_int candidate_ip
  network=$(cidr_network_int "$cidr")
  prefix=$(cidr_prefix "$cidr")
  [ "$prefix" -le 30 ] || die "CIDR too small for auto allocation: $cidr"
  size=$((1 << (32 - prefix)))
  first=$((network + skip_count))
  last=$((network + size - 2))
  for ((candidate_int = first; candidate_int <= last; candidate_int++)); do
    candidate_ip=$(int_to_ip "$candidate_int")
    if ! ip_in_use "$candidate_ip"; then
      printf '%s\n' "$candidate_ip"
      return 0
    fi
  done
  die "no free addresses left in $cidr"
}

ensure_jail_pfs() {
  local mount_info hammer_special hammer_mount base_special existing_special
  mount_info=$(find_hammer2_mount "$ROOT_PARENT")
  hammer_special=${mount_info%%$'\t'*}
  hammer_mount=${mount_info#*$'\t'}
  base_special=${hammer_special%@*}
  [ "$base_special" != "$hammer_special" ] || die "could not determine base HAMMER2 special from mount source: $hammer_special"
  JAIL_ROOT=${ROOT_PARENT%/}/${JAIL_NAME}
  PFS_SPECIAL="${base_special}@${JAIL_NAME}"
  if is_mountpoint "$JAIL_ROOT" hammer2; then
    existing_special=$(mounted_special_for_path "$JAIL_ROOT")
    [ "$existing_special" = "$PFS_SPECIAL" ] || die "$JAIL_ROOT is already mounted from $existing_special, expected $PFS_SPECIAL"
  else
    if [ -d "$JAIL_ROOT" ] && [ -n "$(find "$JAIL_ROOT" -mindepth 1 -maxdepth 1 2>/dev/null)" ]; then
      die "$JAIL_ROOT exists and is not empty before HAMMER2 mount"
    fi
    run_cmd mkdir -p "$JAIL_ROOT"
    if ! pfs_exists "$hammer_mount" "$JAIL_NAME"; then
      run_cmd hammer2 -s "$hammer_mount" pfs-create "$JAIL_NAME"
    fi
    set_root_fstab_line "$JAIL_FSTAB_PATH" "${PFS_SPECIAL} ${JAIL_ROOT} hammer2 rw 2 2 $(current_root_marker "$JAIL_NAME")"
    run_cmd mount_hammer2 "$PFS_SPECIAL" "$JAIL_ROOT"
  fi
}

download_and_extract_world() {
  local workdir artifact_url sha_url artifact_path sha_path expected_hash actual_hash
  workdir=$(mktemp -d)
  artifact_url=$(artifact_url_for "$ARTIFACT_NAME")
  sha_url=$(artifact_url_for "${ARTIFACT_NAME}.sha256")
  artifact_path="$workdir/$ARTIFACT_NAME"
  sha_path="$workdir/${ARTIFACT_NAME}.sha256"
  log "using world artifact: $ARTIFACT_NAME"
  log "artifact url: $artifact_url"
  prepare_cached_artifact "$artifact_url" "$sha_url" "$workdir"
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
  if [ "$DRY_RUN" != "1" ]; then
    rm -rf "$workdir"
  fi
}

bootstrap_pkg_and_install() {
  local mounted_here=0 status=0 previous_exit_trap=
  [ "$BOOTSTRAP_PKG" = "1" ] || [ ${#PACKAGES[@]} -gt 0 ] || return 0
  if ! is_mountpoint "$JAIL_ROOT/dev" devfs; then
    run_cmd mount -t devfs devfs "$JAIL_ROOT/dev"
    mounted_here=1
    if [ "$DRY_RUN" != "1" ]; then
      previous_exit_trap=$(trap -p EXIT || true)
      trap 'umount "'"$JAIL_ROOT"'"/dev" >/dev/null 2>&1 || true' EXIT
    fi
  fi

  set +e
  run_cmd chroot "$JAIL_ROOT" /usr/bin/env ASSUME_ALWAYS_YES=yes /bin/sh -c 'cd /usr && make pkg-bootstrap-force'
  status=$?
  if [ "$status" -eq 0 ] && [ ${#PACKAGES[@]} -gt 0 ]; then
    run_cmd chroot "$JAIL_ROOT" /usr/bin/env ASSUME_ALWAYS_YES=yes PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin /usr/local/sbin/pkg install -y "${PACKAGES[@]}"
    status=$?
  fi
  set -e

  if [ "$mounted_here" = "1" ]; then
    if [ "$DRY_RUN" != "1" ]; then
      if [ -n "$previous_exit_trap" ]; then
        eval "$previous_exit_trap"
      else
        trap - EXIT
      fi
    fi
    run_cmd umount "$JAIL_ROOT/dev"
  fi

  [ "$status" -eq 0 ] || return "$status"
}

running_jid() {
  local jid_file="/var/run/jail_${JAIL_NAME}.id" jid
  if [ -f "$jid_file" ]; then
    jid=$(tr -d '[:space:]' < "$jid_file")
    if [ -n "$jid" ] && jls | awk -v jid="$jid" '$1 == jid { found = 1 } END { exit(found ? 0 : 1) }'; then
      printf '%s\n' "$jid"
      return 0
    fi
  fi
  if [ -n "$JAIL_ROOT" ]; then
    jls | awk -v path="$JAIL_ROOT" '$4 == path { print $1; exit }'
  fi
}

command_create() {
  [ -n "$JAIL_NAME" ] || die "create requires --name"
  validate_jail_name "$JAIL_NAME"
  validate_managed_state
  NETWORK_MODE=$(normalize_network_mode "$NETWORK_MODE")
  if [ -n "$JAIL_INTERFACE" ]; then
    warn "--interface is deprecated; use --service-iface instead"
    if [ -z "$SERVICE_IFACE" ]; then
      SERVICE_IFACE=$JAIL_INTERFACE
    fi
  fi
  if [ -z "$JAIL_HOSTNAME" ]; then
    JAIL_HOSTNAME=$JAIL_NAME
  fi
  if [ -z "$JAIL_FSTAB_PATH" ]; then
    JAIL_FSTAB_PATH="/etc/fstab.${JAIL_NAME}"
  fi
  ensure_parent_directory
  if [ -z "$ARTIFACT_NAME" ]; then
    ARTIFACT_NAME=$(latest_world_artifact)
  fi
  prime_used_ips
  if [ -z "$JAIL_LOOPBACK_IP" ]; then
    JAIL_LOOPBACK_IP=$(allocate_ip_from_cidr "$LOOPBACK_SUBNET" 2)
  elif ip_in_use "$JAIL_LOOPBACK_IP"; then
    die "loopback IP already in use by another jail: $JAIL_LOOPBACK_IP"
  fi
  if ! ip_in_use "$JAIL_LOOPBACK_IP"; then
    USED_IPS+=("$JAIL_LOOPBACK_IP")
  fi
  assert_ip_not_configured_on_host "$JAIL_LOOPBACK_IP" "loopback"
  case "$NETWORK_MODE" in
    private-loopback)
      if [ -z "$JAIL_SERVICE_IP" ]; then
        JAIL_SERVICE_IP=$(allocate_ip_from_cidr "$SERVICE_SUBNET" 2)
      elif ip_in_use "$JAIL_SERVICE_IP"; then
        die "service IP already in use by another jail: $JAIL_SERVICE_IP"
      fi
      if [ -z "$SERVICE_IFACE" ]; then
        SERVICE_IFACE=$PRIVATE_IFACE
      fi
      ;;
    interface-alias)
      [ -n "$SERVICE_IFACE" ] || die "interface-alias mode requires --service-iface"
      [ -n "$JAIL_SERVICE_IP" ] || die "interface-alias mode requires --service-ip"
      if ip_in_use "$JAIL_SERVICE_IP"; then
        die "service IP already in use by another jail: $JAIL_SERVICE_IP"
      fi
      ;;
  esac
  [ "$JAIL_SERVICE_IP" != "$JAIL_LOOPBACK_IP" ] || die "service and loopback IPs must be different"
  assert_ip_not_configured_on_host "$JAIL_SERVICE_IP" "service"
  ensure_jail_pfs
  download_and_extract_world
  run_cmd mkdir -p "$JAIL_ROOT/etc" "$JAIL_ROOT/dev" "$JAIL_ROOT/proc"
  write_jail_local_rc_conf "$JAIL_ROOT/etc/rc.conf"
  write_resolv_conf "$JAIL_ROOT/etc/resolv.conf"
  write_host_rc_conf
  rebuild_managed_network_block
  ensure_runtime_network_for_jail
  bootstrap_pkg_and_install
  printf '\n'
  log "prepared jail ${JAIL_NAME}"
  log "root: ${JAIL_ROOT}"
  log "artifact: ${ARTIFACT_NAME}"
  if [ "$NO_CACHE" = "1" ]; then
    log "cache: disabled"
  else
    log "cache dir: ${CACHE_DIR}"
    log "cache keep: ${CACHE_KEEP}"
  fi
  log "loopback ip: ${JAIL_LOOPBACK_IP}"
  log "service ip: ${JAIL_SERVICE_IP}"
  log "network mode: ${NETWORK_MODE}"
  log "service iface: ${SERVICE_IFACE}"
  log "rc.conf block tag: $(current_tag "$JAIL_NAME")"
  log "jail fstab: ${JAIL_FSTAB_PATH}"
  log "fstab marker: $(current_root_marker "$JAIL_NAME")"
  if [ "$NETWORK_MODE" = "private-loopback" ]; then
    warn "private-subnet aliases are configured on ${LOOPBACK_IFACE}/${PRIVATE_IFACE}; outbound Internet still requires separate PF/NAT host configuration"
  fi
  if ! grep -Eq '^jail_default_allow_listen_override="YES"' "$RC_CONF_PATH" 2>/dev/null; then
    warn "jail_default_allow_listen_override is not set to YES in $RC_CONF_PATH; jailed listeners may conflict with host wildcard listeners"
  fi
  log "next step: service jail start ${JAIL_NAME}"
}

command_start() {
  [ -n "$JAIL_NAME" ] || die "start requires --name"
  validate_managed_state
  resolve_existing_config
  [ -n "$JAIL_ROOT" ] || die "no managed jail configuration found for $JAIL_NAME"
  [ -f "$JAIL_FSTAB_PATH" ] || die "missing jail fstab: $JAIL_FSTAB_PATH"
  [ -d "$JAIL_ROOT" ] || die "missing jail root: $JAIL_ROOT"
  ensure_runtime_network_for_jail
  run_cmd service jail start "$JAIL_NAME"
}

command_stop() {
  [ -n "$JAIL_NAME" ] || die "stop requires --name"
  run_cmd service jail stop "$JAIL_NAME"
}

command_destroy() {
  local jid mount_info hammer_special hammer_mount
  [ -n "$JAIL_NAME" ] || die "destroy requires --name"
  resolve_existing_config
  [ -n "$JAIL_ROOT" ] || die "no managed jail configuration found for $JAIL_NAME"
  jid=$(running_jid || true)
  if [ -n "$jid" ]; then
    run_cmd service jail stop "$JAIL_NAME"
  fi
  if [ -n "$JAIL_LOOPBACK_IP" ] && [ -n "$JAIL_SERVICE_IP" ]; then
    remove_runtime_network_for_jail
  fi
  if is_mountpoint "$JAIL_ROOT" hammer2; then
    run_cmd umount "$JAIL_ROOT"
  fi
  remove_managed_blocks_for_name "$RC_CONF_PATH" "$JAIL_NAME"
  remove_root_fstab_line "$JAIL_FSTAB_PATH"
  rebuild_managed_network_block
  mount_info=$(find_hammer2_mount "$(dirname "$JAIL_ROOT")")
  hammer_special=${mount_info%%$'\t'*}
  hammer_mount=${mount_info#*$'\t'}
  if pfs_exists "$hammer_mount" "$JAIL_NAME"; then
    run_cmd hammer2 -s "$hammer_mount" pfs-delete "$JAIL_NAME"
  fi
  if [ -d "$JAIL_ROOT" ] && [ -z "$(find "$JAIL_ROOT" -mindepth 1 -maxdepth 1 2>/dev/null)" ]; then
    run_cmd rmdir "$JAIL_ROOT"
  fi
  log "destroyed managed jail $JAIL_NAME"
}

command_status() {
  local jid configured=no mounted=no mounted_special=
  [ -n "$JAIL_NAME" ] || die "status requires --name"
  validate_managed_state
  resolve_existing_config
  if [ -n "$JAIL_ROOT" ]; then
    configured=yes
  fi
  if [ -n "$JAIL_ROOT" ] && is_mountpoint "$JAIL_ROOT" hammer2; then
    mounted=yes
    mounted_special=$(mounted_special_for_path "$JAIL_ROOT" || true)
  fi
  jid=$(running_jid || true)
  log "name: ${JAIL_NAME}"
  log "configured: ${configured}"
  log "running: $( [ -n "$jid" ] && printf yes || printf no )"
  if [ -n "$jid" ]; then
    log "jid: ${jid}"
  fi
  if [ -n "$JAIL_ROOT" ]; then
    log "root: ${JAIL_ROOT}"
    log "hostname: ${JAIL_HOSTNAME}"
    log "network mode: ${NETWORK_MODE}"
    log "loopback ip: ${JAIL_LOOPBACK_IP}"
    log "service ip: ${JAIL_SERVICE_IP}"
    log "service iface: ${SERVICE_IFACE}"
    log "jail fstab: ${JAIL_FSTAB_PATH}"
    if [ "$NETWORK_MODE" = "private-loopback" ]; then
      log "external egress: requires separate PF/NAT host configuration"
    fi
  fi
  log "root mounted: ${mounted}"
  if [ -n "$mounted_special" ]; then
    log "mounted special: ${mounted_special}"
  fi
}

command_list() {
  local name jid mode iface rootdir hostname loopback_ip service_ip fstab
  local -a records=()
  validate_managed_state
  mapfile -t records < <(managed_jail_records)
  if [ ${#records[@]} -eq 0 ]; then
    log "no managed jails found"
    return 0
  fi
  for record in "${records[@]}"; do
    IFS=$'\t' read -r name mode iface rootdir hostname loopback_ip service_ip fstab <<<"$record"
    [ -n "$name" ] || continue
    JAIL_NAME=$name
    JAIL_ROOT=$rootdir
    jid=$(running_jid || true)
    printf '%s\tconfigured=%s\trunning=%s\tmode=%s' "$name" yes "$([ -n "$jid" ] && printf yes || printf no)" "$mode"
    if [ -n "$jid" ]; then
      printf '\tjid=%s' "$jid"
    fi
    if [ -n "$rootdir" ]; then
      printf '\troot=%s' "$rootdir"
    fi
    if [ -n "$iface" ]; then
      printf '\tservice_iface=%s' "$iface"
    fi
    if [ -n "$service_ip" ]; then
      printf '\tservice_ip=%s' "$service_ip"
    fi
    printf '\n'
  done
}

command_verify() {
  validate_managed_state
  validate_managed_network_block
  log "managed jail state is valid"
  local name mode iface rootdir hostname loopback_ip service_ip fstab
  while IFS=$'\t' read -r name mode iface rootdir hostname loopback_ip service_ip fstab; do
    [ -n "$name" ] || continue
    printf '%s\tmode=%s\tservice_iface=%s\tloopback_ip=%s\tservice_ip=%s\troot=%s\n' "$name" "$mode" "$iface" "$loopback_ip" "$service_ip" "$rootdir"
  done < <(managed_jail_records)
}

command_rebuild_network() {
  validate_managed_state
  rebuild_managed_network_block
  log "rebuilt manager-owned network block"
}

parse_common_flags() {
  while [ $# -gt 0 ]; do
    case "$1" in
      --dry-run)
        DRY_RUN=1
        shift
        ;;
      -h|--help)
        usage
        exit 0
        ;;
      *)
        printf '%s\n' "$@"
        return 0
        ;;
    esac
  done
}

require_option_value() {
  local option=$1 argc=$2
  [ "$argc" -ge 2 ] || die "option ${option} requires a value"
}

parse_create_flags() {
  while [ $# -gt 0 ]; do
    case "$1" in
      --name)
        require_option_value "$1" $#
        JAIL_NAME=$2
        shift 2
        ;;
      --hostname)
        require_option_value "$1" $#
        JAIL_HOSTNAME=$2
        shift 2
        ;;
      --interface)
        require_option_value "$1" $#
        JAIL_INTERFACE=$2
        shift 2
        ;;
      --network-mode)
        require_option_value "$1" $#
        NETWORK_MODE=$2
        shift 2
        ;;
      --service-iface)
        require_option_value "$1" $#
        SERVICE_IFACE=$2
        shift 2
        ;;
      --root-parent)
        require_option_value "$1" $#
        ROOT_PARENT=$2
        shift 2
        ;;
      --private-iface)
        require_option_value "$1" $#
        PRIVATE_IFACE=$2
        shift 2
        ;;
      --loopback-iface)
        require_option_value "$1" $#
        LOOPBACK_IFACE=$2
        shift 2
        ;;
      --cache-dir)
        require_option_value "$1" $#
        CACHE_DIR=$2
        shift 2
        ;;
      --cache-keep)
        require_option_value "$1" $#
        CACHE_KEEP=$2
        shift 2
        ;;
      --no-cache)
        NO_CACHE=1
        shift
        ;;
      --service-subnet)
        require_option_value "$1" $#
        SERVICE_SUBNET=$2
        shift 2
        ;;
      --loopback-subnet)
        require_option_value "$1" $#
        LOOPBACK_SUBNET=$2
        shift 2
        ;;
      --service-ip)
        require_option_value "$1" $#
        JAIL_SERVICE_IP=$2
        shift 2
        ;;
      --loopback-ip)
        require_option_value "$1" $#
        JAIL_LOOPBACK_IP=$2
        shift 2
        ;;
      --fstab-path)
        require_option_value "$1" $#
        JAIL_FSTAB_PATH=$2
        shift 2
        ;;
      --artifact-name)
        require_option_value "$1" $#
        ARTIFACT_NAME=$2
        shift 2
        ;;
      --release-index-url)
        require_option_value "$1" $#
        RELEASE_INDEX_URL=$2
        shift 2
        ;;
      --resolver)
        require_option_value "$1" $#
        RESOLVERS+=("$2")
        shift 2
        ;;
      --bootstrap-pkg)
        BOOTSTRAP_PKG=1
        shift
        ;;
      --package)
        require_option_value "$1" $#
        PACKAGES+=("$2")
        shift 2
        ;;
      --packages)
        require_option_value "$1" $#
        read -r -a more_packages <<<"$2"
        PACKAGES+=("${more_packages[@]}")
        shift 2
        ;;
      *)
        die "unknown create option: $1"
        ;;
    esac
  done
}

parse_name_only_flags() {
  while [ $# -gt 0 ]; do
    case "$1" in
      --name)
        require_option_value "$1" $#
        JAIL_NAME=$2
        shift 2
        ;;
      *)
        die "unknown option: $1"
        ;;
    esac
  done
}

main() {
  local command args
  [ $# -gt 0 ] || { usage; exit 1; }
  command=$1
  shift
  case "$command" in
    -h|--help|help)
      usage
      return 0
      ;;
  esac
  require_dragonfly_root
  require_command fetch
  require_command tar
  require_command sha256
  require_command hammer2
  require_command ifconfig
  require_command mount_hammer2
  require_command mount
  require_command umount
  require_command chroot
  require_command awk
  require_command cmp
  require_command grep
  require_command sed
  require_command stat
  require_command find
  require_command service
  require_command jls

  mapfile -t args < <(parse_common_flags "$@")
  set -- "${args[@]}"

  [ "$CACHE_KEEP" -ge 1 ] 2>/dev/null || die "--cache-keep must be at least 1"

  case "$command" in
    create)
      parse_create_flags "$@"
      command_create
      ;;
    destroy)
      parse_name_only_flags "$@"
      command_destroy
      ;;
    start)
      parse_name_only_flags "$@"
      command_start
      ;;
    stop)
      parse_name_only_flags "$@"
      command_stop
      ;;
    status)
      parse_name_only_flags "$@"
      command_status
      ;;
    list)
      [ $# -eq 0 ] || die "list does not accept additional options"
      command_list
      ;;
    verify)
      [ $# -eq 0 ] || die "verify does not accept additional options"
      command_verify
      ;;
    rebuild-network)
      [ $# -eq 0 ] || die "rebuild-network does not accept additional options"
      command_rebuild_network
      ;;
    *)
      die "unknown command: $command"
      ;;
  esac
}

main "$@"
