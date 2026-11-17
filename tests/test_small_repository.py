"""A repository too small to judge: `wingbox init` writes a config that every command accepts."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
# What 0.6.0 to 0.8.0 wrote for a repository with fewer than 12 source files: `parts:` with no entry.
LEGACY_EMPTY_PARTS = (
    "# Wingbox architecture config. Read this before you change the file (written 2026-11-17).\n"
    "parts:\n"
    "# not judged (fewer than 12 source files): .\n"
)


def _environment(home: str) -> dict[str, str]:
    environment = dict(os.environ, HOME=home, USERPROFILE=home)
    environment["PYTHONPATH"] = os.pathsep.join(filter(None, (str(REPO / "src"), environment.get("PYTHONPATH", ""))))
    return environment


class SmallRepositoryTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name).resolve()
        (self.base / "home").mkdir()

    def repository(self, sources: int) -> Path:
        root = self.base / f"repo{len(list(self.base.glob('repo*')))}-{sources}"
        root.mkdir()
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        (root / "README.md").write_text("A small project.\n", encoding="utf-8")
        for index in range(sources):
            body = "VALUE = 1\n" if index == 0 else f"from app.m0 import VALUE\n\n\ndef f{index}():\n    return VALUE\n"
            (root / "app").mkdir(exist_ok=True)
            (root / "app" / f"m{index}.py").write_text(body, encoding="utf-8")
        return root

    def wingbox(self, root: Path, *args: str, stdin: str = "") -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, "-m", "wingbox", *args], cwd=root, input=stdin, capture_output=True, text=True,
                              env=_environment(str(self.base / "home")), timeout=300)

    def test_init_on_a_small_repository_writes_a_config_that_build_accepts(self) -> None:
        """/* REQ-ARCH-042: `wingbox init --yes` on a repository with 1, 3 or 11 source files (too few to judge) writes a config.yaml that `wingbox build` accepts: the build exits 0 and writes the graph; a repository with no source file is a new project, which init sets up with --language and --style, and that config is accepted too. */"""
        for sources in (0, 1, 3, 11):
            root = self.repository(sources)
            new_project = ("--language", "python", "--style", "layered") if sources == 0 else ()
            init = self.wingbox(root, "init", *new_project, "--yes")
            self.assertEqual(0, init.returncode, init.stderr)
            self.assertTrue((root / ".wingbox" / "config.yaml").is_file())
            build = self.wingbox(root, "build", ".")
            self.assertEqual(0, build.returncode, f"{sources} files: {build.stdout}{build.stderr}")
            self.assertNotIn("ERROR", build.stdout + build.stderr, sources)
            self.assertTrue((root / ".wingbox" / "index.sexp").is_file(), sources)

    def test_a_config_without_parts_from_an_earlier_version_is_read_as_no_parts_declared(self) -> None:
        """/* REQ-ARCH-043: a config.yaml whose `parts` is empty or missing, as 0.6.0 to 0.8.0 wrote for a small repository, is read as no parts declared: the whole repository is one undetermined part, the build writes the graph and says so in one line. */"""
        for text in (LEGACY_EMPTY_PARTS, "# only a comment\n", "parts: {}\n", "{}\n"):
            root = self.repository(3)
            (root / ".wingbox").mkdir()
            (root / ".wingbox" / "config.yaml").write_text(text, encoding="utf-8")
            build = self.wingbox(root, "build", ".")
            self.assertEqual(0, build.returncode, f"{text!r}: {build.stdout}{build.stderr}")
            self.assertTrue((root / ".wingbox" / "index.sexp").is_file(), text)
            output = build.stdout + build.stderr
            self.assertEqual(1, output.count(".wingbox/config.yaml declares no parts: the whole repository is one undetermined part"), output)

    def test_a_config_broken_by_hand_names_the_line(self) -> None:
        """/* REQ-ARCH-044: a config.yaml broken in another way (`parts` that is not a mapping, a part with an unknown style) is still an error, and the message names the line of the file. */"""
        cases = {
            "# header\nparts:\n  - app\n": "line 2",
            "parts:\n  .:\n    style: layered\n  app:\n    style: spaghetti\n": "line 4",
        }
        for text, line in cases.items():
            root = self.repository(3)
            (root / ".wingbox").mkdir()
            (root / ".wingbox" / "config.yaml").write_text(text, encoding="utf-8")
            build = self.wingbox(root, "build", ".")
            output = build.stdout + build.stderr
            self.assertIn(f".wingbox/config.yaml {line}:", output)

    def test_every_command_works_on_a_small_repository(self) -> None:
        """/* REQ-ARCH-045: after `init --yes` and `build` on a repository too small to judge, learn, align, advice, place, compare, boundaries and the search hook answer without a traceback; boundaries says the part was not checked. */"""
        root = self.repository(3)
        self.assertEqual(0, self.wingbox(root, "init", "--yes").returncode)
        self.assertEqual(0, self.wingbox(root, "build", ".").returncode)
        runs = {
            "learn": self.wingbox(root, "learn", "--agent", "--sexp"),
            "align": self.wingbox(root, "align", "--sexp"),
            "advice": self.wingbox(root, "advice", "--sexp"),
            "place": self.wingbox(root, "place", "a payment client", "--sexp"),
            "compare": self.wingbox(root, "compare", "--sexp"),
            "boundaries": self.wingbox(root, "boundaries"),
            "hook": self.wingbox(root, "hook", "pre-search", stdin=json.dumps(
                {"session_id": "s", "cwd": str(root), "tool_name": "Read", "tool_input": {"file_path": str(root / "app" / "m0.py")}})),
        }
        for name, run in runs.items():
            self.assertNotIn("Traceback", run.stderr, name)
            self.assertIn(run.returncode, (0, 1, 2), f"{name}: {run.stdout}{run.stderr}")
        self.assertIn("boundaries-not-checked", runs["boundaries"].stdout)
        self.assertIn("(wingbox-graph", runs["hook"].stdout)


if __name__ == "__main__":
    unittest.main()
