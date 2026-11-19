"""`wingbox boundaries` says which parts it checked: `ok` only when every part with files was checked.

A part whose declared rules were not read, whose style is undetermined, or that
has no layer style is not checked; saying `ok` for it would be a false pass.
Every command runs as a process, as a user or an agent runs it.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

LAYERED = {
    "package.json": '{"name": "shop"}\n',
    **{f"src/{folder}/{folder}{i}.ts": f"export const {folder}{i} = {i};\n"
       for folder, count in (("routes", 4), ("services", 4), ("models", 3), ("repositories", 3)) for i in range(count)},
}
# A dependency-cruiser configuration whose rules are computed: Wingbox cannot read them.
UNREAD_CONFIG = "const rules = require('./rules');\nmodule.exports = { forbidden: rules.map((rule) => rule) };\n"


def _flat(prefix: str, count: int) -> dict[str, str]:
    return {f"{prefix}lib/file{i}.ts": f"export const file{i} = {i};\n" for i in range(count)}


def _environment() -> dict[str, str]:
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join(filter(None, (str(REPO / "src"), environment.get("PYTHONPATH", ""))))
    environment["WINGBOX_AUTO_REFRESH"] = "0"
    environment.pop("CLAUDECODE", None)
    return environment


class BoundariesCoverageTest(unittest.TestCase):
    def project(self, files: dict[str, str]) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(os.path.realpath(tmp.name)) / "project"
        root.mkdir()
        for name, text in files.items():
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            (root / name).write_text(text, encoding="utf-8")
        built = self.wingbox(root, "build", str(root))
        self.assertEqual(0, built.returncode, built.stderr)
        return root

    def wingbox(self, root: Path, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, "-m", "wingbox.cli", *args], capture_output=True, text=True,
                              cwd=root, env=_environment(), timeout=600)

    def boundaries(self, root: Path) -> tuple[int, list[str]]:
        done = self.wingbox(root, "boundaries", "all")
        return done.returncode, done.stdout.splitlines()

    def test_every_part_checked_says_ok(self) -> None:
        """/* REQ-BCOV-001: when every part with files was checked against rules that were read or determined, boundaries prints boundaries-ok and exits 0. */"""
        status, lines = self.boundaries(self.project(LAYERED))
        self.assertEqual(0, status, lines)
        self.assertEqual(["(boundaries-ok :checked 14)"], lines)

    def test_a_part_whose_declared_rules_were_not_read_is_not_checked(self) -> None:
        """/* REQ-BCOV-002: a part whose declared rules were not read is reported first as boundaries-not-checked with the reason, the rule files and its file count; boundaries-partial replaces ok; the exit status is 2. */"""
        root = self.project({"package.json": '{"name": "x"}\n', ".dependency-cruiser.js": UNREAD_CONFIG, **_flat("", 13)})
        status, lines = self.boundaries(root)
        self.assertEqual(2, status, lines)
        self.assertRegex(lines[0], r'^\(boundaries-not-checked :part \. :reason declared-rules-not-read '
                                   r':files \(\.dependency-cruiser\.js\) :files-in-part \d+\)$')
        count = int(re.search(r":files-in-part (\d+)", lines[0]).group(1))
        self.assertIn(f"(boundaries-partial :checked 0 :not-checked {count})", lines)
        self.assertFalse(any(line.startswith("(boundaries-ok") for line in lines), lines)

    def test_checked_and_unchecked_parts_give_a_partial_result(self) -> None:
        """/* REQ-BCOV-003: in a monorepo with one part checked and one whose declared rules were not read, boundaries prints boundaries-not-checked for the second part and boundaries-partial with both file counts; the exit status is 2. */"""
        files = {
            "packages/shop/" + name: text for name, text in LAYERED.items()
        }
        files.update({"packages/legacy/package.json": '{"name": "legacy"}\n',
                      "packages/legacy/.dependency-cruiser.js": UNREAD_CONFIG, **_flat("packages/legacy/", 3)})
        status, lines = self.boundaries(self.project(files))
        self.assertEqual(2, status, lines)
        self.assertRegex(lines[0], r'^\(boundaries-not-checked :part packages/legacy :reason declared-rules-not-read '
                                   r':files \(packages/legacy/\.dependency-cruiser\.js\) :files-in-part \d+\)$')
        self.assertTrue(any(re.match(r"^\(boundaries-partial :checked 14 :not-checked \d+\)$", line) for line in lines), lines)

    def test_an_undetermined_style_is_not_checked_but_is_not_an_error(self) -> None:
        """/* REQ-BCOV-004: a part whose style is undetermined is reported as boundaries-not-checked (style-undetermined) with boundaries-partial instead of ok; with no declared rules left unread the exit status stays 0. */"""
        files = dict(LAYERED)
        files[".wingbox/config.yaml"] = "parts:\n  .:\n    style: undetermined\n"
        status, lines = self.boundaries(self.project(files))
        self.assertEqual(0, status, lines)
        self.assertEqual("(boundaries-not-checked :part . :reason style-undetermined :files-in-part 14)", lines[0])
        self.assertIn("(boundaries-partial :checked 0 :not-checked 14)", lines)

    def test_a_violation_exits_1(self) -> None:
        """/* REQ-BCOV-005: a boundary violation in a checked part makes boundaries exit 1. */"""
        files = dict(LAYERED)
        files["src/models/models0.ts"] = "import { routes0 } from '../routes/routes0';\nexport const models0 = routes0;\n"
        status, lines = self.boundaries(self.project(files))
        self.assertEqual(1, status, lines)
        self.assertTrue(any(line.startswith("(violation ") for line in lines), lines)

    def test_report_advice_and_the_mcp_tool_say_the_same(self) -> None:
        """/* REQ-BCOV-006: REPORT.md, `wingbox advice` (its text and its facts) and the MCP boundaries tool name the part that was not checked, its reason and its rule files, and never say ok for it. */"""
        root = self.project({"package.json": '{"name": "x"}\n', ".dependency-cruiser.js": UNREAD_CONFIG, **_flat("", 13)})
        report = (root / ".wingbox" / "REPORT.md").read_text(encoding="utf-8")
        self.assertIn("Not checked: part `.` - declared rules not read (`.dependency-cruiser.js`)", report)
        advice = self.wingbox(root, "advice", "--human")
        self.assertIn("not checked: part . - declared rules not read (.dependency-cruiser.js)", advice.stdout)
        advice_sexp = self.wingbox(root, "advice", "--sexp")
        self.assertIn('(advice-none "." :reason "not checked: declared rules not read (.dependency-cruiser.js)")', advice_sexp.stdout)
        lines = [
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "0"}}}),
            json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
            json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "boundaries", "arguments": {}}}),
        ]
        done = subprocess.run([sys.executable, "-m", "wingbox", "mcp", "--root", str(root)], input="\n".join(lines) + "\n",
                              capture_output=True, text=True, cwd=root, env=_environment(), timeout=300)
        answer = next(json.loads(line) for line in done.stdout.splitlines() if '"id": 2' in line or '"id":2' in line)
        text = answer["result"]["content"][0]["text"]
        self.assertIn("(boundaries-not-checked :part . :reason declared-rules-not-read", text)
        self.assertNotIn("boundaries-ok", text)


if __name__ == "__main__":
    unittest.main()
