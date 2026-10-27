#!/usr/bin/env bash
# Ubuntu 22.04: passive Zenoh diagnostics; no service/configuration changes.
# Usage: ./zenoh-capture.sh [duration_seconds=3600] [output_parent=$HOME/zenoh-logs]
set -u
export LC_ALL=C TZ=UTC
duration=${1:-3600}
parent=${2:-$HOME/zenoh-logs}
if ! [[ "$duration" =~ ^[0-9]+$ ]] || (( duration < 1 || duration > 86400 )); then
  echo 'Duration must be 1..86400 seconds.' >&2; exit 2
fi
# Resolve the invoking user's default destination before sudo changes HOME.
if (( EUID != 0 )); then
  exec sudo bash "${BASH_SOURCE[0]}" "$duration" "$parent"
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
echo 'Stop with Ctrl+C. No ROS messages or Goals are published.'

# Rotate each stream at 8 MiB, retaining four files per stream (~192 MiB total).
# Bounds disk use; after rotation old records are removed. Stop after reproduction.
rotate() {
  local base=$1 line bytes=0 size max=8388608 i
  : > "$base"
  while IFS= read -r line || [[ -n "$line" ]]; do
    size=$((${#line}+1))
    if (( bytes + size > max )); then
      rm -f "$base.3"
      for i in 2 1; do [[ ! -f "$base.$i" ]] || mv "$base.$i" "$base.$((i+1))"; done
      mv "$base" "$base.1"
      : > "$base"; bytes=0
    fi
    printf '%s\n' "$line" >> "$base"
    bytes=$((bytes+size))
  done
}
export -f rotate

{
  date -u --iso-8601=ns
  hostname; uname -a
  timedatectl
  command -v chronyc >/dev/null && chronyc tracking
  ip -br address; ip route
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
setsid bash -c 'timeout "$1" journalctl -u rmw-zenoh-router.service --since "5 minutes ago" -f --utc -o short-iso-precise --no-pager 2>&1 | rotate "$2"' _ "$duration" "$out/router.log" &
pids+=("$!")
setsid bash -c '
  end=$((SECONDS+$1))
  while (( SECONDS < end )); do
    date -u --iso-8601=ns
    ss -tinp "( sport = :7447 or dport = :7447 )" 2>&1
    sleep 1
  done | rotate "$2"
' _ "$duration" "$out/tcp.log" &
pids+=("$!")
setsid bash -c '
  end=$((SECONDS+$1))
  while (( SECONDS < end )); do
    date -u --iso-8601=ns
    cat /proc/net/dev
    sleep 1
  done | rotate "$2"
' _ "$duration" "$out/network.log" &
pids+=("$!")
setsid bash -c '
  end=$((SECONDS+$1))
  while (( SECONDS < end )); do
    date -u --iso-8601=ns
    ps -C rmw_zenohd -o pid,etimes,pcpu,pmem,rss,nlwp,stat,wchan:32
    head -n 1 /proc/stat
    cat /proc/loadavg
    sleep 5
  done | rotate "$2"
' _ "$duration" "$out/process.log" &
pids+=("$!")
# Central probes both endpoints; endpoints probe central and the opposite endpoint.
if ip -o -4 addr show | grep -q 'inet 192\.168\.1\.70/'; then
  targets=(192.168.20.73 192.168.20.119)
elif ip -o -4 addr show | grep -q 'inet 192\.168\.20\.73/'; then
  targets=(192.168.1.70 192.168.20.119)
else
  targets=(192.168.1.70 192.168.20.73)
fi
for target in "${targets[@]}"; do
  setsid bash -c 'timeout -s INT "$1" ping -n -D -O -i 1 -W 1 "$2" 2>&1 | rotate "$3"' _ "$duration" "$target" "$out/ping-$target.log" &
  pids+=("$!")
done
for pid in "${pids[@]}"; do wait "$pid" || true; done
