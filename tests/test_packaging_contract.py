from __future__ import annotations

import io
import stat
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parents[1]
VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip()


class PackagingContractTests(unittest.TestCase):
    def build_release(self, output_dir: Path) -> Path:
        result = subprocess.run(
            [str(ROOT / "packaging" / "build-release.sh"), "--output", str(output_dir)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return output_dir / f"fleet-node-observability-{VERSION}.tar.gz"

    def test_build_release_contains_final_runtime_and_excludes_tests(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            tarball = self.build_release(Path(temp_dir))

            self.assertTrue(tarball.exists())
            with tarfile.open(tarball) as archive:
                names = archive.getnames()

            self.assertEqual(
                {PurePosixPath(name).parts[0] for name in names},
                {f"fleet-node-observability-{VERSION}"},
            )
            for required in [
                "bin/install-fleet-node-agent",
                "examples/node-agent.example.json",
                "src/fleet_node_observability/agent.py",
                "src/fleet_node_observability/atomic.py",
                "src/fleet_node_observability/openclaw.py",
            ]:
                self.assertIn(f"fleet-node-observability-{VERSION}/{required}", names)
            self.assertFalse(
                any(name.startswith(f"fleet-node-observability-{VERSION}/tests") for name in names),
                "release artifact must not include development-only tests/",
            )
            self.assertFalse(any("__pycache__" in name or name.endswith(".pyc") for name in names))

            checksum = tarball.with_name(f"fleet-node-observability-{VERSION}.sha256")
            checksum_parts = checksum.read_text(encoding="utf-8").split()
            self.assertEqual(checksum_parts[1], tarball.name)
            self.assertFalse(Path(checksum_parts[1]).is_absolute())

    def test_install_from_release_rejects_multi_root_archive(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            tarball = temp_path / "bad.tar.gz"
            with tarfile.open(tarball, "w:gz") as archive:
                for dirname in (f"fleet-node-observability-{VERSION}", "unexpected-root"):
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
                    str(temp_path / "fleet-node-observability"),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=30,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("expected exactly one top-level directory", result.stderr)

    def test_install_from_release_rejects_path_traversal_archive(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            tarball = temp_path / "bad-traversal.tar.gz"
            with tarfile.open(tarball, "w:gz") as archive:
                payload = b"bad"
                info = tarfile.TarInfo("../outside")
                info.size = len(payload)
                archive.addfile(info, io.BytesIO(payload))

            result = subprocess.run(
                [
                    str(ROOT / "packaging" / "install-from-release.sh"),
                    "--tarball",
                    str(tarball),
                    "--install-dir",
                    str(temp_path / "fleet-node-observability"),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=30,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Invalid tarball member path", result.stderr)
            self.assertFalse((temp_path.parent / "outside").exists())

    def test_install_from_release_rejects_dangerous_overwrite_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            tarball = Path(temp_dir) / "placeholder.tar.gz"
            tarball.write_bytes(b"not reached")

            result = subprocess.run(
                [
                    str(ROOT / "packaging" / "install-from-release.sh"),
                    "--tarball",
                    str(tarball),
                    "--install-dir",
                    "/",
                    "--overwrite",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=30,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Refusing dangerous install directory", result.stderr)

    def test_install_from_release_overwrite_removes_hidden_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            tarball = self.build_release(temp_path / "dist")
            install_dir = temp_path / "fleet-node-observability"

            first_result = subprocess.run(
                [
                    str(ROOT / "packaging" / "install-from-release.sh"),
                    "--tarball",
                    str(tarball),
                    "--install-dir",
                    str(install_dir),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(first_result.returncode, 0, first_result.stderr)
            stale_file = install_dir / ".stale-hidden"
            stale_file.write_text("stale\n", encoding="utf-8")

            overwrite_result = subprocess.run(
                [
                    str(ROOT / "packaging" / "install-from-release.sh"),
                    "--tarball",
                    str(tarball),
                    "--install-dir",
                    str(install_dir),
                    "--overwrite",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=30,
            )

            self.assertEqual(overwrite_result.returncode, 0, overwrite_result.stderr)
            self.assertFalse(stale_file.exists())

    def test_install_from_release_root_is_traversable_by_runtime_user(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            tarball = self.build_release(temp_path / "dist")
            install_dir = temp_path / "fleet-node-observability"

            result = subprocess.run(
                [
                    str(ROOT / "packaging" / "install-from-release.sh"),
                    "--tarball",
                    str(tarball),
                    "--install-dir",
                    str(install_dir),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=30,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(stat.S_IMODE(install_dir.stat().st_mode), 0o755)

    @unittest.skipUnless(hasattr(Path, "symlink_to"), "symlinks are not supported")
    def test_install_from_release_rejects_symlinked_install_dir(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            tarball = self.build_release(temp_path / "dist")
            real_target = temp_path / "real-target"
            real_target.mkdir()
            install_dir = temp_path / "fleet-node-observability"
            install_dir.symlink_to(real_target, target_is_directory=True)

            result = subprocess.run(
                [
                    str(ROOT / "packaging" / "install-from-release.sh"),
                    "--tarball",
                    str(tarball),
                    "--install-dir",
                    str(install_dir),
                    "--overwrite",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=30,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Install directory must not be a symlink", result.stderr)
            self.assertFalse((real_target / "VERSION").exists())

    @unittest.skipUnless(hasattr(Path, "symlink_to"), "symlinks are not supported")
    def test_install_from_release_rejects_symlinked_install_parent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            tarball = self.build_release(temp_path / "dist")
            real_parent = temp_path / "real-parent"
            real_parent.mkdir()
            symlink_parent = temp_path / "link-parent"
            symlink_parent.symlink_to(real_parent, target_is_directory=True)

            result = subprocess.run(
                [
                    str(ROOT / "packaging" / "install-from-release.sh"),
                    "--tarball",
                    str(tarball),
                    "--install-dir",
                    str(symlink_parent / "fleet-node-observability"),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=30,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Install path must not include symlinked parent directories", result.stderr)
            self.assertFalse((real_parent / "fleet-node-observability").exists())

    def test_install_from_release_rejects_checksum_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            tarball = self.build_release(temp_path / "dist")

            result = subprocess.run(
                [
                    str(ROOT / "packaging" / "install-from-release.sh"),
                    "--tarball",
                    str(tarball),
                    "--sha256",
                    "0" * 64,
                    "--install-dir",
                    str(temp_path / "fleet-node-observability"),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=30,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("SHA256 mismatch", result.stderr)


if __name__ == "__main__":
    unittest.main()
