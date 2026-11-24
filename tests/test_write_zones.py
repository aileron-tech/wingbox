"""Check each command's write scope, including the project .gitignore exception for init.

Each user-facing command runs in a temporary git repository with a temporary home folder and a temporary
folder for temporary files; afterwards every path that changed (created, modified or deleted) must lie under
<project>/.wingbox/ or under the home folder, except for init's .gitignore update.
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from wingbox import freshness  # noqa: E402
FILES = {
    "shop/__init__.py": "",
    "shop/pricing.py": "class Pricer:\n    def total(self, items):\n        return sum(items)\n",
    "shop/cart.py": "from shop.pricing import Pricer\n\n\ndef pay(items):\n    return Pricer().total(items)\n",
}
def _state(folder: Path) -> dict[str, tuple[int, int]]:
    found = {}
    for path in folder.rglob("*"):
        if ".git" in path.relative_to(folder).parts[:1]:
            continue
        try:
            status = path.lstat()
        except FileNotFoundError:   # a temporary file renamed away while the folder was listed
            continue
        found[str(path)] = (status.st_size, status.st_mtime_ns) if path.is_file() else (-1, 0)
    return found


class WriteZonesTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(os.path.realpath(tmp.name))
        self.project, self.home, self.temp = self.base / "project", self.base / "home", self.base / "tmp"
        for folder in (self.project, self.home, self.temp, self.home / ".claude", self.home / ".codex"):
            folder.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(self.project)], check=True)
        for name, text in FILES.items():
            (self.project / name).parent.mkdir(parents=True, exist_ok=True)
            (self.project / name).write_text(text, encoding="utf-8")
        self.environment = dict(
            os.environ, HOME=str(self.home), USERPROFILE=str(self.home), TMPDIR=str(self.temp), TEMP=str(self.temp),
            TMP=str(self.temp), PYTHONDONTWRITEBYTECODE="1", NO_COLOR="1", WINGBOX_HOOK_WAIT="30",
            PYTHONPATH=os.pathsep.join(filter(None, (str(REPO / "src"), os.environ.get("PYTHONPATH", "")))),
        )

    def run_command(self, *args: str, stdin: str = "") -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, "-m", "wingbox.cli", *args], cwd=self.project, env=self.environment,
                              input=stdin, capture_output=True, text=True, timeout=600)

    def wait_for_background_refresh(self) -> None:
        """A hook may start a refresh that goes on after it returns: its writes count for that step."""
        deadline = time.monotonic() + 300
        while freshness.refresh_running(str(self.project)) and time.monotonic() < deadline:
            time.sleep(0.1)

    def outside(self, before: dict, after: dict, command: str) -> list[str]:
        changed = {path for path in before.keys() | after.keys() if before.get(path) != after.get(path)}
        if command == "init":
            changed.discard(str(self.project / ".gitignore"))
        allowed = (str(self.project / ".wingbox"), str(self.home))
        return sorted(
            path for path in changed
            if not any(path == root or path.startswith(root + os.sep) for root in allowed)
        )

    def test_every_command_writes_only_into_wingbox_and_the_home_folder(self) -> None:
        """/* REQ-ZONE-001: build, init, learn, align (and --learn), place, advice, compare, boundaries, impact, usages, plain install, update --check, uninstall, the MCP server with --build-missing and every hook (with its background refresh) write only below <project>/.wingbox/ and the user's home folder. Only init also updates the project's .gitignore. */"""
        def read(path: str) -> dict:
            return {"session_id": "s", "cwd": str(self.project), "tool_name": "Read", "tool_input": {"file_path": path}}

        mcp_lines = "".join(json.dumps(line) + "\n" for line in (
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "analyze_impact", "arguments": {"target": "shop/pricing.py"}}},
        ))
        steps = [
            ("mcp --build-missing", ["mcp", "--root", str(self.project), "--build-missing"], mcp_lines),
            ("hook pre-search without a graph", ["hook", "pre-search"], json.dumps(read(str(self.project / "shop/cart.py")))),
            ("build", ["build", str(self.project)], ""),
            ("init", ["init", str(self.project), "--yes"], ""),
            ("learn", ["learn", "--agent", "--sexp"], ""),
            ("align", ["align", "--sexp"], ""),
            ("align --learn", ["align", "--learn", "--sexp"], ""),
            ("place", ["place", "a pricing rule", "--sexp"], ""),
            ("advice", ["advice", "--sexp"], ""),
            ("compare", ["compare", "--sexp"], ""),
            ("boundaries", ["boundaries"], ""),
            ("impact", ["impact", "shop/pricing.py"], ""),
            ("usages", ["usages", "Pricer"], ""),
            ("hook pre-search", ["hook", "pre-search"], json.dumps(read(str(self.project / "shop/cart.py")))),
            ("hook dirty", ["hook", "dirty"], json.dumps({
                "cwd": str(self.project), "tool_name": "Edit", "tool_input": {"file_path": str(self.project / "shop/cart.py")},
                "tool_response": {"success": True}})),
            ("hook session-start", ["hook", "session-start"], json.dumps({"cwd": str(self.project), "hook_event_name": "SessionStart"})),
            ("hook git", ["hook", "git"], json.dumps({
                "cwd": str(self.project), "tool_name": "Bash", "tool_input": {"command": "git switch main"}})),
            ("install", ["install", "--yes"], ""),
            ("update --check", ["update", "--check"], ""),
            ("uninstall", ["uninstall"], ""),
        ]
        problems = []
        for name, args, stdin in steps:
            before = {**_state(self.project), **_state(self.home), **_state(self.temp)}
            done = self.run_command(*args, stdin=stdin)
            self.wait_for_background_refresh()
            after = {**_state(self.project), **_state(self.home), **_state(self.temp)}
            self.assertNotIn("Traceback", done.stderr, f"{name}: {done.stderr[-2000:]}")
            problems += [f"{name}: {path}" for path in self.outside(before, after, name)]
        self.assertEqual([], problems)


if __name__ == "__main__":
    unittest.main()
