from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from copy_trader.central.hub_server import SignalStore


class HubIdempotencyTests(unittest.TestCase):
    def test_same_exact_event_id_is_stored_once(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "signals.jsonl"
            store = SignalStore(path)
            first = store.publish({"event_id": "line_event_1", "type": "trade_signal"})
            retry = store.publish({"event_id": "line_event_1", "type": "trade_signal"})
            self.assertEqual(first["seq"], retry["seq"])
            self.assertTrue(retry["already_published"])
            self.assertEqual(store.count, 1)
            self.assertEqual(len(path.read_text(encoding="utf-8").splitlines()), 1)


if __name__ == "__main__":
    unittest.main()
