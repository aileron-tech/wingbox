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


class WindowsHookTest(unittest.TestCase):
    def test_a_windows_hook_runs_the_program_without_a_shell(self) -> None:
        """/* REQ-INSTALL-023: on Windows a hook entry is written in exec form (`command` = the program, `args` = the rest), which Claude Code starts without a shell, so a path with spaces or backslashes needs no quoting and PowerShell syntax never matters; elsewhere the entry stays a shell command; both forms are recognised as Wingbox's own entries. */"""
        sys.path.insert(0, str(REPO / "src"))
        from wingbox import cli, install

        program = r"C:\Program Files\Python313\Scripts\wingbox.exe"
        command = cli.global_hook_command_for(program, sys.executable, windows=True)
        entry = install.hook_group(command, windows=True)["hooks"][0]
        self.assertEqual(program, entry["command"])
        self.assertEqual(["hook", "pre-search"], entry["args"])
        dirty = install.dirty_group(command, windows=True)["hooks"][0]
        self.assertEqual(["hook", "dirty"], dirty["args"])
        self.assertTrue(install.is_wingbox_hook(entry) and install.is_wingbox_hook(dirty))
        posix = install.hook_group("/usr/local/bin/wingbox hook pre-search", windows=False)["hooks"][0]
        self.assertEqual("/usr/local/bin/wingbox hook pre-search", posix["command"])
        self.assertNotIn("args", posix)
        self.assertTrue(install.is_wingbox_hook(posix))


class LineEndingTest(unittest.TestCase):
    def test_crlf_sources_give_the_same_graph_as_lf_sources(self) -> None:
        """/* REQ-PORT-004: source files with CRLF line endings (as Windows editors and Git checkouts write them) give the same graph, line numbers included, as the same files with LF; the graph file itself is written with LF on every system. */"""
        files = {
            "pkg/__init__.py": "",
            "pkg/models.py": "class Order:\n    pass\n\n\ndef total(order):\n    return order\n",
            "pkg/service.py": "import os\nfrom pkg.models import Order, total\n\n\ndef run():\n    os.environ.get('X')\n    return total(Order())\n",
            "web/src/view.ts": "import { fmt } from './util';\nexport function show(n: number) {\n  return fmt(n);\n}\n",
            "web/src/util.ts": "export function fmt(n: number): string {\n  return String(n);\n}\n",
        }
        graphs = []
        for ending in ("\n", "\r\n"):
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory) / "project"
                for name, text in files.items():
                    path = root / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(text.replace("\n", ending).encode("utf-8"))
                graph = _build(root, "")
                self.assertNotIn(b"\r\n", graph)
                graphs.append(graph.replace(root.name.encode(), b"project"))
        self.assertEqual(graphs[0], graphs[1])
        self.assertIn(b"total 5 ", graphs[1])


class EdgePathTest(unittest.TestCase):
    def test_paths_from_users_and_hooks_take_the_systems_form(self) -> None:
        """/* REQ-PORT-005: a path given by a person or a hook is normalised at the edge by one function: on Windows backslashes, forward slashes, a Git Bash drive (/c/Users/me), a lower-case drive letter and the \\\\?\\ long-path prefix all become the same C:\\Users\\me form; on macOS and Linux only the redundant parts (a//b, ./a) go. */"""
        expected = r"C:\Users\me\shop\src\app.py"
        for given in (
            r"C:\Users\me\shop\src\app.py",
            "C:/Users/me/shop/src/app.py",
            r"c:\Users\me\shop\src\app.py",
            "/c/Users/me/shop/src/app.py",
            "\\\\?\\C:\\Users\\me\\shop\\src\\app.py",
            r"C:\Users\me\shop\src\.\app.py",
        ):
            self.assertEqual(expected, portable.local_path(given, windows=True), given)
        self.assertEqual("/home/me/shop/src/app.py", portable.local_path("/home/me//shop/./src/app.py", windows=False))
        self.assertEqual("src/app.py", portable.local_path("./src/app.py", windows=False))

    def test_a_path_is_relative_to_the_root_or_outside_it(self) -> None:
        """/* REQ-PORT-006: relative_posix gives a path under the root with / on every system, "" for the root itself, and None for a path outside it (a parent, a sibling whose name starts like the root, another drive); no string prefix test decides it. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "shop"
            (root / "src").mkdir(parents=True)
            (root / "src" / "app.py").write_text("", encoding="utf-8")
            (Path(directory) / "shop-old").mkdir()
            self.assertEqual("src/app.py", portable.relative_posix(str(root / "src" / "app.py"), str(root)))
            self.assertEqual("", portable.relative_posix(str(root), str(root)))
            self.assertIsNone(portable.relative_posix(directory, str(root)))
            self.assertIsNone(portable.relative_posix(str(Path(directory) / "shop-old"), str(root)))
            if os.name == "nt":
                other = "D:\\elsewhere" if str(root).upper().startswith("C") else "C:\\elsewhere"
                self.assertIsNone(portable.relative_posix(other, str(root)))
