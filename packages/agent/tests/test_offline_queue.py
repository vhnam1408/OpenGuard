"""Durability, lifecycle and ACK tests without network or privileged actions."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "packages/agent/src"), str(ROOT / "packages/contracts/src")]

from ogk_agent.storage.checkpoints import SQLiteCheckpointStore
from ogk_agent.storage.offline_queue import HTTPSBatchUploader, OfflineQueue, default_database_path
from ogk_contracts.sync import EventBatchAck


class OfflineQueueTests(unittest.TestCase):
    def setUp(self):
        root = (ROOT / "runtime/agent-tests").resolve()
        root.mkdir(parents=True, exist_ok=True)
        self.directory = tempfile.TemporaryDirectory(dir=root)
        self.assertTrue(Path(self.directory.name).resolve().is_relative_to(root))
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "agent.db"
        self.now = datetime.now(timezone.utc)
        self.queue = self.open_queue()
        self.addCleanup(lambda: self.queue.close())
        self.queue.configure_binding("b1", device_id="d1", child_id="c1", data_epoch=1)

    def open_queue(self):
        return OfflineQueue(self.path, utcnow=lambda: self.now)

    def event(self, **changes):
        return {"ts": self.now, "device_id": "d1", "child_id": "c1", "type": "app_start",
                "subject": "notepad.exe", "duration_sec": 0, "policy_id": "p1", **changes}

    def enqueue(self, **changes):
        return self.queue.enqueue(self.event(**changes), binding_id="b1", data_epoch=1)

    def ack(self, batch, **changes):
        return EventBatchAck(batch_id=batch.batch_id, binding_id=batch.binding_id, data_epoch=batch.data_epoch,
                             server_time=self.now, accepted_keys=changes.pop("accepted_keys", [x.idempotency_key for x in batch.events]),
                             **changes)

    def test_persistence_after_restart_and_failed_upload(self):
        key = self.enqueue()
        first = self.queue.next_batch("b1")
        with self.assertRaises(TimeoutError):
            self.queue.sync_once("b1", Mock(side_effect=TimeoutError), prepare_sync=lambda: True)
        self.queue.close()
        self.queue = self.open_queue()
        self.assertEqual(first, self.queue.next_batch("b1"))
        self.assertEqual(first.events[0].idempotency_key, key)
        self.assertEqual(self.queue.sync_once("b1", self.ack, prepare_sync=lambda: True), 1)
        self.assertEqual(self.queue.pending_count("b1"), 0)

    def test_wrong_partial_duplicate_and_unknown_ack(self):
        one, two = self.enqueue(), self.enqueue(subject="calculator.exe")
        batch = self.queue.next_batch("b1")
        bad = self.ack(batch).model_copy(update={"batch_id": "wrong"})
        for ack in [bad, self.ack(batch, accepted_keys=[one, one]), self.ack(batch, accepted_keys=["unknown"])]:
            with self.assertRaises(ValueError):
                self.queue.acknowledge(batch, ack)
        self.assertEqual(self.queue.pending_count("b1"), 2)
        self.assertEqual(self.queue.acknowledge(batch, self.ack(batch, accepted_keys=[one])), 1)
        retry = self.queue.next_batch("b1")
        self.assertEqual(retry.batch_id, batch.batch_id)
        self.assertEqual([x.idempotency_key for x in retry.events], [two])
        self.assertEqual(self.queue.acknowledge(batch, self.ack(batch, accepted_keys=[one])), 0)

    def test_epoch_advance_revocation_and_inflight_ack(self):
        self.enqueue()
        batch = self.queue.next_batch("b1")
        self.queue.configure_binding("b1", device_id="d1", child_id="c1", data_epoch=2)
        self.assertEqual(self.queue.pending_count("b1"), 0)
        with self.assertRaises(ValueError):
            self.queue.acknowledge(batch, self.ack(batch))
        with self.assertRaises(ValueError):
            self.enqueue()
        with self.assertRaises(ValueError):
            self.queue.configure_binding("b1", device_id="d1", child_id="c1", data_epoch=1)
        self.queue.revoke_binding("b1")
        with self.assertRaises(ValueError):
            self.queue.configure_binding("b1", device_id="d1", child_id="c1", data_epoch=3)

    def test_lifecycle_preflight_pauses_or_deletes_before_network(self):
        self.enqueue()
        send = Mock()
        self.assertEqual(self.queue.sync_once("b1", send, prepare_sync=lambda: False), 0)
        send.assert_not_called()
        def prepare():
            self.queue.configure_binding("b1", device_id="d1", child_id="c1", data_epoch=2)
            return True
        self.assertEqual(self.queue.sync_once("b1", send, prepare_sync=prepare), 0)
        send.assert_not_called()

    def test_revocation_before_first_configuration_keeps_tombstone(self):
        self.queue.revoke_binding("b2")
        with self.assertRaises(ValueError):
            self.queue.configure_binding("b2", device_id="d2", child_id="c2", data_epoch=1)

    def test_retention_uses_event_occurrence(self):
        self.enqueue(ts=self.now - timedelta(days=89))
        self.now += timedelta(days=2)
        self.assertEqual(self.queue.pending_count("b1"), 0)
        with self.assertRaises(ValueError):
            self.enqueue(ts=self.now - timedelta(days=91))

    def test_event_validation_and_scoping(self):
        for changes in [{"child_id": "other"}, {"subject": "https://example.test/path"}, {"type": "screenshot"}]:
            with self.assertRaises(ValueError):
                self.enqueue(**changes)
        self.assertEqual(self.queue.pending_count("b1"), 0)

    def test_enqueue_idempotency_and_key_conflict(self):
        kwargs = dict(binding_id="b1", data_epoch=1, idempotency_key="stable")
        self.queue.enqueue(self.event(), **kwargs)
        self.queue.enqueue(self.event(), **kwargs)
        with self.assertRaises(ValueError):
            self.queue.enqueue(self.event(subject="calculator.exe"), **kwargs)
        self.assertEqual(self.queue.pending_count("b1"), 1)

    def test_bounded_fifo_batches_and_concurrent_producers(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            keys = list(pool.map(lambda i: self.enqueue(subject=f"fixture-{i}.exe"), range(105)))
        batch = self.queue.next_batch("b1")
        self.assertEqual(len(batch.events), 100)
        self.assertEqual(len(set(keys)), 105)
        self.queue.acknowledge(batch, self.ack(batch))
        self.assertEqual(len(self.queue.next_batch("b1").events), 5)

    def test_shares_database_with_checkpoint_store(self):
        checkpoint = SQLiteCheckpointStore(self.path)
        try:
            self.enqueue()
            self.assertIsNone(checkpoint.load("b1"))
            with closing(sqlite3.connect(self.path)) as db:
                self.assertEqual(db.execute("PRAGMA journal_mode").fetchone()[0], "wal")
            self.assertEqual(self.queue.pending_count("b1"), 1)
        finally:
            checkpoint.close()

    def test_multiple_connections_do_not_change_batch_membership(self):
        self.enqueue()
        with self.open_queue() as second:
            first = self.queue.next_batch("b1", batch_size=1)
            self.assertEqual(first, second.next_batch("b1"))

    def test_https_transport_and_default_path(self):
        with patch.dict(os.environ, {"ProgramData": "C:/ProgramData"}):
            self.assertEqual(default_database_path(), Path("C:/ProgramData/OpenGuardKids/state/agent.db"))
        for origin in ["http://example.test", "https://user:pass@example.test", "https://example.test/?token=bad"]:
            with self.assertRaises(ValueError):
                HTTPSBatchUploader(origin, lambda: "token")
        self.enqueue()
        batch = self.queue.next_batch("b1")
        response = Mock(status=200)
        response.read.return_value = self.ack(batch).model_dump_json().encode()
        opener = Mock()
        opener.open.return_value.__enter__ = Mock(return_value=response)
        opener.open.return_value.__exit__ = Mock(return_value=False)
        uploader = HTTPSBatchUploader("https://example.test", lambda: "synthetic-token", opener=opener)
        self.assertEqual(uploader(batch), self.ack(batch))
        request = opener.open.call_args.args[0]
        self.assertEqual(request.get_header("Authorization"), "Bearer synthetic-token")
        self.assertEqual(request.full_url, "https://example.test/api/v1/events")


if __name__ == "__main__":
    unittest.main()
