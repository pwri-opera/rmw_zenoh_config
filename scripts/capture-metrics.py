#!/usr/bin/env python3
"""Bounded, passive Linux metrics plus low-rate probes of observed TCP peers."""
import argparse
import concurrent.futures
import datetime
import ipaddress
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import time


class RotatingLog:
    def __init__(self, path, limit=32 * 1024 * 1024, count=16):
        self.path, self.limit, self.count = Path(path), limit, count
        self.file = self.path.open('ab')
        self.size = self.file.tell()

    def write(self, text):
        data = text.encode('utf-8', errors='replace')
        # Preserve lines, including the established raw ss output format.
        for line in data.splitlines(keepends=True):
            if self.size and self.size + len(line) > self.limit:
                self.file.close()
                self.path.with_name(self.path.name + '.' + str(self.count - 1)).unlink(missing_ok=True)
                for i in range(self.count - 2, 0, -1):
                    old = self.path.with_name(self.path.name + '.' + str(i))
                    if old.exists():
                        old.rename(self.path.with_name(self.path.name + '.' + str(i + 1)))
                self.path.rename(self.path.with_name(self.path.name + '.1'))
                self.file = self.path.open('wb')
                self.size = 0
            self.file.write(line)
            self.size += len(line)
        self.file.flush()

    def close(self):
        self.file.close()


def timestamp():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def read(path):
    try:
        return Path(path).read_text().strip()
    except OSError as e:
        return 'unavailable: ' + str(e)


def command(args, timeout=2):
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return {'returncode': p.returncode, 'stdout': p.stdout, 'stderr': p.stderr}
    except (OSError, subprocess.TimeoutExpired) as e:
        return {'returncode': None, 'stdout': '', 'stderr': str(e)}


def peer_ip(endpoint):
    host = endpoint.rsplit(':', 1)[0].strip('[]')
    try:
        ip = ipaddress.ip_address(host.split('%')[0])
        if getattr(ip, 'ipv4_mapped', None):
            ip = ip.ipv4_mapped
            host = str(ip)
        return None if ip.is_loopback or ip.is_unspecified else host
    except ValueError:
        return None


def parse_ss(text):
    rows, current = [], None
    for line in text.splitlines():
        fields = line.split()
        if len(fields) >= 5 and fields[0] in {'ESTAB', 'SYN-SENT', 'SYN-RECV', 'FIN-WAIT-1', 'FIN-WAIT-2', 'TIME-WAIT', 'CLOSE-WAIT', 'LAST-ACK', 'CLOSING'}:
            current = {'state': fields[0], 'recv_q': int(fields[1]), 'send_q': int(fields[2]),
                       'local': fields[3], 'remote': fields[4], 'ip': peer_ip(fields[4]), 'detail': line}
            rows.append(current)
        elif current and line[:1].isspace():
            current['detail'] += ' ' + line.strip()
    for row in rows:
        detail = row.pop('detail')
        row['counters'] = {}
        for name in ('bytes_sent', 'bytes_acked', 'bytes_received', 'bytes_retrans', 'segs_in', 'segs_out', 'data_segs_in', 'data_segs_out'):
            m = re.search(r'\b' + name + r':(\d+)', detail)
            row['counters'][name] = int(m[1]) if m else None
        for name in ('rtt', 'rto', 'cwnd', 'retrans', 'rwnd_limited', 'sndbuf_limited', 'lastsnd', 'lastrcv', 'lastack'):
            m = re.search(r'\b' + name + r':([^\s]+)', detail)
            row[name] = m[1] if m else None
        m = re.search(r'\bsk:([0-9a-fA-F]+)', detail)
        row['socket_id'] = m[1] if m else None
        row['key'] = '|'.join((row['local'], row['remote'], row['socket_id'] or 'unknown'))
    return rows


class Tracker:
    def __init__(self, retention):
        self.retention, self.peers, self.previous = retention, {}, {}

    def update(self, rows, now):
        events = []
        active = {r['ip'] for r in rows if r['ip'] and r['state'] == 'ESTAB'}
        for ip in active:
            p = self.peers.get(ip)
            if p is None or not p['connected']:
                events.append({'event': 'peer_connected' if p is None else 'peer_reconnected', 'ip': ip})
            self.peers[ip] = {'connected': True, 'disconnected_at': None}
        for ip, p in list(self.peers.items()):
            if ip not in active:
                if p['connected']:
                    p.update(connected=False, disconnected_at=now)
                    events.append({'event': 'peer_disconnected', 'ip': ip})
                if now - p['disconnected_at'] >= self.retention:
                    events.append({'event': 'peer_tracking_expired', 'ip': ip})
                    del self.peers[ip]
        new = {}
        for r in rows:
            key = r.pop('key')
            old = self.previous.get(key)
            r['delta'] = None
            r['elapsed_seconds'] = None
            # Without a socket cookie a reused 4-tuple cannot be identified reliably.
            if old and r['socket_id'] and now > old[0]:
                elapsed = now - old[0]
                r['elapsed_seconds'] = elapsed
                r['delta'] = {k: (v - old[1][k] if v is not None and old[1].get(k) is not None and v >= old[1][k] else None)
                              for k, v in r['counters'].items()}
                for direction, counter in [('tx', 'bytes_sent'), ('rx', 'bytes_received')]:
                    delta = r['delta'][counter]
                    r[direction + '_bps'] = None if delta is None else delta * 8 / elapsed
            new[key] = (now, r['counters'].copy())
        self.previous = new
        return events


