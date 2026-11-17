"""The platform helpers and the parallel build give the same result on every system."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import portable  # noqa: E402


def _build(root: Path, method: str) -> bytes:
    environment = dict(os.environ, WINGBOX_START_METHOD=method)
    done = subprocess.run([sys.executable, str(REPO / "scripts" / "build_code_graph.py"), "--root", str(root), "--full"],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", env=environment, timeout=900)
    assert done.returncode == 0, done.stderr
    return (root / ".wingbox" / "index.sexp").read_bytes()


class PortableTest(unittest.TestCase):
    def test_a_lock_is_exclusive_and_released(self) -> None:
        """/* REQ-PORT-001: portable.lock takes the exclusive lock of a file (a second handle cannot take it without waiting) and portable.unlock releases it, on every system. */"""
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "lock")
            with open(path, "a+", encoding="utf-8") as first, open(path, "a+", encoding="utf-8") as second:
                self.assertTrue(portable.lock(first, blocking=False))
                if os.name == "nt":
                    self.assertFalse(portable.lock(second, blocking=False))
                portable.unlock(first)
                self.assertTrue(portable.lock(second, blocking=False))
                portable.unlock(second)

    def test_replace_writes_the_new_file(self) -> None:
        """/* REQ-PORT-002: portable.replace moves a file over an existing one. */"""
        with tempfile.TemporaryDirectory() as directory:
            source, target = os.path.join(directory, "a"), os.path.join(directory, "b")
            Path(source).write_text("new", encoding="utf-8")
            Path(target).write_text("old", encoding="utf-8")
            portable.replace(source, target)
            self.assertEqual("new", Path(target).read_text(encoding="utf-8"))

    def test_the_graph_is_the_same_bytes_whatever_the_start_method(self) -> None:
        """/* REQ-PORT-003: a build whose worker processes are spawned (the only method on Windows) publishes the same graph, byte for byte, as a build with the default start method; enough modules to use the parallel resolution. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "project"
            for index in range(420):
                path = root / "pkg" / f"m{index}.py"
                path.parent.mkdir(parents=True, exist_ok=True)
                previous = f"from pkg.m{index - 1} import f{index - 1}\n" if index else ""
                path.write_text(f"{previous}\n\ndef f{index}():\n    return {index}\n", encoding="utf-8")
            (root / "pkg" / "__init__.py").write_text("", encoding="utf-8")
            spawned = _build(root, "spawn")
            default = _build(root, "")   # the methods a build uses when nothing overrides them
        self.assertEqual(default, spawned)
        self.assertIn(b"(imports ", spawned)


if __name__ == "__main__":
    unittest.main()
