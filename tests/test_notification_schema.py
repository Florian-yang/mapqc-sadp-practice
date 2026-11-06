from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import threading
import unittest
from check_collect.auth import Store
from check_collect.collection import Collection
from check_collect.notifications import Notifications


class NotificationSchemaTests(unittest.TestCase):
    def test_web_and_message_workers_can_initialize_one_database_concurrently(self):
        with tempfile.TemporaryDirectory() as folder:
            path=str(Path(folder)/'shared.sqlite');barrier=threading.Barrier(4)
            def initialize(_):
                store=Store(path);barrier.wait(timeout=5)
                Collection(store);Notifications(store)
                with store.connect() as db:
                    return {r['name'] for r in db.execute('PRAGMA table_info(event_outbox)')}
            with ThreadPoolExecutor(max_workers=4) as pool:
                columns=list(pool.map(initialize,range(4)))
            self.assertTrue(all({'published_ms','publish_attempts','last_error'}<=c for c in columns))
