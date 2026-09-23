"""Scanner correctness and HTTP boundaries, using disposable files only."""
import contextlib
import http.client
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import Handler, Monitor, Server, discover_config, scan_volume


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.mountinfo = Path(self.temp.name) / 'mountinfo'

    def tearDown(self):
        self.temp.cleanup()

    def test_discovers_storage_and_filters_virtual_and_duplicate_mounts(self):
        self.mountinfo.write_text("""10 1 8:1 / /host ro - ext4 /dev/root rw
11 10 0:1 / /host/proc ro - proc proc rw
12 10 8:2 / /host/mnt/archive ro - ext4 /dev/sdb1 rw
13 10 8:1 /etc /host/etc/bind ro - ext4 /dev/root rw
14 1 8:1 /var/lib/orbit /data rw - ext4 /dev/root rw
""")
        config = discover_config('/host', self.mountinfo, '/data')
        self.assertEqual([volume['id'] for volume in config['volumes']], ['system', 'archive'])
        self.assertEqual([volume['mount'] for volume in config['volumes']], ['/', '/mnt/archive'])
        self.assertEqual(config['volumes'][1]['group'], 'Disks')
        self.assertEqual(config['volumes'][1]['device'], '/dev/sdb1')
        excluded = config['volumes'][0]['exclude']
        self.assertNotIn('mnt/archive', excluded)
        self.assertIn('etc/bind', excluded)
        self.assertIn('var/lib/orbit', excluded)

    def test_ids_are_stable_for_duplicate_names_and_escaped_paths(self):
        self.mountinfo.write_text("""10 1 8:1 / /host ro - ext4 /dev/root rw
12 10 8:2 / /host/mnt/data ro - ext4 /dev/sdb1 rw
13 10 8:3 / /host/srv/data ro - ext4 /dev/sdc1 rw
14 10 8:4 / /host/media/My\\040Disk ro - ext4 /dev/sdd1 rw
""")
        first = discover_config('/host', self.mountinfo, '/data')['volumes']
        second = discover_config('/host', self.mountinfo, '/data')['volumes']
        self.assertEqual([volume['id'] for volume in first], [volume['id'] for volume in second])
        ids = [volume['id'] for volume in first]
        self.assertIn('data', ids)
        self.assertEqual(sum(identifier.startswith('data') for identifier in ids), 2)
        escaped = next(volume for volume in first if volume['mount'] == '/media/My Disk')
        self.assertEqual(escaped['id'], 'My-Disk')
        self.assertEqual(escaped['name'], 'My Disk')

    def test_rejects_a_writable_host_submount(self):
        self.mountinfo.write_text("""10 1 8:1 / /host ro - ext4 /dev/root rw
11 10 8:2 / /host/mnt/archive rw - ext4 /dev/sdb1 rw
""")
        with self.assertRaisesRegex(ValueError, 'recursively read-only'):
            discover_config('/host', self.mountinfo, '/data')


class ScannerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.root = self.base / 'root'
        self.root.mkdir()
        self.volume = {'id': 'test', 'name': 'Test', 'mount': '/', 'path': str(self.root)}
        self.db_path = self.base / 'scan.sqlite'

    def tearDown(self):
        self.temp.cleanup()

    def scan(self):
        return scan_volume(self.volume, self.db_path, threading.Event(), lambda value: None)

    def rows(self):
        with sqlite3.connect(self.db_path) as db:
            db.row_factory = sqlite3.Row
            return [dict(r) for r in db.execute('SELECT * FROM nodes')]

    def test_allocated_bytes_match_du_with_sparse_files_and_hardlinks(self):
        (self.root / 'nested').mkdir()
        (self.root / 'nested' / 'data').write_bytes(b'x' * 14000)
        os.link(self.root / 'nested' / 'data', self.root / 'hardlink')
        with (self.root / 'sparse').open('wb') as file:
            file.seek(8 * 1024 * 1024)
            file.write(b'!')
        (self.root / 'empty').mkdir()
        result = self.scan()
        root = self.rows()[0]
        du = int(subprocess.check_output(['du', '-s', '-B1', str(self.root)]).split()[0])
        self.assertEqual(root['size'], du)
        self.assertEqual(result['hardlinks'], 1)
        self.assertEqual(root['apparent'], 14000 + 8 * 1024 * 1024 + 1)
        self.assertEqual(root['files'], 3)
        self.assertEqual(root['dirs'], 2)

    def test_symlinks_are_listed_but_never_followed(self):
        outside = self.base / 'outside'
        outside.mkdir()
        (outside / 'secret').write_bytes(b'x' * 100000)
        (self.root / 'loop').symlink_to(self.root, target_is_directory=True)
        (self.root / 'external').symlink_to(outside, target_is_directory=True)
        (self.root / 'dangling').symlink_to(self.base / 'missing')
        self.scan()
        rows = self.rows()
        self.assertEqual(len(rows), 4)
        self.assertTrue(all(r['kind'] == 'symlink' for r in rows[1:]))
        self.assertFalse(any(r['name'] == 'secret' for r in rows))

    def test_excluded_directories_and_mount_boundaries(self):
        for name in ['excluded', 'mounted', 'included']:
            (self.root / name).mkdir()
            (self.root / name / 'data').write_bytes(b'a' * 8192)
        self.volume['exclude'] = ['excluded']
        original_stat = os.stat
        def fake_stat(path, *args, **kwargs):
            result = original_stat(path, *args, **kwargs)
            if path == 'mounted' and kwargs.get('dir_fd') is not None:
                values = list(result)
                values[2] = result.st_dev + 999
                # stat_result does not preserve st_blocks from its sequence.
                class DifferentDevice:
                    def __getattr__(self, key):
                        return result.st_dev + 999 if key == 'st_dev' else getattr(result, key)
                return DifferentDevice()
            return result
        with patch('app.os.stat', fake_stat):
            result = self.scan()
        rows = self.rows()
        self.assertEqual(result['excluded'], 2)
        self.assertEqual([r['path'] for r in rows if r['kind'] == 'file'], ['included/data'])

    def test_unreadable_entry_is_reported_and_does_not_abort(self):
        (self.root / 'denied').write_text('x')
        (self.root / 'ok').write_text('hello')
        original_stat = os.stat
        def fake_stat(path, *args, **kwargs):
            if path == 'denied' and kwargs.get('dir_fd') is not None:
                raise PermissionError(13, 'Permission denied', path)
            return original_stat(path, *args, **kwargs)
        with patch('app.os.stat', fake_stat):
            result = self.scan()
        self.assertEqual(result['skipped'], 1)
        self.assertEqual(result['errors'][0]['path'], 'denied')
        self.assertEqual(result['files'], 1)

    def test_unusual_filenames_are_serializable(self):
        (self.root / '<script> & "quotes"\n100%_file').write_text('safe')
        fd = os.open(os.fsencode(self.root) + b'/invalid-\xff', os.O_CREAT | os.O_WRONLY, 0o600)
        os.close(fd)
        self.scan()
        json.dumps(self.rows()).encode('utf-8')
        self.assertEqual(len(self.rows()), 3)

    def test_cancel_stops_the_scan(self):
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(InterruptedError):
            scan_volume(self.volume, self.db_path, cancel, lambda value: None)

    def test_symlink_root_is_rejected(self):
        (self.base / 'alias').symlink_to(self.root, target_is_directory=True)
        self.volume['path'] = str(self.base / 'alias')
        with self.assertRaises(ValueError):
            self.scan()


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.root = self.base / 'root'
        self.root.mkdir()
        for i in range(35):
            folder = self.root / f'directory-{i}'
            folder.mkdir()
            (folder / f'file-{i}').write_bytes(b'z' * (5000 + i * 1024))
        (self.root / 'literal-100%_name').write_text('find me')
        (self.root / 'literal-100XYname').write_text('not this')
        self.volume = {'id': 'test', 'name': 'Test', 'mount': '/', 'path': str(self.root)}
        self.monitor = Monitor({'hostname': 'testhost', 'volumes': [self.volume], 'scan_hours': 0}, self.base / 'data')
        self.meta = scan_volume(self.volume, self.monitor.snapshot('test'), threading.Event(), lambda value: None)
        self.monitor.sample()

    def tearDown(self):
        self.monitor.close()
        self.temp.cleanup()

    def test_tree_totals_pagination_and_chart_remainders(self):
        tree = self.monitor.tree('test', 1, limit=10)
        self.assertEqual(tree['count'], 37)
        self.assertEqual(len(tree['children']), 10)
        self.assertEqual(sum(c['size'] for c in tree['chart']['children']), tree['node']['size'])
        self.assertTrue(any(c['kind'] == 'aggregate' for c in tree['chart']['children']))
        second = self.monitor.tree('test', 1, limit=10, offset=10)
        self.assertFalse(set(c['id'] for c in tree['children']) & set(c['id'] for c in second['children']))
        child = next(c for c in tree['children'] if c['kind'] == 'directory')
        self.assertEqual(self.monitor.tree('test', child['id'])['ancestors'][0]['id'], 1)

    def test_search_is_literal_and_largest_files_are_sorted(self):
        self.assertEqual(len(self.monitor.search('test', '100%_')['items']), 1)
        self.assertEqual(self.monitor.search('test', "' OR 1=1 --")['items'], [])
        results = self.monitor.search('test')['items']
        sizes = [r['size'] for r in results]
        self.assertEqual(sizes, sorted(sizes, reverse=True))
        self.assertTrue(all(r['kind'] == 'file' for r in results))

    def test_dense_chart_keeps_the_largest_top_level_folders(self):
        for folder in self.root.glob('directory-*'):
            for i in range(25):
                (folder / f'extra-{i}').write_bytes(b'x' * 4096)
        rebuilt = self.base / 'dense.sqlite'
        scan_volume(self.volume, rebuilt, threading.Event(), lambda value: None)
        os.replace(rebuilt, self.monitor.snapshot('test'))
        tree = self.monitor.tree('test', 1)
        expected = {n['id'] for n in tree['children'][:24]}
        visible = {n['id'] for n in tree['chart']['children'] if n['kind'] != 'aggregate'}
        self.assertEqual(visible, expected)

    def test_cancelled_rescan_preserves_last_good_snapshot(self):
        started = threading.Event()
        def delayed_scan(volume, destination, cancel, progress):
            Path(destination).write_bytes(b'partial')
            started.set()
            cancel.wait(3)
            raise InterruptedError()
        self.monitor.worker.start()
        with patch('app.scan_volume', delayed_scan):
            self.monitor.enqueue('test')
            self.assertTrue(started.wait(2))
            self.monitor.cancel('test')
            self.monitor.queue.join()
        self.assertEqual(self.monitor.scan_metadata('test'), self.meta)
        self.assertFalse((self.base / 'data' / 'test.scanning.sqlite').exists())
        self.assertEqual(self.monitor.jobs['test']['status'], 'cancelled')

    def test_targets_are_independent_and_have_no_aggregate_capacity(self):
        config = {'volumes': [self.volume, {**self.volume, 'id': 'second'}]}
        other = Monitor(config, self.base / 'other')
        other.sample()
        status = other.status()
        self.assertEqual(len(status['volumes']), 2)
        self.assertEqual(status['volumes'][0]['total'], status['volumes'][1]['total'])
        self.assertNotIn('totals', status)

    def test_name_mount_and_group_default_to_generic_paths(self):
        other = Monitor({'volumes': [{'id': 'plain', 'path': str(self.root)}]}, self.base / 'plain')
        self.assertEqual(other.volumes['plain']['name'], str(self.root))
        self.assertEqual(other.volumes['plain']['mount'], str(self.root))
        self.assertEqual(other.volumes['plain']['group'], 'File systems')

    def test_request_boundaries(self):
        server = Server(('127.0.0.1', 0), Handler)
        server.monitor = self.monitor
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def request(path, method='GET', body=None, headers=None):
            connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
            connection.request(method, path, body, headers or {})
            response = connection.getresponse()
            result = response.status, response.read()
            connection.close()
            return result
        try:
            self.assertEqual(request('/api/status')[0], 200)
            self.assertEqual(request('/api/tree?volume=../../etc/passwd')[0], 404)
            self.assertEqual(request('/api/tree?volume=test&node=garbage')[0], 400)
            self.assertEqual(request('/api/tree?volume=test&node=999999')[0], 404)
            self.assertEqual(request('/../app.py')[0], 404)
            self.assertEqual(request('/api/scan', 'POST', '{"volume":"test"}')[0], 403)
            headers = {'Content-Type': 'application/json', 'X-Orbit-Request': '1', 'Origin': 'https://other.example'}
            self.assertEqual(request('/api/scan', 'POST', '{"volume":"test"}', headers)[0], 403)
            del headers['Origin']
            self.assertEqual(request('/api/scan', 'POST', '[]', headers)[0], 400)
            self.assertEqual(request('/api/cancel', 'POST', '{"volume":"test"}', headers)[0], 200)
        finally:
            server.shutdown()
            thread.join()
            server.server_close()


if __name__ == '__main__':
    unittest.main()
