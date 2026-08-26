from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from copy_trader.line_db.discovery import (
    DatabaseCandidate,
    choose_database_candidate,
    discover_windows_databases,
)
from copy_trader.line_db.keys import (
    WindowsCredentialManagerKeyProvider,
    default_key_provider,
)


class WindowsDiscoveryTests(unittest.TestCase):
    def test_finds_desktop_and_store_layouts_without_recursive_disk_scan(self):
        with tempfile.TemporaryDirectory() as directory:
            local = Path(directory)
            desktop = local / "LINE/Data/db/qwdesktop.edb"
            store = (
                local / "Packages/NAVER.LINEwin8_8ptj331gd3tyt"
                / "AppData/LINE/Data/db/qwstore.edb"
            )
            desktop.parent.mkdir(parents=True)
            store.parent.mkdir(parents=True)
            desktop.write_bytes(b"desktop")
            store.write_bytes(b"store")

            candidates = discover_windows_databases(local)
            self.assertEqual(
                {item.path for item in candidates},
                {desktop.resolve(), store.resolve()},
            )
            self.assertEqual(
                {item.source.split(":", 1)[0] for item in candidates},
                {"windows_desktop", "windows_package"},
            )

    def test_one_qw_candidate_is_recommended_over_other_edb_files(self):
        values = [
            DatabaseCandidate(Path("C:/LINE/Data/db/settings.edb"), "test"),
            DatabaseCandidate(Path("C:/LINE/Data/db/qwmessage.edb"), "test"),
        ]
        self.assertEqual(choose_database_candidate(values).name, "qwmessage.edb")

    def test_ambiguous_candidates_require_explicit_selection(self):
        values = [
            DatabaseCandidate(Path("C:/one.edb"), "test"),
            DatabaseCandidate(Path("C:/two.edb"), "test"),
        ]
        with self.assertRaisesRegex(RuntimeError, "multiple LINE database"):
            choose_database_candidate(values)


class WindowsKeyProviderSelectionTests(unittest.TestCase):
    def test_windows_defaults_to_credential_manager(self):
        with patch("copy_trader.line_db.keys.sys.platform", "win32"), patch.dict(
            "os.environ", {"LINE_DB_KEY": ""}, clear=False
        ):
            provider = default_key_provider("trader-test")
        self.assertIsInstance(provider, WindowsCredentialManagerKeyProvider)
        self.assertEqual(provider.target, "trader-test")


if __name__ == "__main__":
    unittest.main()
