"""The .wingbox/ folder: everything Wingbox writes lives there, and it marks the project root.

Every command runs as a process, as a user or an agent runs it.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

FILES = {
    "shop/__init__.py": "",
    "shop/pricing.py": "def order_total(items):\n    return sum(items)\n",
    "shop/cart.py": "from shop.pricing import order_total\n\n\ndef pay(items):\n    return order_total(items)\n",
}
GENERATED = ("index.sexp", "index.uses.sexp", "cache.json", "REPORT.md", "map.html", "graph-snapshot.json")
NO_PROJECT = "no .wingbox/ in this folder or its parents: run wingbox build"


def _environment(**extra: str) -> dict[str, str]:
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join(filter(None, (str(REPO / "src"), environment.get("PYTHONPATH", ""))))
    environment["WINGBOX_HOOK_WAIT"] = "120"
    environment.update(extra)
    return environment


def _tree(root: Path, skip: str = ".wingbox") -> dict[str, bytes]:
    """Every file under root, but .git and the given folder, with its bytes."""
    found: dict[str, bytes] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if relative.parts[0] in (".git", skip) or path.is_dir():
            continue
        found[relative.as_posix()] = path.read_bytes()
    return found


class ProjectFolderTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(os.path.realpath(tmp.name))
        self.root = self.base / "project"
        self.root.mkdir()
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        for name, text in FILES.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")

    def wingbox(self, *args: str, cwd: Path | None = None, stdin: str = "", **environment: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", "wingbox.cli", *args],
            input=stdin, capture_output=True, text=True, cwd=cwd or self.root, env=_environment(**environment), timeout=600,
        )

    def build(self) -> None:
        completed = self.wingbox("build", str(self.root))
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_build_writes_only_into_the_wingbox_folder(self) -> None:
        """/* REQ-FRESH-029: `wingbox build PATH` must create PATH/.wingbox/ and write the graph, its use file, the build cache, REPORT.md, map.html and its state there; every other file of the project (also files named like the outputs of earlier versions) must be left byte for byte as it was. */"""
        (self.root / ".code_graph.sexp").write_text("(old)\n", encoding="utf-8")
        (self.root / "wingbox-out").mkdir()
        (self.root / "wingbox-out" / "REPORT.md").write_text("old report\n", encoding="utf-8")
        before = _tree(self.root)
        completed = self.wingbox("build", str(self.root))
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(before, _tree(self.root))
        for name in GENERATED:
            self.assertTrue((self.root / ".wingbox" / name).is_file(), name)
        self.assertIn(".wingbox/map.html", completed.stdout)
        self.assertNotIn("code_graph", completed.stdout + completed.stderr)

    def test_the_generated_files_are_ignored_by_git_and_files_a_person_writes_are_not(self) -> None:
        """/* REQ-FRESH-030: the build must write .wingbox/.gitignore so that git ignores every generated file of the folder and keeps the files people and agents decide on (config.yaml, learned.sexp, baseline.sexp, and decisions.sexp, the advice log the build appends to). */"""
        self.build()
        kept = (".wingbox/config.yaml", ".wingbox/learned.sexp", ".wingbox/baseline.sexp", ".wingbox/decisions.sexp", ".wingbox/.gitignore")
        for name in ("config.yaml", "learned.sexp", "baseline.sexp"):
            (self.root / ".wingbox" / name).write_text("; written by hand\n", encoding="utf-8")
        listed = subprocess.run(
            ["git", "-C", str(self.root), "status", "--porcelain", "--untracked-files=all", ".wingbox"],
            capture_output=True, text=True, check=True,
        ).stdout.split()
        for name in kept:
            self.assertIn(name, listed)
        self.assertEqual([], [name for name in listed if name.startswith(".wingbox/") and name not in kept])

    def test_a_query_from_a_subfolder_finds_the_project(self) -> None:
        """/* REQ-FRESH-031: a query run in a subfolder of the project must find .wingbox/index.sexp in a parent folder and answer from it. */"""
        self.build()
        completed = self.wingbox("rdeps", "shop/pricing", cwd=self.root / "shop")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("shop/cart", completed.stdout)

    def test_without_a_wingbox_folder_a_query_says_so_and_fails(self) -> None:
        """/* REQ-FRESH-032: without .wingbox/index.sexp in the folder or a parent, a query must say "no .wingbox/ in this folder or its parents: run wingbox build", name the paths it looked at and the --map and --root options, and exit non-zero; the search hook must stay silent and exit 0. */"""
        completed = self.wingbox("impact", "shop/pricing.py")
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn(NO_PROJECT, completed.stderr)
        self.assertIn(str(self.root / ".wingbox" / "index.sexp"), completed.stderr)
        self.assertIn("--map", completed.stderr)
        self.assertIn("--root", completed.stderr)
        hook = self.wingbox("hook", "pre-search", stdin=json.dumps(
            {"tool_name": "Read", "cwd": str(self.root), "tool_input": {"file_path": str(self.root / "shop/cart.py")}}
        ))
        self.assertEqual((hook.returncode, hook.stdout), (0, ""))

    def test_a_moved_wingbox_folder_is_never_refreshed(self) -> None:
        """/* REQ-FRESH-033: a .wingbox/ folder copied to a folder it was not built from (no sources there) must be read as it is, with a note to rebuild; the graph must not be replaced (no refresh runs). */"""
        self.build()
        other = self.base / "elsewhere"
        other.mkdir()
        shutil.copytree(self.root / ".wingbox", other / ".wingbox")
        graph = (other / ".wingbox" / "index.sexp").read_bytes()
        completed = self.wingbox("rdeps", "shop/pricing", cwd=other)
        self.assertIn("shop/cart", completed.stdout)
        self.assertIn("rebuild", completed.stderr)
        hook = self.wingbox("hook", "pre-search", cwd=other, stdin=json.dumps(
            {"tool_name": "Grep", "cwd": str(other), "tool_input": {"pattern": "order_total"}}
        ))
        self.assertIn("rebuild", hook.stdout)
        self.assertEqual(graph, (other / ".wingbox" / "index.sexp").read_bytes())
        self.assertFalse((other / ".wingbox" / "refresh.log").exists())
        self.assertFalse((other / "shop").exists())

    def test_a_refresh_never_publishes_an_empty_graph_over_a_full_one(self) -> None:
        """/* REQ-FRESH-034: when every source is gone, the automatic refresh must keep the published graph and fail (the marks stay), never publish an empty graph. */"""
        self.build()
        graph = (self.root / ".wingbox" / "index.sexp").read_bytes()
        shutil.rmtree(self.root / "shop")
        hook = self.wingbox("hook", "pre-search", stdin=json.dumps(
            {"tool_name": "Grep", "cwd": str(self.root), "tool_input": {"pattern": "order_total"}}
        ))
        self.assertIn("refresh-failed", hook.stdout)
        self.assertEqual(graph, (self.root / ".wingbox" / "index.sexp").read_bytes())

    def test_build_help_describes_wingbox_build(self) -> None:
        """/* REQ-CLI-008: `wingbox build --help` must print the usage of `wingbox build [PATH]` with its options, not the builder script's; `--output` is refused with a message that the graph is always PATH/.wingbox/index.sexp. */"""
        completed = self.wingbox("build", "--help")
        self.assertEqual(completed.returncode, 0)
        self.assertIn("usage: wingbox build [PATH]", completed.stdout)
        for option in ("--full", "--exclude-dir", "--lsp", "--lsp-timeout"):
            self.assertIn(option, completed.stdout)
        self.assertNotIn("build_code_graph.py", completed.stdout)
        refused = self.wingbox("build", str(self.root), "--output", str(self.base / "graph.sexp"))
        self.assertEqual(refused.returncode, 2)
        self.assertIn(".wingbox/index.sexp", refused.stderr)
        self.assertFalse((self.base / "graph.sexp").exists())

    def test_a_search_without_a_match_says_so(self) -> None:
        """/* REQ-CS-074: `wingbox search` with no matching module or fact must print `(result-count 0)` instead of nothing. */"""
        self.build()
        completed = self.wingbox("search", "zzzq")
        self.assertEqual(completed.stdout.strip(), "(result-count 0)")

    def test_open_shows_the_map(self) -> None:
        """/* REQ-CLI-009: `wingbox open` must open .wingbox/map.html of the project in the browser and print its path; without a project it says to run wingbox build. */"""
        self.build()
        opened = self.base / "opened.txt"
        browser = self.base / "browser.sh"
        browser.write_text(f'#!/bin/sh\necho "$1" > "{opened}"\n', encoding="utf-8")
        browser.chmod(0o755)
        completed = self.wingbox("open", cwd=self.root / "shop", BROWSER=str(browser))
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn(str(self.root / ".wingbox" / "map.html"), completed.stdout)
        self.assertIn("map.html", opened.read_text(encoding="utf-8"))
        missing = self.wingbox("open", cwd=self.base)
        self.assertNotEqual(missing.returncode, 0)
        self.assertIn(NO_PROJECT, missing.stderr)


if __name__ == "__main__":
    unittest.main()
