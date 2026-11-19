"""`wingbox boundaries --strict`: one result line and an exit status a gate can rely on.

Strict mode ends with `(boundaries-result :status pass|fail|incomplete|error ...)`
and exits 0 only when every part with files was checked and at least one file
was checked; 1 for a violation, 2 for incomplete coverage, 3 when the graph is
missing or cannot be read. Without `--strict` the output and the exit status
stay as they were. Every command runs as a process, as a gate runs it.
"""
import os
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
UPWARD_IMPORT = "import { routes0 } from '../routes/routes0';\nexport const models0 = routes0;\n"
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


class BoundariesStrictTest(unittest.TestCase):
    def folder(self, files: dict[str, str]) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(os.path.realpath(tmp.name)) / "project"
        root.mkdir()
        for name, text in files.items():
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            (root / name).write_text(text, encoding="utf-8")
        return root

    def project(self, files: dict[str, str]) -> Path:
        root = self.folder(files)
        built = self.wingbox(root, "build", str(root))
        self.assertEqual(0, built.returncode, built.stderr)
        return root

    def wingbox(self, root: Path, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, "-m", "wingbox.cli", *args], capture_output=True, text=True,
                              cwd=root, env=_environment(), timeout=600)

    def strict(self, root: Path, *args: str) -> tuple[int, list[str]]:
        done = self.wingbox(root, "boundaries", *(args or ("all",)), "--strict")
        return done.returncode, done.stdout.splitlines()

    def plain(self, root: Path) -> tuple[int, list[str]]:
        done = self.wingbox(root, "boundaries", "all")
        return done.returncode, done.stdout.splitlines()

    def test_every_part_checked_passes(self) -> None:
        """/* REQ-BCOV-007: with --strict, when every part with files was checked and nothing breaks a rule, the last line is boundaries-result :status pass with the file counts, each checked part has a boundaries-checked fact with its style and its number of modules, and the exit status is 0. */"""
        status, lines = self.strict(self.project(LAYERED))
        self.assertEqual(0, status, lines)
        self.assertEqual([
            "(boundaries-ok :checked 14)",
            "(boundaries-checked :part . :style layered :modules 14)",
            "(boundaries-result :status pass :checked 14 :not-checked 0 :violations 0 :known 0)",
        ], lines)

    def test_a_violation_fails_with_exit_1(self) -> None:
        """/* REQ-BCOV-008: with --strict, a boundary violation gives boundaries-result :status fail with the number of violations as the last line, after the unchanged violation facts, and the exit status 1. */"""
        files = dict(LAYERED)
        files["src/models/models0.ts"] = UPWARD_IMPORT
        root = self.project(files)
        status, lines = self.strict(root)
        self.assertEqual(1, status, lines)
        self.assertEqual("(boundaries-result :status fail :checked 14 :not-checked 0 :violations 1 :known 0)", lines[-1])
        self.assertIn("(boundaries-checked :part . :style layered :modules 14)", lines)
        plain_status, plain_lines = self.plain(root)
        self.assertEqual(1, plain_status)
        self.assertEqual(plain_lines, lines[:len(plain_lines)])

    def test_declared_rules_not_read_are_incomplete(self) -> None:
        """/* REQ-BCOV-009: with --strict, a part whose declared rules were not read gives boundaries-result :status incomplete :reason part-not-checked, no boundaries-checked fact for that part, and the exit status 2. */"""
        root = self.project({"package.json": '{"name": "x"}\n', ".dependency-cruiser.js": UNREAD_CONFIG, **_flat("", 13)})
        status, lines = self.strict(root)
        self.assertEqual(2, status, lines)
        self.assertRegex(lines[-1], r"^\(boundaries-result :status incomplete :checked 0 :not-checked \d+ :violations 0 "
                                    r":known 0 :reason part-not-checked\)$")
        self.assertNotIn(":not-checked 0 ", lines[-1])
        self.assertFalse(any(line.startswith("(boundaries-checked") for line in lines), lines)

    def test_an_undetermined_style_is_incomplete(self) -> None:
        """/* REQ-BCOV-010: with --strict, a part whose style is undetermined is not a pass: boundaries-result :status incomplete and the exit status 2, where the same project without --strict still exits 0. */"""
        files = dict(LAYERED)
        files[".wingbox/config.yaml"] = "parts:\n  .:\n    style: undetermined\n"
        root = self.project(files)
        status, lines = self.strict(root)
        self.assertEqual(2, status, lines)
        self.assertIn("(boundaries-not-checked :part . :reason style-undetermined :files-in-part 14)", lines)
        self.assertEqual(
            "(boundaries-result :status incomplete :checked 0 :not-checked 14 :violations 0 :known 0 :reason part-not-checked)",
            lines[-1])
        self.assertEqual(0, self.plain(root)[0])

    def test_a_project_without_source_files_is_incomplete(self) -> None:
        """/* REQ-BCOV-011: with --strict, a graph without any file to check gives boundaries-result :status incomplete :reason no-files-checked and the exit status 2: zero files checked is never a pass. */"""
        root = self.project({"README.md": "# nothing to parse\n"})
        status, lines = self.strict(root)
        self.assertEqual(2, status, lines)
        self.assertEqual(
            "(boundaries-result :status incomplete :checked 0 :not-checked 0 :violations 0 :known 0 :reason no-files-checked)",
            lines[-1])
        self.assertEqual((0, ["(boundaries-ok :checked 0)"]), self.plain(root))

    def test_no_graph_is_an_error_with_exit_3(self) -> None:
        """/* REQ-BCOV-012: with --strict, a folder without .wingbox/ gives boundaries-result :status error :reason no-graph on standard output and the exit status 3, which no violation shares; without --strict the exit status stays 1 and nothing is printed on standard output. */"""
        root = self.folder(LAYERED)
        done = self.wingbox(root, "boundaries", "all", "--strict")
        self.assertEqual(3, done.returncode, done.stdout + done.stderr)
        self.assertEqual(
            ["(boundaries-result :status error :checked 0 :not-checked 0 :violations 0 :known 0 :reason no-graph)"],
            done.stdout.splitlines())
        self.assertIn("no .wingbox/ in this folder or its parents", done.stderr)
        plain = self.wingbox(root, "boundaries", "all")
        self.assertEqual((1, ""), (plain.returncode, plain.stdout))

    def test_a_damaged_graph_is_an_error_with_exit_3(self) -> None:
        """/* REQ-BCOV-013: with --strict, a graph that cannot be read gives the invalid-map error, then boundaries-result :status error :reason invalid-graph, and the exit status 3; without --strict the exit status stays 1. */"""
        root = self.project(LAYERED)
        (root / ".wingbox" / "index.sexp").write_text("this is not a code graph\n", encoding="utf-8")
        status, lines = self.strict(root)
        self.assertEqual(3, status, lines)
        self.assertTrue(lines[0].startswith("(error invalid-map "), lines)
        self.assertEqual(
            "(boundaries-result :status error :checked 0 :not-checked 0 :violations 0 :known 0 :reason invalid-graph)",
            lines[-1])
        plain_status, plain_lines = self.plain(root)
        self.assertEqual(1, plain_status, plain_lines)
        self.assertEqual(lines[:1], plain_lines)

    def test_one_part_not_checked_in_a_monorepo_does_not_pass(self) -> None:
        """/* REQ-BCOV-014: with --strict, in a monorepo with one part checked and one whose style is undetermined, the checked part has its boundaries-checked fact, the other has none, the result is incomplete with both file counts, and the exit status is 2 where it is 0 without --strict. */"""
        files = {"packages/shop/" + name: text for name, text in LAYERED.items()}
        files.update({"packages/legacy/package.json": '{"name": "legacy"}\n', **_flat("packages/legacy/", 3)})
        files[".wingbox/config.yaml"] = (
            "parts:\n  packages/shop:\n    style: layered\n  packages/legacy:\n    style: undetermined\n")
        root = self.project(files)
        status, lines = self.strict(root)
        self.assertEqual(2, status, lines)
        self.assertEqual([
            "(boundaries-not-checked :part packages/legacy :reason style-undetermined :files-in-part 3)",
            "(boundaries-partial :checked 14 :not-checked 3)",
            "(boundaries-checked :part packages/shop :style layered :modules 14)",
            "(boundaries-result :status incomplete :checked 14 :not-checked 3 :violations 0 :known 0 :reason part-not-checked)",
        ], lines)
        self.assertEqual(0, self.plain(root)[0])

    def test_without_strict_nothing_changes(self) -> None:
        """/* REQ-BCOV-015: without --strict, boundaries prints no boundaries-result and no boundaries-checked line; the strict output is the plain output followed by the strict lines. */"""
        root = self.project(LAYERED)
        plain_status, plain_lines = self.plain(root)
        self.assertEqual((0, ["(boundaries-ok :checked 14)"]), (plain_status, plain_lines))
        _, lines = self.strict(root)
        self.assertEqual(plain_lines, lines[:len(plain_lines)])
        added = lines[len(plain_lines):]
        self.assertEqual(["(boundaries-checked ", "(boundaries-result "], [line.split(":", 1)[0] for line in added])

    def test_a_violation_outranks_a_part_not_checked(self) -> None:
        """/* REQ-BCOV-016: with --strict, a violation in a checked part beside a part that was not checked gives :status fail and the exit status 1, and the result line still carries the number of files not checked. */"""
        files = {"packages/shop/" + name: text for name, text in LAYERED.items()}
        files["packages/shop/src/models/models0.ts"] = UPWARD_IMPORT
        files.update({"packages/legacy/package.json": '{"name": "legacy"}\n', **_flat("packages/legacy/", 3)})
        files[".wingbox/config.yaml"] = (
            "parts:\n  packages/shop:\n    style: layered\n  packages/legacy:\n    style: undetermined\n")
        status, lines = self.strict(self.project(files))
        self.assertEqual(1, status, lines)
        self.assertEqual("(boundaries-result :status fail :checked 14 :not-checked 3 :violations 1 :known 0)", lines[-1])

    def test_a_scope_that_matches_nothing_is_incomplete(self) -> None:
        """/* REQ-BCOV-017: with --strict, a scope that matches no module gives the no-match error, then boundaries-result :status incomplete :reason no-files-checked, and the exit status 2. */"""
        status, lines = self.strict(self.project(LAYERED), "no/such/module")
        self.assertEqual(2, status, lines)
        self.assertTrue(lines[0].startswith("(error no-match "), lines)
        self.assertEqual(
            "(boundaries-result :status incomplete :checked 0 :not-checked 0 :violations 0 :known 0 :reason no-files-checked)",
            lines[-1])

    def test_files_in_no_part_are_not_checked(self) -> None:
        """/* REQ-BCOV-019: with --strict, source files that belong to no part of the configured architecture are listed as boundaries-not-checked with the reason in-no-part and make the result incomplete (exit status 2), where plain mode counts them as checked and says ok. */"""
        files = {"packages/shop/" + name: text for name, text in LAYERED.items()}
        files.update({"packages/legacy/package.json": '{"name": "legacy"}\n', **_flat("packages/legacy/", 3)})
        files[".wingbox/config.yaml"] = "parts:\n  packages/shop:\n    style: layered\n"
        root = self.project(files)
        self.assertEqual((0, ["(boundaries-ok :checked 17)"]), self.plain(root))
        status, lines = self.strict(root)
        self.assertEqual(2, status, lines)
        self.assertEqual([
            "(boundaries-ok :checked 17)",
            "(boundaries-not-checked :part - :reason in-no-part :files-in-part 3)",
            "(boundaries-checked :part packages/shop :style layered :modules 14)",
            "(boundaries-result :status incomplete :checked 14 :not-checked 3 :violations 0 :known 0 :reason part-not-checked)",
        ], lines)

    def test_a_break_accepted_in_the_baseline_is_counted_as_known(self) -> None:
        """/* REQ-BCOV-018: with --strict, a break listed in .wingbox/baseline.sexp is counted in :known and not in :violations; the result is pass and the exit status 0. */"""
        files = dict(LAYERED)
        files["src/models/models0.ts"] = UPWARD_IMPORT
        files[".wingbox/baseline.sexp"] = '(baseline "." src/models/models0 src/routes/routes0 :rule "domain -> api" :from align)\n'
        status, lines = self.strict(self.project(files))
        self.assertEqual(0, status, lines)
        self.assertTrue(any(line.startswith("(violation-known ") for line in lines), lines)
        self.assertEqual("(boundaries-result :status pass :checked 14 :not-checked 0 :violations 0 :known 1)", lines[-1])


if __name__ == "__main__":
    unittest.main()