def performance():
    paths = ['/proc/stat', '/proc/meminfo', '/proc/vmstat', '/proc/loadavg', '/proc/uptime',
             '/proc/interrupts', '/proc/softirqs', '/proc/net/softnet_stat', '/proc/net/snmp',
             '/proc/net/netstat', '/proc/net/sockstat', '/proc/net/sockstat6', '/proc/diskstats',
             '/proc/pressure/cpu', '/proc/pressure/memory', '/proc/pressure/io']
    result = {'proc': {p: read(p) for p in paths}, 'interfaces': {}, 'routers': {}}
    for nic in Path('/sys/class/net').iterdir():
        result['interfaces'][nic.name] = {f: read(nic / f) for f in ('speed', 'duplex', 'operstate', 'carrier', 'mtu')}
    result['qdisc'] = command(['tc', '-s', 'qdisc', 'show'])
    result['top_cpu_lifetime'] = command(['ps', '-eo', 'pid,comm,etimes,pcpu,pmem,rss,nlwp,stat', '--sort=-pcpu'])
    result['top_cpu_lifetime']['stdout'] = '\n'.join(result['top_cpu_lifetime']['stdout'].splitlines()[:21])
    for proc in Path('/proc').iterdir():
        if proc.name.isdigit() and read(proc / 'comm') in ('rmw_zenohd', 'zenohd'):
            entry = {f: read(proc / f) for f in ('stat', 'status', 'io', 'limits', 'cgroup')}
            try:
                entry['fd_count'] = len(list((proc / 'fd').iterdir()))
                entry['threads'] = {t.name: {'stat': read(t / 'stat'), 'schedstat': read(t / 'schedstat')} for t in (proc / 'task').iterdir()}
            except OSError as e:
                entry['error'] = str(e)
            result['routers'][proc.name] = entry
    result['collector'] = {f: read('/proc/self/' + f) for f in ('stat', 'status', 'io')}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    parser.add_argument('--duration', type=int, required=True)
    parser.add_argument('--retain-seconds', type=int, default=300)
    parser.add_argument('--skip-ping', nargs='*', default=[])
    args = parser.parse_args()
    if args.retain_seconds < 0 or args.duration < 1:
        parser.error('duration must be positive; retention must be nonnegative')
    out = Path(args.output)
    logs = {k: RotatingLog(out / name) for k, name in [('tcp', 'tcp.log'), ('peers', 'peers.jsonl'), ('performance', 'performance.jsonl'), ('health', 'metrics-status.jsonl')]}
    def emit(kind, data):
        logs[kind].write(json.dumps({'time_utc': timestamp(), 'monotonic': time.monotonic(), **data}) + '\n')
    stopped = False
    def stop(*_):
        nonlocal stopped
        stopped = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    tracker = Tracker(args.retain_seconds)
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=4)
    pending, last_probe = {}, {}
    start = time.monotonic()
    next_perf = start
    emit('health', {'event': 'start', 'retention_seconds': args.retain_seconds, 'probe_interval_seconds': 10, 'probe_workers': 4})
    try:
        while not stopped and time.monotonic() - start < args.duration:
            tick = time.monotonic()
            ss = command(['ss', '-tinpe', '( sport = :7447 or dport = :7447 )'])
            logs['tcp'].write(timestamp() + '\n' + ss['stdout'] + ss['stderr'])
            if ss['returncode'] == 0:
                rows = parse_ss(ss['stdout'])
                events = tracker.update(rows, tick)
                emit('peers', {'event': 'sample', 'connections': rows, 'changes': events, 'tracked_peers': tracker.peers})
            else:
                # A failed sample is not evidence that every peer disconnected.
                emit('health', {'event': 'ss_failed', **ss})
            for ip, future in list(pending.items()):
                if future.done():
                    emit('peers', {'event': 'ping', 'ip': ip, 'result': future.result()})
                    del pending[ip]
            for ip in tracker.peers:
                if len(pending) >= 4:
                    break
                if ip not in args.skip_ping and ip not in pending and tick - last_probe.get(ip, -1e9) >= 10:
                    pending[ip] = pool.submit(command, ['ping', '-n', '-c', '1', '-W', '1', ip], 2)
                    last_probe[ip] = tick
            last_probe = {ip: t for ip, t in last_probe.items() if ip in tracker.peers or ip in pending}
            if tick >= next_perf:
                emit('performance', performance())
                next_perf = time.monotonic() + 5
            elapsed = time.monotonic() - tick
            if elapsed > 1:
                emit('health', {'event': 'sample_overrun', 'seconds': elapsed})
            time.sleep(max(0, min(1 - elapsed, start + args.duration - time.monotonic())))
    finally:
        pool.shutdown(wait=True, cancel_futures=True)
        for ip, future in pending.items():
            if not future.cancelled():
                emit('peers', {'event': 'ping', 'ip': ip, 'result': future.result()})
        emit('health', {'event': 'stop', 'interrupted': stopped})
        for log in logs.values():
            log.close()


if __name__ == '__main__':
    main()
