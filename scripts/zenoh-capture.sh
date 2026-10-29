#!/usr/bin/env bash
# Ubuntu 22.04: passive Zenoh diagnostics; no service/configuration changes.
# Usage: ./zenoh-capture.sh [duration_seconds=0] [output_parent=$HOME/zenoh-logs] [peer_retention_seconds=300]
set -u
export LC_ALL=C TZ=UTC
duration=${1:-0}
parent=${2:-$HOME/zenoh-logs}
# Optional third argument: keep probing disconnected peers for this many seconds.
retain=${3:-300}
if ! [[ "$retain" =~ ^[0-9]+$ ]] || (( retain > 86400 )); then
  echo 'Peer retention must be 0..86400 seconds.' >&2; exit 2
fi
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
[[ -r "$script_dir/capture-metrics.py" ]] || { echo 'Missing capture-metrics.py' >&2; exit 1; }
if ! [[ "$duration" =~ ^[0-9]+$ ]] || (( duration < 0 || duration > 86400 )); then
  echo 'Duration must be 0..86400 seconds (0 = unlimited).' >&2; exit 2
fi
# Resolve the invoking user's default destination before sudo changes HOME.
if (( EUID != 0 )); then
  exec sudo bash "${BASH_SOURCE[0]}" "$duration" "$parent" "$retain"
fi
umask 077
parent_created=false
[[ -d "$parent" ]] || parent_created=true
mkdir -p "$parent" || exit 1
if "$parent_created" && (( EUID == 0 )) && [[ ${SUDO_UID:-} =~ ^[0-9]+$ && ${SUDO_GID:-} =~ ^[0-9]+$ ]]; then
  chown -- "$SUDO_UID:$SUDO_GID" "$parent" || exit 1
fi
out=$(mktemp -d "$parent/zenoh-$(hostname)-$(TZ=Asia/Tokyo date +%Y%m%dT%H%M%SJST)-XXXXXX") || exit 1
echo "Recording to: $out"
if (( duration == 0 )); then echo 'Duration: unlimited (capacity-based rotation).'; fi
echo 'Stop with Ctrl+C. No ROS messages or Goals are published.'

