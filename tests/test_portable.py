"""The platform helpers and the parallel build give the same result on every system."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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

    def test_a_text_file_another_process_is_replacing_is_read_after_a_moment(self) -> None:
        """/* REQ-PORT-013: portable.read_text returns the text of a file, None when it does not exist, and on Windows tries again while the system reports the file in use (another process is replacing it); elsewhere a PermissionError is raised at once. */"""
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "note.txt")
            self.assertIsNone(portable.read_text(path))
            with open(path, "w", encoding="utf-8", newline="\n") as handle:
                handle.write("kept\n")
            self.assertEqual("kept\n", portable.read_text(path))
            real_open, calls = open, []

            def busy_twice(file, *args, **kwargs):
                calls.append(file)
                if len(calls) <= 2:
                    raise PermissionError(13, "Permission denied", file)
                return real_open(file, *args, **kwargs)

            with mock.patch.object(portable, "WINDOWS", True), mock.patch.object(portable, "_REPLACE_PAUSE", 0), mock.patch("builtins.open", busy_twice):
                self.assertEqual("kept\n", portable.read_text(path))
            self.assertEqual(3, len(calls))
            with mock.patch.object(portable, "WINDOWS", False), mock.patch("builtins.open", side_effect=PermissionError(13, "Permission denied", path)):
                with self.assertRaises(PermissionError):
                    portable.read_text(path)

    def test_two_processes_preparing_the_folder_do_not_stop_on_its_gitignore(self) -> None:
        """/* REQ-PORT-014: prepare_wingbox_directory reads .wingbox/.gitignore through portable.read_text, so a second process that starts while the first is replacing the file (two MCP servers started together on Windows) waits a moment and goes on. */"""
        sys.path.insert(0, str(REPO))
        from scripts import build_code_graph as builder

        with tempfile.TemporaryDirectory() as directory:
            builder.prepare_wingbox_directory(directory)
            path = os.path.join(directory, ".wingbox", ".gitignore")
            real_open, failures = open, []

            def busy_once(file, *args, **kwargs):
                if str(file) == path and not failures:
                    failures.append(file)
                    raise PermissionError(13, "Permission denied", file)
                return real_open(file, *args, **kwargs)

            # The builder holds its own copy of the helpers module (imported as scripts.portable).
            with mock.patch.object(builder.portable, "WINDOWS", True), mock.patch.object(builder.portable, "_REPLACE_PAUSE", 0), mock.patch("builtins.open", busy_once):
                builder.prepare_wingbox_directory(directory)
            self.assertEqual(1, len(failures))
            with open(path, encoding="utf-8") as handle:
                self.assertEqual(builder.GITIGNORE_TEXT, handle.read())

    def test_the_worker_count_follows_the_jobs_setting(self) -> None:
        """/* REQ-PORT-015: portable.worker_count is WINGBOX_JOBS when that is a whole number of at least 1, every processor otherwise (unset, empty, zero, negative, not a number), and never more than the number of tasks */"""
        with mock.patch.object(portable.os, "cpu_count", return_value=8):
            for value, expected in (("1", 1), ("3", 3), ("12", 12), ("", 8), ("0", 8), ("-2", 8), ("many", 8), ("1.5", 8)):
                with mock.patch.dict(os.environ, {portable.JOBS_VARIABLE: value}):
                    self.assertEqual(expected, portable.worker_count(), value)
            with mock.patch.dict(os.environ):
                os.environ.pop(portable.JOBS_VARIABLE, None)
                self.assertEqual(8, portable.worker_count())
                self.assertEqual(5, portable.worker_count(5))
                self.assertEqual(1, portable.worker_count(0))
            with mock.patch.dict(os.environ, {portable.JOBS_VARIABLE: "2"}):
                self.assertEqual(2, portable.worker_count(40))
                self.assertEqual(1, portable.worker_count(1))
        with mock.patch.object(portable.os, "cpu_count", return_value=None), mock.patch.dict(os.environ):
            os.environ.pop(portable.JOBS_VARIABLE, None)
            self.assertEqual(1, portable.worker_count())

    def test_one_job_parses_in_this_process(self) -> None:
        """/* REQ-PORT-016: with WINGBOX_JOBS=1 the builder parses its files without starting a pool of worker processes */"""
        sys.path.insert(0, str(REPO))
        from scripts import build_code_graph as builder

        with tempfile.TemporaryDirectory() as directory:
            items = []
            for name in ("a.py", "b.py", "c.py"):
                path = os.path.join(directory, name)
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write("X = 1\n")
                items.append((path, "python", "repo"))
            with mock.patch.dict(os.environ, {builder.portable.JOBS_VARIABLE: "1"}), mock.patch.object(builder, "ProcessPoolExecutor") as pool:
                results = builder.parse_files(items)
            pool.assert_not_called()
            self.assertEqual(sorted(path for path, _, _ in items), sorted(result.path for result in results))

    def test_no_code_reads_the_processor_count_but_the_helper(self) -> None:
        """/* REQ-PORT-017: the processor count is read in one place, portable.worker_count, so WINGBOX_JOBS limits every pool: the build, the incremental build, the use positions and the background refresh */"""
        readers = []
        for folder in ("scripts", "src"):
            for path in sorted((REPO / folder).rglob("*.py")):
                if "cpu_count" in path.read_text(encoding="utf-8"):
                    readers.append(path.relative_to(REPO).as_posix())
        self.assertEqual(["scripts/portable.py"], readers)


if __name__ == "__main__":
    unittest.main()


class HookFormTest(unittest.TestCase):
    def test_every_hook_is_written_in_exec_form_and_never_blocks(self) -> None:
        """/* REQ-INSTALL-023: every hook entry Wingbox writes is in exec form on every system (`command` = the program, `args` = the rest), which Claude Code starts with no shell, so it runs on Windows without Git Bash and a path with spaces or backslashes needs no quoting; each entry carries `"onFailure": "continue"`, so a missing program or a timeout never blocks Grep, Glob, Read or an edit; the shell form of older versions is still recognised as Wingbox's own entry. */"""
        sys.path.insert(0, str(REPO / "src"))
        from wingbox import install

        program = (r"C:\Program Files\Python313\Scripts\wingbox.exe",)
        entry = install.hook_group(program)["hooks"][0]
        self.assertEqual(
            {"type": "command", "command": program[0], "args": ["hook", "pre-search"], "timeout": 10, "onFailure": "continue"},
            entry,
        )
        dirty = install.dirty_group(("python3", "-m", "wingbox.cli"))["hooks"][0]
        self.assertEqual(("python3", ["-m", "wingbox.cli", "hook", "dirty"]), (dirty["command"], dirty["args"]))
        self.assertTrue(install.is_wingbox_hook(entry) and install.is_wingbox_hook(dirty))
        self.assertTrue(install.is_wingbox_hook({"type": "command", "command": "/usr/local/bin/wingbox hook pre-search"}))
        self.assertFalse(install.is_wingbox_hook({"type": "command", "command": "fmt.sh"}))


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
