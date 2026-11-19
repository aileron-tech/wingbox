import ast
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wingbox import freshness  # noqa: E402
from wingbox.cli import global_hook_program  # noqa: E402
from wingbox.launch import code_command, module_command, without_starting_folder  # noqa: E402

# The package, named for a Python that leaves the starting folder off its path (as every start of Wingbox's own
# does: freshness.start_refresh, update.refresh_installed).
ENVIRONMENT = {**os.environ, "WINGBOX_JOBS": "1",
               "PYTHONPATH": os.pathsep.join(filter(None, (str(ROOT / "src"), os.environ.get("PYTHONPATH", ""))))}
# Every module a Python started by Wingbox imports first, as a file a project could hold.
TRAPS = ("wingbox/__init__.py", "wingbox/__main__.py", "wingbox/cli.py", "wingbox/refresh.py", "pip/__init__.py",
         "pip/__main__.py", "yaml.py", "json.py", "tree_sitter.py")


def project_with_traps(folder: str) -> Path:
    """A project whose files are named like the modules Wingbox imports; each one, when run, leaves a mark."""
    project = Path(folder) / "project"
    for relative in TRAPS:
        path = project / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "import os\nopen(os.path.join(%r, 'RAN-' + %r), 'w').close()\n" % (folder, relative.replace("/", "-")),
            encoding="utf-8",
        )
    return project


def marks(folder: str) -> list[str]:
    return sorted(name for name in os.listdir(folder) if name.startswith("RAN-"))


class ProjectCodeNeverRunsTest(unittest.TestCase):
    def test_every_python_wingbox_starts_leaves_the_starting_folder_out(self) -> None:
        """/* REQ-SEC-006: no source file of Wingbox starts a Python with -m or -c without -P before it (or -I, which includes it): `python -m` and `python -c` put the starting folder first on the module search path, and Wingbox is started inside the project it reads. The commands are built by wingbox/launch.py alone. */"""
        offenders = []
        for folder in ("scripts", "src"):
            for path in sorted((ROOT / folder).rglob("*.py")):
                for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                    if not isinstance(node, (ast.List, ast.Tuple)) or not node.elts:
                        continue
                    words = [e.value if isinstance(e, ast.Constant) and isinstance(e.value, str) else None for e in node.elts]
                    if words[0] is not None:
                        continue          # a named program (git -c ...), not a Python of ours
                    for option in ("-m", "-c"):
                        if option in words and not {"-P", "-I"} & set(words[: words.index(option)]):
                            offenders.append(f"{path.relative_to(ROOT).as_posix()}:{node.lineno}")
        self.assertEqual([], offenders)
        self.assertEqual(("py", "-P", "-m", "wingbox.cli"), module_command("py", "wingbox.cli"))
        self.assertEqual(("py", "-P", "-c", "pass"), code_command("py", "pass"))
        with mock.patch("shutil.which", return_value=None):
            self.assertEqual(("py", "-P", "-m", "wingbox.cli"), global_hook_program("not-the-program", "py"))

    def test_a_refresh_started_in_a_project_runs_no_file_of_the_project(self) -> None:
        """/* REQ-SEC-007: the refresh the hooks start in the project folder (`python -P -m wingbox.refresh`) imports the installed Wingbox: a project that holds wingbox/, pip/, yaml.py, json.py or tree_sitter.py gets none of them run, and the refresh itself runs (its log holds a line of the real refresh). */"""
        with tempfile.TemporaryDirectory() as folder:
            project = project_with_traps(folder)
            process = freshness.start_refresh(str(project))
            process.wait(timeout=120)
            log = Path(freshness.refresh_log_path(str(project))).read_text(encoding="utf-8", errors="replace")
            self.assertEqual([], marks(folder), log)
            self.assertIn("[wingbox]", log)

    def test_the_command_started_as_a_module_runs_no_file_of_the_project(self) -> None:
        """/* REQ-SEC-008: `python -P -m wingbox` (the form of the hooks of an install without the wingbox program, and of the first build of the MCP server) started in a project that holds files named like the modules Wingbox imports builds the graph of that project and runs none of them; they are in the graph as source files. */"""
        with tempfile.TemporaryDirectory() as folder:
            project = project_with_traps(folder)
            done = subprocess.run([*module_command(sys.executable, "wingbox"), "build", "."], cwd=project, capture_output=True,
                                  text=True, encoding="utf-8", errors="replace", timeout=300,
                                  env=ENVIRONMENT)
            self.assertEqual(0, done.returncode, done.stderr)
            self.assertEqual([], marks(folder), done.stderr)
            index = (project / ".wingbox" / "index.sexp").read_text(encoding="utf-8")
            self.assertIn("yaml", index)

    def test_the_starting_folder_comes_off_the_path_of_a_command(self) -> None:
        """/* REQ-SEC-009: a command a person starts as `python -m wingbox` takes the starting folder off its module search path before it loads anything else: the entry of that folder and the empty entry go, every other entry stays in order; the folder stays when the Wingbox package itself lies in it. A project file named yaml.py is then not run by a build started without -P. */"""
        here = os.path.abspath(os.sep + "work")
        elsewhere = os.path.abspath(os.sep + "lib")
        self.assertEqual([elsewhere, "zip"], without_starting_folder(["", here, elsewhere, "zip"], here, elsewhere))
        self.assertEqual(["", here, elsewhere], without_starting_folder(["", here, elsewhere], here, here))
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder) / "project"
            project.mkdir()
            for name in ("yaml.py", "tree_sitter.py", "portable.py", "build_code_graph.py"):
                (project / name).write_text("import os\nopen(os.path.join(%r, 'RAN-' + %r), 'w').close()\n" % (folder, name),
                                            encoding="utf-8")
            done = subprocess.run([sys.executable, "-m", "wingbox", "build", "."], cwd=project, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", timeout=300, env=ENVIRONMENT)
            self.assertEqual(0, done.returncode, done.stderr)
            self.assertEqual([], marks(folder), done.stderr)


if __name__ == "__main__":
    unittest.main()
