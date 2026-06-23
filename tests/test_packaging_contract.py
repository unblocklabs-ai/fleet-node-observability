from __future__ import annotations

import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class PackagingContractTests(unittest.TestCase):
    def test_build_release_excludes_scripts_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            result = subprocess.run(
                [str(ROOT / "packaging" / "build-release.sh"), "--output", temp_dir],
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=30,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            tarball = Path(temp_dir) / "fleet-node-observability-0.1.0.tar.gz"
            self.assertTrue(tarball.exists())
            with tarfile.open(tarball) as archive:
                names = archive.getnames()

            self.assertFalse(
                any(name.startswith("fleet-node-observability-0.1.0/scripts") for name in names),
                "release artifact must not include the broad legacy scripts/ path",
            )
            self.assertFalse(any("__pycache__" in name or name.endswith(".pyc") for name in names))

    def test_install_from_release_rejects_multi_root_archive(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            tarball = temp_path / "bad.tar.gz"
            with tarfile.open(tarball, "w:gz") as archive:
                for dirname in ("fleet-node-observability-0.1.0", "unexpected-root"):
                    info = tarfile.TarInfo(dirname)
                    info.type = tarfile.DIRTYPE
                    info.mode = 0o755
                    archive.addfile(info)

            result = subprocess.run(
                [
                    str(ROOT / "packaging" / "install-from-release.sh"),
                    "--tarball",
                    str(tarball),
                    "--install-dir",
                    str(temp_path / "install"),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=30,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("expected exactly one top-level directory", result.stderr)


if __name__ == "__main__":
    unittest.main()
