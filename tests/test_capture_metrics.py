import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import signal
import subprocess
import json

spec = importlib.util.spec_from_file_location('capture_metrics', Path(__file__).resolve().parents[1] / 'scripts/capture-metrics.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def sample(cookie='abc', sent=100, received=200, ip='192.168.20.73'):
    return m.parse_ss(f'ESTAB 0 6 192.168.1.70:7447 {ip}:12345 users:(("rmw_zenohd",pid=12,fd=3)) ino:22 sk:{cookie}\n\t cubic rtt:2.5/1 rto:205 cwnd:10 bytes_sent:{sent} bytes_received:{received} retrans:0/2\n')


class CaptureMetricsTest(unittest.TestCase):
    def test_exported_shell_rotation_retains_latest_generations(self):
        source = (Path(__file__).resolve().parents[1] / 'scripts/zenoh-capture.sh').read_text()
        rotate = source[source.index('rotate() {'):source.index('export -f rotate')]
        rotate = rotate.replace('max=33554432', 'max=4')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'stream.log'
            subprocess.run(['bash', '-c', rotate + "\nexport -f rotate\nbash -c 'seq 10 29 | rotate \"$1\"' _ \"$1\"", '_', str(path)], check=True)
            self.assertEqual(path.read_text(), '29\n')
            self.assertEqual(Path(str(path) + '.15').read_text(), '14\n')
            self.assertEqual(len(list(Path(directory).iterdir())), 16)

    def test_default_runs_beyond_hour_until_signal(self):
        self.check_duration([], 4002)

    def test_explicit_duration_stops(self):
        self.check_duration(['--duration', '2'], 2)

    def check_duration(self, extra, expected):
        clock = [0]
        handlers = {}
        def sleep(seconds):
            clock[0] += seconds
            if not extra and clock[0] >= 4002:
                handlers[signal.SIGTERM]()
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch('sys.argv', ['capture-metrics', '--output', directory] + extra), \
                 mock.patch.object(m.time, 'monotonic', side_effect=lambda: clock[0]), \
                 mock.patch.object(m.time, 'sleep', side_effect=sleep), \
                 mock.patch.object(m.signal, 'signal', side_effect=lambda sig, fn: handlers.update({sig: fn})), \
                 mock.patch.object(m, 'command', return_value={'returncode': 0, 'stdout': '', 'stderr': ''}), \
                 mock.patch.object(m, 'performance', return_value={}):
                m.main()
            self.assertEqual(clock[0], expected)
            records = [json.loads(line) for line in (Path(directory) / 'metrics-status.jsonl').read_text().splitlines()]
            self.assertEqual(records[-1]['event'], 'stop')
            self.assertEqual(records[-1]['interrupted'], not bool(extra))

    def test_counter_rates_and_reconnection(self):
        t = m.Tracker(300)
        t.update(sample(), 1)
        rows = sample(sent=150, received=300)
        t.update(rows, 3)
        self.assertEqual(rows[0]['tx_bps'], 200)
        self.assertEqual(rows[0]['rx_bps'], 400)
        rows = sample(cookie='def', sent=5)
        t.update(rows, 4)
        self.assertIsNone(rows[0]['delta'])

    def test_retention_from_disconnect_and_reconnect(self):
        t = m.Tracker(300)
        self.assertEqual(t.update(sample(), 0)[0]['event'], 'peer_connected')
        self.assertEqual(t.update([], 10)[0]['event'], 'peer_disconnected')
        t.update([], 309)
        self.assertIn('192.168.20.73', t.peers)
        self.assertEqual(t.update(sample(cookie='def'), 309.5)[0]['event'], 'peer_reconnected')
        t.update([], 400)
        self.assertEqual(t.update([], 700)[0]['event'], 'peer_tracking_expired')
        self.assertFalse(t.peers)

    def test_ip_change_preserves_old_tracking(self):
        t = m.Tracker(300)
        t.update(sample(), 0)
        t.update(sample(ip='192.168.20.74'), 1)
        self.assertEqual(len(t.peers), 2)
        self.assertFalse(t.peers['192.168.20.73']['connected'])

    def test_ipv6_and_loopback(self):
        self.assertEqual(m.peer_ip('[2001:db8::1]:123'), '2001:db8::1')
        self.assertIsNone(m.peer_ip('[::1]:123'))
        self.assertEqual(m.peer_ip('[::ffff:192.168.1.2]:123'), '192.168.1.2')

    def test_missing_and_reset_counters(self):
        t = m.Tracker(300)
        t.update(sample(), 1)
        rows = sample(sent=1)
        t.update(rows, 2)
        self.assertIsNone(rows[0]['tx_bps'])
        self.assertIsNone(rows[0]['delta']['bytes_acked'])

    def test_rotation(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'log'
            log = m.RotatingLog(p, limit=9, count=16)
            for i in range(25):
                log.write(f'{i:08d}\n')
            log.close()
            self.assertEqual(len(list(Path(d).iterdir())), 16)
            for age in range(16):
                file = p if age == 0 else p.with_name('log.' + str(age))
                self.assertEqual(file.read_text(), f'{24-age:08d}\n')

if __name__ == '__main__':
    unittest.main()
