import copy
import json
import subprocess
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from tools.app_statistics import collect, query, render

EMPTY = {'users': {'enabled': 0, 'disabled': 0}, 'registrations': {'24h': 0, '7d': 0, '30d': 0},
         'books': {'digital': 0, 'physical': 0}, 'distinct_uploaded_contents': 0, 'files': []}


class CollectionTests(unittest.TestCase):
    def test_failure_preserves_snapshot_and_timestamp_then_recovers(self):
        snapshot = copy.deepcopy(EMPTY)
        snapshot['users']['enabled'] = 3
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'app.prom'
            with patch('tools.app_statistics.time.time', return_value=1000):
                collect(path, lambda: snapshot)
            with self.assertRaises(RuntimeError):
                collect(path, lambda: (_ for _ in ()).throw(subprocess.TimeoutExpired('psql', 15)))
            failed = path.read_text()
            self.assertIn('papyrus_app_users{status="enabled"} 3\n', failed)
            self.assertIn('papyrus_app_statistics_timestamp_seconds 1000\n', failed)
            self.assertIn('papyrus_app_collection_success 0\n', failed)
            with patch('tools.app_statistics.time.time', return_value=2000):
                collect(path, lambda: EMPTY)
            self.assertIn('papyrus_app_statistics_timestamp_seconds 2000\n', path.read_text())
            self.assertIn('papyrus_app_users{status="enabled"} 0\n', path.read_text())

    def test_first_failure_does_not_fabricate_zero_counts(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'app.prom'
            with self.assertRaises(RuntimeError):
                collect(path, lambda: {})
            self.assertNotIn('papyrus_app_users', path.read_text())
            self.assertNotIn('papyrus_app_statistics_timestamp_seconds', path.read_text())
            self.assertIn('papyrus_app_collection_success 0', path.read_text())

    def test_rejects_unbounded_labels_and_invalid_values(self):
        for row in [{'kind': 'book_file', 'format': 'private-title', 'files': 1, 'bytes': 2},
                    {'kind': 'book_file', 'format': 'epub', 'files': 1, 'bytes': -2}]:
            snapshot = {**EMPTY, 'files': [row]}
            with self.assertRaises(ValueError):
                render(snapshot)


class DatabaseStatisticsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.container = 'papyrus-app-statistics-test-' + uuid.uuid4().hex[:12]
        cls.addClassCleanup(subprocess.run, ['docker', 'rm', '-f', cls.container],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        image = 'postgres:17-alpine@sha256:18cfe3ef5e6815560c98237d6216d1e5119702fb0f3894c8785dd58b8bbe5d73'
        subprocess.run(['docker', 'run', '-d', '--name', cls.container, '--network', 'none',
                        '--tmpfs', '/var/lib/postgresql/data', '-e', 'POSTGRES_HOST_AUTH_METHOD=trust',
                        image], check=True, stdout=subprocess.DEVNULL)
        deadline = time.monotonic() + 60
        while subprocess.run(['docker', 'exec', cls.container, 'pg_isready', '-U', 'postgres'],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode:
            if time.monotonic() > deadline:
                raise RuntimeError('Disposable database did not start')
            time.sleep(1)
        cls.command = ['docker', 'exec', '-i', cls.container, 'psql', '-XqAt', '-v', 'ON_ERROR_STOP=1', '-U', 'postgres']
        cls.sql('''
            CREATE TABLE users (user_id integer PRIMARY KEY, disabled_at timestamptz, created_at timestamptz);
            CREATE TABLE books (book_id integer PRIMARY KEY, owner_user_id integer REFERENCES users ON DELETE CASCADE, is_physical boolean NOT NULL);
            CREATE TABLE media_assets (book_id integer REFERENCES books ON DELETE CASCADE, kind text, extension text, size_bytes integer, sha256 text);
        ''')

    @classmethod
    def sql(cls, text):
        return subprocess.run(cls.command, input=text, check=True, capture_output=True, text=True).stdout

    def setUp(self):
        self.sql('TRUNCATE users, books, media_assets;')

    def test_empty_database_and_zero_buckets(self):
        self.assertEqual(query(self.command), EMPTY)
        text = render(EMPTY)
        self.assertIn('papyrus_app_files{kind="book_file",format="epub"} 0\n', text)
        self.assertIn('papyrus_app_file_bytes{kind="cover_image",format="png"} 0\n', text)

    def test_counts_duplicates_formats_registrations_and_cascaded_deletion(self):
        self.sql('''
            INSERT INTO users VALUES
                (1, NULL, now() - interval '2 hours'),
                (2, now(), now() - interval '3 days'),
                (3, NULL, now() - interval '15 days'),
                (4, NULL, now() - interval '40 days');
            INSERT INTO books VALUES (1,1,false), (2,2,false), (3,3,true), (4,4,false);
            INSERT INTO media_assets VALUES
                (1,'book_file','epub',100,repeat('a',64)),
                (2,'book_file','EPUB',100,repeat('A',64)),
                (1,'cover_image','jpg',25,repeat('b',64)),
                (4,'book_file','private-title',75,repeat('c',64)),
                (4,'unknown-kind','private-extension',10,repeat('d',64));
        ''')
        snapshot = query(self.command)
        self.assertEqual(snapshot['users'], {'enabled': 3, 'disabled': 1})
        self.assertEqual(snapshot['registrations'], {'24h': 1, '7d': 2, '30d': 3})
        self.assertEqual(snapshot['books'], {'digital': 3, 'physical': 1})
        self.assertEqual(snapshot['distinct_uploaded_contents'], 2)
        buckets = {(row['kind'], row['format']): (row['files'], row['bytes']) for row in snapshot['files']}
        self.assertEqual(buckets[('book_file', 'epub')], (2, 200))
        self.assertEqual(buckets[('book_file', 'other')], (1, 75))
        self.assertEqual(buckets[('other', 'other')], (1, 10))
        self.assertEqual(sum(row['bytes'] for row in snapshot['files']), 310)
        self.assertNotIn('private-', json.dumps(snapshot))
        self.assertNotIn('a' * 64, render(snapshot))
        self.sql('DELETE FROM users WHERE user_id = 1;')
        after = query(self.command)
        self.assertEqual(after['users']['enabled'], 2)
        self.assertEqual(after['registrations']['24h'], 0)
        self.assertEqual(after['books']['digital'], 2)
        self.assertEqual(after['distinct_uploaded_contents'], 2)
        self.assertEqual(sum(row['bytes'] for row in after['files']), 185)

    def test_production_query_rejects_writes(self):
        original = Path('monitoring/app-statistics.sql').read_text()
        modified = original.replace('SELECT jsonb_build_object(', 'DELETE FROM public.users;\nSELECT jsonb_build_object(', 1)
        with patch('tools.app_statistics.Path.read_text', return_value=modified):
            with self.assertRaises(subprocess.CalledProcessError):
                query(self.command)
