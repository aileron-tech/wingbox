"""The architecture commands read a fresh graph, and the files a plugin loader reads keep it fresh.

Every command runs as a process, as a user or an agent runs it.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from wingbox import freshness  # noqa: E402

LAYERED = {
    "package.json": '{"name": "shop"}\n',
    **{f"src/{folder}/{folder}{i}.ts": f"export const {folder}{i} = {i};\n"
       for folder, count in (("routes", 4), ("services", 4), ("models", 3), ("repositories", 3)) for i in range(count)},
}

PLUGINS = {
    "shop/__init__.py": "",
    "shop/registry.py": (
        "import importlib\nimport json\n\n\ndef load(name):\n"
        "    return importlib.import_module(f\"shop.plugins.{name}\")\n"
    ),
    "shop/plugins/__init__.py": "",
    "shop/plugins/discount.py": "def run(order):\n    return order\n",
    "shop/plugins/tax.py": "def run(order):\n    return order\n",
    "shop/plugins.json": '{"enabled": ["discount"]}\n',
}


def _environment(**extra: str) -> dict[str, str]:
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join(filter(None, (str(REPO / "src"), environment.get("PYTHONPATH", ""))))
    environment["WINGBOX_HOOK_WAIT"] = "120"
    environment.pop("CLAUDECODE", None)
    environment.update(extra)
    return environment


class _Project(unittest.TestCase):
    files: dict[str, str] = {}

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(os.path.realpath(tmp.name)) / "project"
        self.root.mkdir()
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        for name, text in self.files.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        built = self.wingbox("build", str(self.root))
        self.assertEqual(0, built.returncode, built.stderr)

    def wingbox(self, *args: str, stdin: str = "", **environment: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", "wingbox.cli", *args], input=stdin, capture_output=True, text=True,
            cwd=self.root, env=_environment(**environment), timeout=600,
        )

    def graph(self) -> str:
        return (self.root / ".wingbox" / "index.sexp").read_text(encoding="utf-8")


class ArchitectureCommandsRefreshTest(_Project):
    files = LAYERED

    def test_each_architecture_command_refreshes_the_graph_before_reading_it(self) -> None:
        """/* REQ-FRESH-035: advice, align, place, learn and compare bring the graph up to date before they read it, as the query commands do: a file added outside Wingbox is in the graph after the command. */"""
        commands = (("advice", "--sexp"), ("align", "--sexp"), ("place", "a repository", "--sexp"),
                    ("learn", "--agent", "--sexp"), ("compare", "--sexp"))
        for number, command in enumerate(commands):
            name = f"added{number}"
            (self.root / "src" / "services" / f"{name}.ts").write_text(f"export const {name} = 1;\n", encoding="utf-8")
            done = self.wingbox(*command)
            self.assertEqual(0, done.returncode, f"{command}: {done.stderr}")
            self.assertIn(name, self.graph(), command[0])

    def test_a_command_that_cannot_refresh_says_the_graph_is_stale(self) -> None:
        """/* REQ-FRESH-036: with WINGBOX_AUTO_REFRESH=0 an architecture command reads the graph as it is and prints the same stale fact as the query commands. */"""
        (self.root / "src" / "services" / "late.ts").write_text("export const late = 1;\n", encoding="utf-8")
        done = self.wingbox("align", "--sexp", WINGBOX_AUTO_REFRESH="0")
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertIn("(wingbox-graph-stale :reason auto-refresh-off", done.stderr)
        self.assertNotIn("late", self.graph())


class PluginConfigFreshnessTest(_Project):
    files = PLUGINS

    def test_the_build_records_the_files_a_plugin_loader_reads(self) -> None:
        """/* REQ-FRESH-037: the snapshot of a build lists the JSON, YAML and TOML files beside a Python module loader (the names the registry-wires edges come from) and the loader's folder. */"""
        snapshot = freshness.read_snapshot(str(self.root))
        self.assertIsNotNone(snapshot)
        assert snapshot is not None
        self.assertIn("shop/plugins.json", snapshot.files)
        self.assertIn("shop", snapshot.data_folders)

    def test_an_edit_of_a_loader_config_marks_the_graph_and_the_next_query_follows_it(self) -> None:
        """/* REQ-FRESH-038: an Edit of a configuration file a plugin loader reads marks the graph out of date (the PostToolUse hook), and the next query rebuilds it: the registry-wires edges follow the new names. */"""
        self.assertNotIn("shop/plugins/tax", self._wires())
        config = self.root / "shop" / "plugins.json"
        config.write_text('{"enabled": ["discount", "tax"]}\n', encoding="utf-8")
        payload = {"tool_name": "Edit", "tool_input": {"file_path": str(config)}, "cwd": str(self.root)}
        freshness.run_post_edit(json.dumps(payload))
        self.assertIn("shop/plugins.json", freshness.read_state(str(self.root)).changed_files)
        done = self.wingbox("deps", "shop/registry")
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertIn("shop/plugins/tax", self._wires())

    def test_a_config_changed_outside_wingbox_or_added_beside_the_loader_is_seen(self) -> None:
        """/* REQ-FRESH-039: a loader's configuration file changed outside Wingbox (no hook), or a new one added beside the loader, is found by the comparison with the snapshot and the next query rebuilds the graph. */"""
        (self.root / "shop" / "plugins.json").write_text('{"enabled": ["tax"]}\n', encoding="utf-8")
        self.assertEqual(0, self.wingbox("deps", "shop/registry").returncode)
        self.assertIn("shop/plugins/tax", self._wires())
        (self.root / "shop" / "plugins.json").write_text('{"enabled": []}\n', encoding="utf-8")
        (self.root / "shop" / "more.yaml").write_text("- discount\n", encoding="utf-8")
        self.assertEqual(0, self.wingbox("deps", "shop/registry").returncode)
        wires = self._wires()
        self.assertIn("shop/plugins/discount", wires)
        self.assertNotIn("shop/plugins/tax", wires)

    def _wires(self) -> str:
        """The registry-wires edges of the graph, with their module ids resolved by the search script."""
        sys.path.insert(0, str(REPO / "scripts"))
        import code_graph_search as search

        graph = search.CodeGraph()
        graph.load(str(self.root / ".wingbox" / "index.sexp"), str(self.root))
        return "\n".join(f"{edge.source} -> {edge.target}" for edge in graph.all_edges if edge.etype == "registry-wires")


if __name__ == "__main__":
    unittest.main()
