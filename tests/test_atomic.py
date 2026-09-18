import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fleet_node_observability.atomic import write_new_private_file, write_private_atomic


class PrivateWriteTests(unittest.TestCase):
    def test_atomic_temporary_collision_preserves_both_files(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "secret"
            temporary = path.with_name(f".secret.tmp-{os.getpid()}-123")
            path.write_text("current")
            temporary.write_text("another writer")
            with patch("fleet_node_observability.atomic.time.time_ns", return_value=123):
                with self.assertRaises(FileExistsError):
                    write_private_atomic(path, "replacement")
            self.assertEqual(path.read_text(), "current")
            self.assertEqual(temporary.read_text(), "another writer")

    def test_failed_write_cleans_up_only_its_new_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "backup"
            with patch("fleet_node_observability.atomic.os.fsync", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    write_new_private_file(path, "private")
            self.assertFalse(path.exists())