# Rotate each stream at 32 MiB, retaining 16 files per stream (~5 GiB total for ten bounded streams).
# Bounds disk use; after rotation old records are removed. Stop after reproduction.
rotate() {
  local base=$1 line bytes=0 size max=33554432 i
  exec 3> "$base"
  while IFS= read -r line || [[ -n "$line" ]]; do
    size=$((${#line}+1))
    if (( bytes + size > max )); then
      exec 3>&-
      rm -f "$base.15"
      for ((i=14; i>=1; i--)); do [[ ! -f "$base.$i" ]] || mv "$base.$i" "$base.$((i+1))"; done
      mv "$base" "$base.1"
      exec 3> "$base"; bytes=0
    fi
    printf '%s\n' "$line" >&3
    bytes=$((bytes+size))
  done
  exec 3>&-
}
export -f rotate

{
  printf 'duration_seconds=%s (0=unlimited)\n' "$duration"
  date -u --iso-8601=ns
  hostname; uname -a
  timedatectl
  command -v chronyc >/dev/null && chronyc tracking
  ip -br address; ip route
  lscpu
  df -hT "$out"
  lsblk -o NAME,TYPE,SIZE,MOUNTPOINT
  printf 'peer_retention_seconds=%s
' "$retain"
  command -v ethtool >/dev/null && for nic in /sys/class/net/*; do
    timeout 2 ethtool "${nic##*/}"
    timeout 2 ethtool -S "${nic##*/}"
  done
  systemctl show rmw-zenoh-router.service -p ActiveState -p ActiveEnterTimestamp -p MainPID
  dpkg-query -W ros-humble-rmw-zenoh-cpp ros-humble-zenoh-cpp-vendor
  sha256sum /opt/ros/humble/opt/zenoh_cpp_vendor/lib/libzenohc.so
  cat /etc/zenoh/rmw_zenoh_config/deployment.env
} > "$out/metadata.txt" 2>&1
for file in deployment.env deny-profile.json router.json5 ros-env.sh; do
  [[ ! -r /etc/zenoh/rmw_zenoh_config/$file ]] || cp "/etc/zenoh/rmw_zenoh_config/$file" "$out/$file"
done

pids=()
cleanup() {
  trap - EXIT INT TERM
  for pid in "${pids[@]}"; do kill -TERM -- "-$pid" 2>/dev/null || true; done
  for pid in "${pids[@]}"; do wait "$pid" 2>/dev/null || true; done
  # sudo already grants root here; no second password prompt is needed.
  # Limit the ownership change to this run's newly created directory.
  if (( EUID == 0 )) && [[ ${SUDO_UID:-} =~ ^[0-9]+$ && ${SUDO_GID:-} =~ ^[0-9]+$ ]]; then
    if ! chown -R -- "$SUDO_UID:$SUDO_GID" "$out"; then
      echo "Warning: could not restore log ownership: $out" >&2
    fi
  fi
  echo "Stopped. Logs: $out"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# Separate process groups allow Ctrl+C to stop every child, including journalctl.
setsid bash -c '
  printf "router start %s\n" "$(date -u --iso-8601=ns)" >> "$3"
  timeout --foreground "$1" journalctl -u rmw-zenoh-router.service --since "5 minutes ago" -f --utc -o short-iso-precise --no-pager 2>&1 | rotate "$2"
  codes=("${PIPESTATUS[@]}")
  printf "router stop %s journal_exit=%s writer_exit=%s (124=duration elapsed)\n" "$(date -u --iso-8601=ns)" "${codes[0]}" "${codes[1]}" >> "$3"
' _ "$duration" "$out/router.log" "$out/capture-status.log" &
pids+=("$!")

setsid bash -c '
  end=$((SECONDS+$1))
  while (( $1 == 0 || SECONDS < end )); do
    date -u --iso-8601=ns
    cat /proc/net/dev
    sleep 1
  done | rotate "$2"
' _ "$duration" "$out/network.log" &
pids+=("$!")
setsid bash -c '
  end=$((SECONDS+$1))
  while (( $1 == 0 || SECONDS < end )); do
    date -u --iso-8601=ns
    ps -C rmw_zenohd -o pid,etimes,pcpu,pmem,rss,nlwp,stat,wchan:32
    head -n 1 /proc/stat
    cat /proc/loadavg
    sleep 5
  done | rotate "$2"
' _ "$duration" "$out/process.log" &
pids+=("$!")
setsid bash -c '
  printf "kernel start %s\n" "$(date -u --iso-8601=ns)" >> "$3"
  timeout --foreground "$1" journalctl -k --since "5 minutes ago" -f --utc -o short-iso-precise --no-pager 2>&1 | rotate "$2"
  codes=("${PIPESTATUS[@]}")
  printf "kernel stop %s journal_exit=%s writer_exit=%s (124=duration elapsed)\n" "$(date -u --iso-8601=ns)" "${codes[0]}" "${codes[1]}" >> "$3"
' _ "$duration" "$out/kernel.log" "$out/capture-status.log" &
pids+=("$!")
# Central probes both endpoints; endpoints probe central and the opposite endpoint.
if ip -o -4 addr show | grep -q 'inet 192\.168\.1\.70/'; then
  targets=(192.168.20.73 192.168.20.119)
elif ip -o -4 addr show | grep -q 'inet 192\.168\.20\.73/'; then
  targets=(192.168.1.70 192.168.20.119)
else
  targets=(192.168.1.70 192.168.20.73)
fi
setsid bash -c '
  python3 "$1" --output "$2" --duration "$3" --retain-seconds "$4" --skip-ping "${@:5}"
  result=$?
  printf "metrics exit=%s %s\n" "$result" "$(date -u --iso-8601=ns)" >> "$2/capture-status.log"
  exit "$result"
' _ "$script_dir/capture-metrics.py" "$out" "$duration" "$retain" "${targets[@]}" &
pids+=("$!")
for target in "${targets[@]}"; do
  setsid bash -c 'timeout --foreground -s INT "$1" ping -n -D -O -i 1 -W 1 "$2" 2>&1 | rotate "$3"' _ "$duration" "$target" "$out/ping-$target.log" &
  pids+=("$!")
done
for pid in "${pids[@]}"; do wait "$pid" || true; done
