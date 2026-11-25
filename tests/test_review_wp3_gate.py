"""`wingbox boundaries` says pass only for what it judged (review of 0.19.1, the gate).

The states in which the gate said pass although a rule was broken or files were
never read: files of a language without a parser, a graph older than the files
or than the rule files, files in no part, files no rule places, a file that a
name exempts and that relays an import, baseline entries that hide more than
they state. Every command runs as a process, as a gate runs it.
"""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from scripts.architecture import baseline, capabilities  # noqa: E402
from scripts.architecture.baseline import Acceptance, read_baseline_terms  # noqa: E402
from scripts.architecture.capabilities import detect  # noqa: E402
from scripts.architecture.detect import is_test_file  # noqa: E402
from scripts.architecture.library import load_library  # noqa: E402

LIBRARY = load_library()
LAYERED = {
    "package.json": '{"name": "shop"}\n',
    **{f"src/{folder}/{folder}{i}.ts": f"export const {folder}{i} = {i};\n"
       for folder, count in (("routes", 4), ("services", 4), ("models", 3), ("repositories", 3)) for i in range(count)},
}
UPWARD_IMPORT = "import { routes0 } from '../routes/routes0';\nexport const models0 = routes0;\n"
LAYERED_CONFIG = """parts:
  .:
    style: layered
    layers:
      api: [routes]
      service: [services]
      infra: [repositories]
      domain: [{domain}]
"""
BASELINE_ENTRY = '(baseline "." src/models/models0 src/routes/routes0 :rule "domain -> api" :from align'
PASS_14 = "(boundaries-result :status pass :checked 14 :not-checked 0 :violations 0 :known 0)"


def _environment(**extra: str) -> dict[str, str]:
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join(filter(None, (str(REPO / "src"), environment.get("PYTHONPATH", ""))))
    environment["WINGBOX_AUTO_REFRESH"] = "0"
    environment.pop("CLAUDECODE", None)
    environment.update(extra)
    return environment


class _Gate(unittest.TestCase):
    def folder(self, files: dict[str, str]) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(os.path.realpath(tmp.name)) / "project"
        root.mkdir()
        self.write(root, files)
        return root

    def write(self, root: Path, files: dict[str, str]) -> None:
        for name, text in files.items():
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            (root / name).write_text(text, encoding="utf-8")

    def build(self, root: Path) -> None:
        built = self.wingbox(root, "build", str(root))
        self.assertEqual(0, built.returncode, built.stderr)

    def project(self, files: dict[str, str]) -> Path:
        root = self.folder(files)
        self.build(root)
        return root

    def wingbox(self, root: Path, *args: str, **environment: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, "-m", "wingbox.cli", *args], capture_output=True, text=True,
                              cwd=root, env=_environment(**environment), timeout=600)

    def strict(self, root: Path, *args: str) -> tuple[int, list[str]]:
        done = self.wingbox(root, "boundaries", *(args or ("all",)), "--strict")
        return done.returncode, done.stdout.splitlines()

    def plain(self, root: Path, *args: str) -> tuple[int, list[str]]:
        done = self.wingbox(root, "boundaries", *(args or ("all",)))
        return done.returncode, done.stdout.splitlines()

    def graph(self, root: Path) -> str:
        return (root / ".wingbox" / "index.sexp").read_text(encoding="utf-8")


class FilesNotReadTest(_Gate):
    KOTLIN = {
        "package.json": '{"name": "shop"}\n',
        **{f"src/{folder}/{folder}{i}.kt": f"package shop.{folder}\n\nval {folder}{i} = {i}\n"
           for folder in ("routes", "services", "models", "repositories") for i in range(4)},
        "src/services/tool.ts": "export const tool = 1;\n",
    }

    def test_files_of_a_language_without_a_parser_are_not_checked(self) -> None:
        """/* REQ-BCOV-9300: the source files of a part that are written in a language no parser reads are in no module, so no rule judged them: the graph states their number per part and language (arch-not-parsed), boundaries lists them as boundaries-not-checked with the reason not-parsed-language and the languages, prints boundaries-partial instead of ok, and with --strict the result is incomplete with the reason not-parsed-language and the exit status 2. */"""
        root = self.project(self.KOTLIN)
        self.assertIn('(arch-not-parsed "." "Kotlin" :files 16)', self.graph(root))
        status, lines = self.plain(root)
        self.assertEqual(0, status, lines)
        self.assertEqual([
            "(boundaries-not-checked :part . :reason not-parsed-language :languages (Kotlin) :files-in-part 16)",
            "(boundaries-partial :checked 1 :not-checked 16)",
        ], lines)
        status, lines = self.strict(root)
        self.assertEqual(2, status, lines)
        self.assertEqual([
            "(boundaries-not-checked :part . :reason not-parsed-language :languages (Kotlin) :files-in-part 16)",
            "(boundaries-partial :checked 1 :not-checked 16)",
            "(boundaries-checked :part . :style layered :modules 1)",
            "(boundaries-result :status incomplete :checked 1 :not-checked 16 :violations 0 :known 0 :reason not-parsed-language)",
        ], lines)

    def test_a_part_without_any_module_is_not_a_pass(self) -> None:
        """/* REQ-BCOV-9301: a part whose every source file is in a language no parser reads has no module in the graph and is still reported: boundaries all --strict names it as not checked and does not pass on the other part alone. */"""
        files = {"packages/shop/" + name: text for name, text in LAYERED.items()}
        files.update({"packages/mobile/" + name: text for name, text in self.KOTLIN.items() if name.endswith((".kt", ".json"))})
        root = self.project(files)
        status, lines = self.strict(root)
        self.assertEqual(2, status, lines)
        self.assertIn(
            "(boundaries-not-checked :part packages/mobile :reason not-parsed-language :languages (Kotlin) :files-in-part 16)",
            lines)
        self.assertEqual(
            "(boundaries-result :status incomplete :checked 14 :not-checked 16 :violations 0 :known 0 :reason not-parsed-language)",
            lines[-1])

    def test_files_the_parts_do_not_count_as_source_change_nothing(self) -> None:
        """/* REQ-BCOV-9302: a shell script, a Dockerfile, a view template and a file of an unparsed language in a test folder or in an ignored folder are not source files of a part: they are not counted as not read, and the gate passes as before. */"""
        files = dict(LAYERED)
        files.update({
            "deploy.sh": "#!/bin/sh\necho deploy\n", "Dockerfile": "FROM scratch\n",
            "src/views/index.erb": "<p>shop</p>\n", "src/services/tests/ServiceTest.kt": "val x = 1\n",
            "docs/Example.kt": "val y = 2\n",
        })
        root = self.project(files)
        self.assertNotIn("arch-not-parsed", self.graph(root))
        status, lines = self.strict(root)
        self.assertEqual(0, status, lines)
        self.assertEqual(PASS_14, lines[-1])


class StaleFactsTest(_Gate):
    def test_a_graph_known_to_be_stale_is_incomplete(self) -> None:
        """/* REQ-BCOV-9303: with --strict, a graph that is known to miss a changed file (the refresh is off or failed) is not a pass: the result is incomplete with the reason graph-stale and the exit status 2, and no boundaries-ok line is printed; without --strict the answer stays as it was, with the warning on standard error. */"""
        root = self.project(LAYERED)
        self.assertEqual(0, self.strict(root)[0])
        self.write(root, {"src/models/models0.ts": UPWARD_IMPORT})
        done = self.wingbox(root, "boundaries", "all", "--strict")
        self.assertEqual(2, done.returncode, done.stdout + done.stderr)
        self.assertEqual([
            "(boundaries-checked :part . :style layered :modules 14)",
            "(boundaries-result :status incomplete :checked 14 :not-checked 0 :violations 0 :known 0 :reason graph-stale)",
        ], done.stdout.splitlines())
        self.assertIn("(wingbox-graph-stale :reason auto-refresh-off :changed-files 1)", done.stderr)
        self.assertEqual((0, ["(boundaries-ok :checked 14)"]), self.plain(root))

    def test_a_state_that_cannot_be_read_is_stale_for_the_gate(self) -> None:
        """/* REQ-BCOV-9315: when the record of what changed since the build cannot be read and the refresh is off, the graph is reported stale with no changed file counted; the gate is told so all the same and --strict is incomplete with the reason graph-stale. */"""
        root = self.project(LAYERED)
        (root / ".wingbox" / "graph-state.json").write_text("not a state\n", encoding="utf-8")
        done = self.wingbox(root, "boundaries", "all", "--strict")
        self.assertIn("(wingbox-graph-stale :reason auto-refresh-off :changed-files 0)", done.stderr)
        self.assertEqual(2, done.returncode, done.stdout + done.stderr)
        self.assertTrue(done.stdout.splitlines()[-1].endswith(":known 0 :reason graph-stale)"), done.stdout)

    def test_a_refresh_that_failed_leaves_the_gate_incomplete(self) -> None:
        """/* REQ-BCOV-9318: when the refresh before the query fails (a config that cannot be read, written after the build, and a changed source file), the graph is stale with the reason refresh-failed: --strict is incomplete with the reason graph-stale and the exit status 2 although the old facts hold no violation, and the changed config is named. */"""
        root = self.project(LAYERED)
        self.write(root, {".wingbox/config.yaml": "parts:\n  .:\n    style: [declared]\n",
                          "src/models/models0.ts": UPWARD_IMPORT})
        done = self.wingbox(root, "boundaries", "all", "--strict", WINGBOX_AUTO_REFRESH="1", WINGBOX_REFRESH_WAIT="120")
        self.assertIn("(wingbox-graph-stale :reason refresh-failed :changed-files 1)", done.stderr)
        self.assertEqual(2, done.returncode, done.stdout + done.stderr)
        self.assertEqual([
            '(boundaries-rules-changed :files (.wingbox/config.yaml) :hint "rebuild: wingbox build")',
            "(boundaries-checked :part . :style layered :modules 14)",
            "(boundaries-result :status incomplete :checked 14 :not-checked 0 :violations 0 :known 0 :reason graph-stale)",
        ], done.stdout.splitlines())

    def test_a_refreshed_graph_passes_or_fails_on_its_facts(self) -> None:
        """/* REQ-BCOV-9304: with the refresh on, the same change is read before the answer: the gate fails on the violation and names no stale graph. */"""
        root = self.project(LAYERED)
        self.write(root, {"src/models/models0.ts": UPWARD_IMPORT})
        done = self.wingbox(root, "boundaries", "all", "--strict", WINGBOX_AUTO_REFRESH="1", WINGBOX_REFRESH_WAIT="120")
        self.assertEqual(1, done.returncode, done.stdout + done.stderr)
        self.assertEqual("(boundaries-result :status fail :checked 14 :not-checked 0 :violations 1 :known 0)",
                         done.stdout.splitlines()[-1])

    def test_a_rule_file_changed_after_the_build_is_noticed(self) -> None:
        """/* REQ-BCOV-9305: the graph records what the build read of .wingbox/config.yaml, baseline.sexp and the learned layers of decisions.sexp; when one of them was edited, added or deleted since, boundaries names the files in a boundaries-rules-changed fact and prints no ok, with --strict the result is incomplete with the reason rules-changed (exit status 2), and after a build the gate answers from the new facts. */"""
        files = dict(LAYERED)
        files["src/models/models0.ts"] = UPWARD_IMPORT
        files[".wingbox/baseline.sexp"] = BASELINE_ENTRY + ")\n"
        root = self.project(files)
        self.assertRegex(self.graph(root), r"(?m)^\(arch-rule-files :config - :baseline [0-9a-f]{16} :learned -\)$")
        status, lines = self.strict(root)
        self.assertEqual((0, "(boundaries-result :status pass :checked 14 :not-checked 0 :violations 0 :known 1)"),
                         (status, lines[-1]))
        (root / ".wingbox" / "baseline.sexp").unlink()
        changed = '(boundaries-rules-changed :files (.wingbox/baseline.sexp) :hint "rebuild: wingbox build")'
        status, lines = self.strict(root)
        self.assertEqual(2, status, lines)
        self.assertEqual(changed, lines[0])
        self.assertEqual(
            "(boundaries-result :status incomplete :checked 14 :not-checked 0 :violations 0 :known 1 :reason rules-changed)",
            lines[-1])
        status, lines = self.plain(root)
        self.assertEqual(0, status, lines)
        self.assertEqual(changed, lines[0])
        self.assertFalse(any(line.startswith("(boundaries-ok") for line in lines), lines)
        self.build(root)
        status, lines = self.strict(root)
        self.assertEqual(1, status, lines)
        self.assertNotIn(changed, lines)
        # A config written after the build changes the rules too.
        self.write(root, {".wingbox/config.yaml": "parts:\n  .:\n    style: undetermined\n"})
        status, lines = self.strict(root)
        self.assertEqual('(boundaries-rules-changed :files (.wingbox/config.yaml) :hint "rebuild: wingbox build")', lines[0])
        self.assertEqual(1, status, lines)   # the violation of the facts it still has outranks an incomplete check

    def test_the_log_lines_of_a_build_are_not_a_change_of_the_rules(self) -> None:
        """/* REQ-BCOV-9306: of .wingbox/decisions.sexp only the learned layers are rules, and only without a config: the lines a build logs (build-run, advice-given, advice-outcome) leave the recorded digest as it is, a learned-layer line changes it, and with a config.yaml the learned layers are not applied and do not count. */"""
        from scripts.architecture import config, decisions, facts

        self.assertEqual((config.CONFIG_FILE, facts.BASELINE_FILE, decisions.STORE_FILE),
                         tuple(file for _name, file in baseline.RULE_FILES))
        texts = {".wingbox/decisions.sexp": "(build-run :graph 0123456789ab :at 2026-01-01)\n"}
        before = baseline.rule_file_digests(texts.get)
        self.assertEqual((("config", "-"), ("baseline", "-"), ("learned", "-")), before)
        texts[".wingbox/decisions.sexp"] += '(advice-given abc :part "." :kind move :target "a.ts" :rule "x" :at 2026-01-02 :graph 0)\n'
        self.assertEqual(before, baseline.rule_file_digests(texts.get))
        texts[".wingbox/decisions.sexp"] += '(learned-layer "." "zone" api :from abc :at 2026-01-03)\n'
        learned = baseline.rule_file_digests(texts.get)
        self.assertNotEqual(before, learned)
        self.assertRegex(dict(learned)["learned"], r"^[0-9a-f]{16}$")
        texts[".wingbox/config.yaml"] = "parts:\n  .:\n    style: layered\n"
        self.assertEqual("-", dict(baseline.rule_file_digests(texts.get))["learned"])
        self.assertRegex(dict(baseline.rule_file_digests(texts.get))["config"], r"^[0-9a-f]{16}$")

    def test_a_learned_layer_added_after_the_build_is_noticed(self) -> None:
        """/* REQ-BCOV-9307: a learned-layer line added to .wingbox/decisions.sexp after the build makes boundaries name that file as changed, and --strict incomplete with the reason rules-changed; the log lines the build wrote itself did not. */"""
        files = dict(LAYERED)
        files[".wingbox/decisions.sexp"] = "(build-run :graph 0123456789ab :at 2026-01-01)\n"
        files["src/zone/order.ts"] = "import { routes0 } from '../routes/routes0';\nexport const order = routes0;\n"
        root = self.project(files)
        self.assertEqual(0, self.strict(root)[0])
        with open(root / ".wingbox" / "decisions.sexp", "a", encoding="utf-8") as handle:
            handle.write('(learned-layer "." "zone" domain :from abc :at 2026-01-03)\n')
        status, lines = self.strict(root)
        self.assertEqual(2, status, lines)
        self.assertEqual('(boundaries-rules-changed :files (.wingbox/decisions.sexp) :hint "rebuild: wingbox build")', lines[0])
        self.assertTrue(lines[-1].endswith(":reason rules-changed)"), lines)


class CoverageCountsTest(_Gate):
    def monorepo(self) -> dict[str, str]:
        files = {"packages/shop/" + name: text for name, text in LAYERED.items()}
        files.update({"packages/legacy/package.json": '{"name": "legacy"}\n',
                      **{f"packages/legacy/lib/file{i}.ts": f"export const file{i} = {i};\n" for i in range(3)}})
        files[".wingbox/config.yaml"] = "parts:\n  packages/shop:\n    style: layered\n"
        return files

    def test_plain_mode_does_not_say_ok_for_files_in_no_part(self) -> None:
        """/* REQ-BCOV-9308: without --strict, source files that belong to no part of the configured architecture are listed as boundaries-not-checked with the reason in-no-part and boundaries-partial replaces ok, with the same counts as --strict gives; the exit status stays 0. */"""
        root = self.project(self.monorepo())
        status, lines = self.plain(root)
        self.assertEqual(0, status, lines)
        self.assertEqual([
            "(boundaries-not-checked :part - :reason in-no-part :files-in-part 3)",
            "(boundaries-partial :checked 14 :not-checked 3)",
        ], lines)
        strict_status, strict_lines = self.strict(root)
        self.assertEqual(2, strict_status, strict_lines)
        self.assertEqual(lines, strict_lines[:2])
        self.assertEqual(
            "(boundaries-result :status incomplete :checked 14 :not-checked 3 :violations 0 :known 0 :reason part-not-checked)",
            strict_lines[-1])

    def test_a_violation_beside_files_in_no_part_is_counted_the_same_in_both_modes(self) -> None:
        """/* REQ-BCOV-9309: with a violation in the checked part and files in no part, boundaries-summary counts the files that were checked (not every file of the scope), and boundaries-partial follows it without --strict as with it. */"""
        files = self.monorepo()
        files["packages/shop/src/models/models0.ts"] = UPWARD_IMPORT
        root = self.project(files)
        status, lines = self.plain(root)
        self.assertEqual(1, status, lines)
        self.assertEqual("(boundaries-not-checked :part - :reason in-no-part :files-in-part 3)", lines[0])
        self.assertEqual([
            "(boundaries-summary :violations 1 :known 0 :type-only 0 :candidates 0 :checked 14)",
            "(boundaries-partial :checked 14 :not-checked 3)",
        ], lines[-2:])
        self.assertEqual(lines, self.strict(root)[1][:len(lines)])

    def test_strict_mode_prints_no_ok_line_unless_the_result_is_pass(self) -> None:
        """/* REQ-BCOV-9310: with --strict, boundaries-ok is printed only in an answer whose result is pass: a project without a file to check ends in the incomplete result alone. */"""
        root = self.project({"README.md": "# nothing to parse\n"})
        self.assertEqual((2, [
            "(boundaries-result :status incomplete :checked 0 :not-checked 0 :violations 0 :known 0 :reason no-files-checked)",
        ]), self.strict(root))
        status, lines = self.strict(self.project(LAYERED))
        self.assertEqual((0, "(boundaries-ok :checked 14)", PASS_14), (status, lines[0], lines[-1]))

    def test_no_ok_line_before_capability_violations(self) -> None:
        """/* REQ-BCOV-9311: when the layer rules hold and a file does what its layer may not, boundaries prints the capability violation and its summary without a boundaries-ok line before them, with and without --strict; the exit status is 1. */"""
        files = {
            "pyproject.toml": '[project]\nname = "shop"\n',
            **{f"app/{folder}/{folder}{i}.py": f"VALUE_{i} = {i}\n"
               for folder in ("routes", "services", "models", "repositories") for i in range(4)},
            "app/models/models0.py": 'def find(cursor):\n    cursor.execute("SELECT id FROM orders")\n',
        }
        root = self.project(files)
        status, lines = self.plain(root)
        self.assertEqual(1, status, lines)
        self.assertTrue(lines[0].startswith("(capability-violation app/models/models0.py :capability sql "), lines)
        self.assertFalse(any(line.startswith("(boundaries-ok") for line in lines), lines)
        self.assertEqual("(capability-summary :violations 1 :known 0 :candidates 0 :patterns 0 :checked 16)", lines[-1])
        status, strict_lines = self.strict(root)
        self.assertEqual(1, status, strict_lines)
        self.assertEqual(lines, strict_lines[:len(lines)])
        self.assertEqual("(boundaries-result :status fail :checked 16 :not-checked 0 :violations 1 :known 0)", strict_lines[-1])

    def test_files_no_rule_places_are_not_counted_as_checked(self) -> None:
        """/* REQ-BCOV-9312: a file of a checked part that no layer rule places is not judged and is not counted in :checked: boundaries states the number per part in a boundaries-no-layer fact and as :no-layer on its ok, summary and result lines (the keyword is absent when the number is zero), and the gate still passes, because the rules say nothing about such files. */"""
        files = dict(LAYERED)
        files[".wingbox/config.yaml"] = LAYERED_CONFIG.replace("{domain}", "modles")   # a typo: models/ is in no layer
        files["src/models/models0.ts"] = UPWARD_IMPORT
        root = self.project(files)
        status, lines = self.plain(root)
        self.assertEqual(0, status, lines)
        self.assertEqual(["(boundaries-no-layer :part . :files-in-part 3)", "(boundaries-ok :checked 11 :no-layer 3)"], lines)
        status, lines = self.strict(root)
        self.assertEqual(0, status, lines)
        self.assertEqual([
            "(boundaries-no-layer :part . :files-in-part 3)",
            "(boundaries-ok :checked 11 :no-layer 3)",
            "(boundaries-checked :part . :style layered :modules 14)",
            "(boundaries-result :status pass :checked 11 :not-checked 0 :violations 0 :known 0 :no-layer 3)",
        ], lines)
        # The same project with the folder spelled right: every file is placed and the violation is found.
        self.write(root, {".wingbox/config.yaml": LAYERED_CONFIG.replace("{domain}", "models")})
        self.build(root)
        status, lines = self.strict(root)
        self.assertEqual(1, status, lines)
        self.assertEqual("(boundaries-result :status fail :checked 14 :not-checked 0 :violations 1 :known 0)", lines[-1])

    def test_a_part_in_which_no_file_is_placed_is_not_checked(self) -> None:
        """/* REQ-BCOV-9313: a part that has rules but no file in any of its layers was not checked: boundaries lists it as boundaries-not-checked with the reason no-file-in-a-layer and prints boundaries-partial; with --strict the result is incomplete with the reason no-files-checked and the exit status 2. */"""
        files = {
            "package.json": '{"name": "game"}\n',
            **{f"src/{folder}/{folder}{i}.ts": f"export const {folder}{i} = {i};\n"
               for folder in ("core", "game", "ui") for i in range(5)},
            # The folders are named relative to the part: core/ is src/core/, so no file is in a layer.
            ".wingbox/config.yaml": "parts:\n  .:\n    style: declared\n    order:\n      - core: [core]\n      - game: [game]\n      - ui: [ui]\n",
        }
        root = self.project(files)
        expected = [
            "(boundaries-not-checked :part . :reason no-file-in-a-layer :files-in-part 15)",
            "(boundaries-partial :checked 0 :not-checked 15)",
        ]
        self.assertEqual((0, expected), self.plain(root))
        self.assertEqual((2, expected + [
            "(boundaries-result :status incomplete :checked 0 :not-checked 15 :violations 0 :known 0 :reason no-files-checked)",
        ]), self.strict(root))

    def test_a_scope_that_matches_nothing_is_not_a_success(self) -> None:
        """/* REQ-BCOV-9314: without --strict, boundaries for a scope that matches no module prints the no-match error and exits 2 (nothing was checked), as --strict does. */"""
        root = self.project(LAYERED)
        done = self.wingbox(root, "boundaries", "nosuchfolder")
        self.assertEqual(2, done.returncode, done.stdout + done.stderr)
        self.assertTrue(done.stdout.startswith("(error no-match nosuchfolder"), done.stdout)
        self.assertEqual(2, self.strict(root, "nosuchfolder")[0])

    def test_a_config_whose_parts_match_nothing_gets_no_default_style(self) -> None:
        """/* REQ-ARCH-9317: a graph without architecture facts takes the built-in layered style only when the project has no .wingbox/config.yaml; with a config whose parts hold no file, no style is assumed: every file is in no part, boundaries says so and --strict is incomplete. */"""
        files = {
            "package.json": '{"name": "game"}\n',
            **{f"src/{folder}/{folder}{i}.ts": f"export const {folder}{i} = {i};\n"
               for folder in ("routes", "services", "models", "repositories") for i in range(4)},
            "src/models/models0.ts": UPWARD_IMPORT,
            ".wingbox/config.yaml": "parts:\n  ./src:\n    style: declared\n    order:\n      - models: [models]\n      - routes: [routes]\n",
        }
        root = self.folder(files)
        built = self.wingbox(root, "build", str(root))
        if built.returncode != 0 or "(arch-part " in self.graph(root):
            # A build that refuses such a config, or places its files, leaves no graph in this state:
            # REQ-ARCH-9316 keeps the state itself under test, on a graph written by hand.
            self.skipTest("the build does not publish a graph without parts for this config")
        expected = [
            "(boundaries-not-checked :part - :reason in-no-part :files-in-part 16)",
            "(boundaries-partial :checked 0 :not-checked 16)",
        ]
        self.assertEqual((0, expected), self.plain(root))
        status, lines = self.strict(root)
        self.assertEqual(2, status, lines)
        self.assertEqual(expected, lines[:2])
        self.assertEqual(
            "(boundaries-result :status incomplete :checked 0 :not-checked 16 :violations 0 :known 0 :reason part-not-checked)",
            lines[-1])
        layers = self.wingbox(root, "layers").stdout
        self.assertNotIn(":style layered", layers)


class DefaultStyleTest(unittest.TestCase):
    GRAPH = (
        "(code-graph :schema 1 :files 2 :nodes 2 :edges 1 :violations 0)\n(paths\n  (src\n    (models\n      (m0 order)\n    )\n"
        "    (routes\n      (m1 orders)\n    )\n  )\n)\n(node m0 ts \"general module\")\n(node m1 ts \"general module\")\n"
        "(imports m0 m1)\n"
    )

    def boundaries(self, config: bool) -> list[str]:
        import io
        from contextlib import redirect_stdout

        from scripts import code_graph_search as search

        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / ".wingbox"
            folder.mkdir()
            (folder / "index.sexp").write_text(self.GRAPH, encoding="utf-8")
            if config:
                (folder / "config.yaml").write_text("parts:\n  ./lib:\n    style: layered\n", encoding="utf-8")
            graph = search.CodeGraph()
            graph.load(str(folder / "index.sexp"), directory)
            output = io.StringIO()
            with redirect_stdout(output):
                search.cmd_boundaries(graph, "all")
        return output.getvalue().splitlines()

    def test_the_default_style_is_for_a_project_without_a_config(self) -> None:
        """/* REQ-ARCH-9316: a graph without architecture facts is judged by the built-in layered style when the project has no .wingbox/config.yaml (the domain -> api import is a violation); when a config exists and the graph still has no part, no style is assumed: the files are in no part and nothing is said to be ok. */"""
        lines = self.boundaries(config=False)
        self.assertTrue(lines[0].startswith("(violation src/models/order src/routes/orders "), lines)
        self.assertEqual([
            "(boundaries-not-checked :part - :reason in-no-part :files-in-part 2)",
            "(boundaries-partial :checked 0 :not-checked 2)",
        ], self.boundaries(config=True))


class ExemptNamesTest(_Gate):
    def violations(self, root: Path) -> list[tuple[str, str]]:
        status, lines = self.strict(root)
        found = [tuple(line.split()[1:3]) for line in lines if line.startswith("(violation ")]
        self.assertEqual(1 if found else 0, status, lines)
        return found

    def test_a_file_named_like_a_composition_root_does_not_relay_an_import(self) -> None:
        """/* REQ-ARCH-9320: a file with the name of a composition root (container.ts) that a file of a layer imports is judged like any file of its folder's layer: its import of a route from the models folder is a domain -> api violation. */"""
        files = dict(LAYERED)
        files["src/models/container.ts"] = "import { routes0 } from '../routes/routes0';\nexport const X = routes0;\n"
        files["src/models/models0.ts"] = "import { X } from './container';\nexport const models0 = X;\n"
        self.assertEqual([("src/models/container", "src/routes/routes0")], self.violations(self.project(files)))

    def test_a_file_named_like_a_test_does_not_relay_an_import(self) -> None:
        """/* REQ-ARCH-9321: a file with a test name (relay.test-helpers.ts) that a file of a layer imports is judged like the files of its folder: its import of a route from the models folder is a domain -> api violation, also when it sits in a test folder below the layer folder. */"""
        for helper, imported in (("src/models/relay.test-helpers.ts", "./relay.test-helpers"),
                                 ("src/models/__tests__/relay.ts", "./__tests__/relay")):
            files = dict(LAYERED)
            back = "../" * (helper.count("/") - 1)
            files[helper] = f"import {{ routes0 }} from '{back}routes/routes0';\nexport const X = routes0;\n"
            files["src/models/models0.ts"] = f"import {{ X }} from '{imported}';\nexport const models0 = X;\n"
            root = self.project(files)
            self.assertEqual([(helper[:-3], "src/routes/routes0")], self.violations(root), helper)
            self.assertIn(":src-layer domain :tgt-layer api", "\n".join(self.strict(root)[1]))

    def test_importing_a_file_named_main_does_not_exempt_a_file(self) -> None:
        """/* REQ-ARCH-9322: a file of a layer that nothing imports is not an entry point because it imports a file named main.ts: that file is then imported by a file of a layer and is no composition root, and the import of a route from the models folder is a violation. */"""
        files = dict(LAYERED)
        files["src/services/main.ts"] = "export const main = 1;\n"
        files["src/models/job.ts"] = ("import { main } from '../services/main';\nimport { routes0 } from '../routes/routes0';\n"
                                      "export const job = [main, routes0];\n")
        self.assertEqual([("src/models/job", "src/routes/routes0")], self.violations(self.project(files)))

    def test_composition_roots_and_tests_keep_their_exemption(self) -> None:
        """/* REQ-ARCH-9323: a composition root that only other composition roots and tests import (main.ts, and the container.ts it imports), a test, and a test helper that only tests import, may import every layer: no violation is stated for them. */"""
        files = dict(LAYERED)
        files["src/repositories/container.ts"] = "import { routes0 } from '../routes/routes0';\nexport const container = routes0;\n"
        files["src/repositories/main.ts"] = ("import { container } from './container';\nimport { routes1 } from '../routes/routes1';\n"
                                             "export const app = [container, routes1];\n")
        files["src/models/order.test-helpers.ts"] = "import { routes2 } from '../routes/routes2';\nexport const fake = routes2;\n"
        files["src/models/order.test.ts"] = ("import { fake } from './order.test-helpers';\nimport { routes3 } from '../routes/routes3';\n"
                                             "import { container } from '../repositories/container';\nexport const t = [fake, routes3, container];\n")
        root = self.project(files)
        self.assertEqual([], self.violations(root))
        self.assertEqual("(boundaries-result :status pass :checked 18 :not-checked 0 :violations 0 :known 0)",
                         self.strict(root)[1][-1])


class BaselineEntriesTest(_Gate):
    def gate(self, *entries: str) -> tuple[int, list[str]]:
        files = dict(LAYERED)
        files["src/models/models0.ts"] = UPWARD_IMPORT
        files[".wingbox/baseline.sexp"] = "".join(entry + "\n" for entry in entries)
        return self.strict(self.project(files))

    def test_an_end_date_is_a_calendar_date(self) -> None:
        """/* REQ-ARCH-9330: :expires is read as a calendar date: 2026-99-99 and 2026-02-30 have the form YYYY-MM-DD and name no day, so the entry is expired and its break is a violation again with :expired; a real date still holds through its last day. */"""
        self.assertTrue(Acceptance(expires="2026-99-99").expired("2020-01-01"))
        self.assertTrue(Acceptance(expires="2026-02-30").expired("2020-01-01"))
        self.assertTrue(Acceptance(expires="20270131").expired("2020-01-01"))
        self.assertFalse(Acceptance(expires="2028-02-29").expired("2028-02-29"))
        self.assertTrue(Acceptance(expires="2028-02-29").expired("2028-03-01"))
        status, lines = self.gate(BASELINE_ENTRY + " :expires 2026-99-99)")
        self.assertEqual(1, status, lines)
        self.assertRegex(lines[0], r'^\(violation src/models/models0 src/routes/routes0 .* :expired 2026-99-99 ')

    def test_a_second_entry_does_not_undo_an_end_date(self) -> None:
        """/* REQ-ARCH-9331: an entry past its end date stays expired when the baseline holds another entry for the same two files that states no end date: one with a reason only, or one written for another part; an end date is changed in its entry. read_baseline_terms keeps the earliest end date of the entries for one finding. */"""
        expired = BASELINE_ENTRY + " :expires 2020-01-01)"
        for second in (BASELINE_ENTRY + ' :reason "again")', BASELINE_ENTRY + ")",
                       '(baseline "x" src/models/models0 src/routes/routes0 :from align)'):
            for entries in ((expired, second), (second, expired)):
                status, lines = self.gate(*entries)
                self.assertEqual(1, status, (entries, lines))
                self.assertTrue(lines[0].startswith("(violation src/models/models0 src/routes/routes0 "), (entries, lines))
                self.assertIn(":expired 2020-01-01", lines[0])
        key = ("baseline", ".", "src/models/models0", "src/routes/routes0")
        merged = read_baseline_terms("\n".join((BASELINE_ENTRY + ' :owner "Ada" :expires 2027-03-31)',
                                                BASELINE_ENTRY + ' :reason "again" :expires 2028-01-01)')))
        self.assertEqual({key: Acceptance("Ada", "again", "2027-03-31")}, dict(merged))

    def test_an_entry_accepts_the_rule_it_states(self) -> None:
        """/* REQ-ARCH-9332: a baseline entry that states a rule accepts a break of that rule only: an entry for "service -> api" between two files does not hide their domain -> api break, which is a violation; an entry that states the rule of the break, or no rule, accepts it as before. */"""
        other = '(baseline "." src/models/models0 src/routes/routes0 :rule "service -> api" :from align)'
        status, lines = self.gate(other)
        self.assertEqual(1, status, lines)
        self.assertTrue(lines[0].startswith("(violation src/models/models0 src/routes/routes0 "), lines)
        for entry in (BASELINE_ENTRY + ")", '(baseline "." src/models/models0 src/routes/routes0 :from align)'):
            status, lines = self.gate(entry)
            self.assertEqual(0, status, (entry, lines))
            self.assertTrue(lines[0].startswith("(violation-known src/models/models0 src/routes/routes0 "), lines)
            self.assertEqual("(boundaries-result :status pass :checked 14 :not-checked 0 :violations 0 :known 1)", lines[-1])

    def test_an_expired_capability_entry_is_not_undone_by_another_part(self) -> None:
        """/* REQ-ARCH-9333: a baseline-capability entry past its end date stays expired when another entry for the same file and capability, written for another part, states no end date. */"""
        files = {
            "pyproject.toml": '[project]\nname = "shop"\n',
            **{f"app/{folder}/{folder}{i}.py": f"VALUE_{i} = {i}\n"
               for folder in ("routes", "services", "models", "repositories") for i in range(4)},
            "app/models/models0.py": 'def find(cursor):\n    cursor.execute("SELECT id FROM orders")\n',
        }
        entry = "app/models/models0 sql :line 2 :from align"
        files[".wingbox/baseline.sexp"] = f'(baseline-capability "." {entry} :expires 2020-01-01)\n'
        root = self.project(files)
        status, lines = self.strict(root)
        self.assertEqual(1, status, lines)
        self.assertIn(":expired 2020-01-01", lines[0])
        self.write(root, {".wingbox/baseline.sexp": f'(baseline-capability "." {entry} :expires 2020-01-01)\n'
                                                     f'(baseline-capability "x" {entry})\n'})
        self.build(root)
        status, lines = self.strict(root)
        self.assertEqual(1, status, lines)
        self.assertTrue(lines[0].startswith("(capability-violation app/models/models0.py "), lines)


class CapabilityDetectorTest(_Gate):
    def test_a_language_the_detector_does_not_read_is_named(self) -> None:
        """/* REQ-CAP-9340: in a part with capability rules, the files of a language the capability detector does not read are stated: the graph holds their number per part and language (arch-capability-unread), and boundaries prints a capability-not-read fact with the part, the language and the number; the status does not change. */"""
        files = {
            "Cargo.toml": '[package]\nname = "shop"\nversion = "0.1.0"\n',
            "src/lib.rs": "pub mod models;\npub mod repositories;\npub mod routes;\npub mod services;\n",
            **{f"src/{folder}/mod.rs": "".join(f"pub mod {folder}{i};\n" for i in range(4))
               for folder in ("routes", "services", "models", "repositories")},
            **{f"src/{folder}/{folder}{i}.rs": f"pub fn {folder}{i}() -> i32 {{ {i} }}\n"
               for folder in ("routes", "services", "models", "repositories") for i in range(4)},
            "src/models/models0.rs": 'pub async fn find(pool: &sqlx::PgPool) {\n    sqlx::query("SELECT id FROM orders").fetch_one(pool).await;\n}\n',
        }
        root = self.project(files)
        self.assertIn('(arch-capability-unread "." "Rust" :files 21)', self.graph(root))
        status, lines = self.strict(root)
        self.assertEqual(0, status, lines)
        self.assertIn("(capability-not-read :part . :language Rust :files 21)", lines)
        self.assertTrue(lines[-1].startswith("(boundaries-result :status pass "), lines)
        self.assertIn("(capability-not-read :part . :language Rust :files 21)", self.plain(root)[1])

    def test_languages_the_detector_reads_give_no_such_fact(self) -> None:
        """/* REQ-CAP-9341: a part whose files the capability detector reads (TypeScript) has no arch-capability-unread fact and boundaries prints no capability-not-read line. */"""
        root = self.project(LAYERED)
        self.assertNotIn("arch-capability-unread", self.graph(root))
        self.assertEqual((0, ["(boundaries-ok :checked 14)"]), self.plain(root))

    def test_the_detector_reads_a_bounded_part_of_a_long_line(self) -> None:
        """/* REQ-CAP-9342: the capability detector reads at most MAX_LINE_LENGTH characters of a line: a query call within them is found, one that starts after them is not, and a line of 60000 characters of words that start SQL statements without ending them costs no more than its first MAX_LINE_LENGTH characters. */"""
        query = 'cursor.execute("SELECT id FROM orders")'
        self.assertEqual(["sql"], [finding.capability for finding in detect("a.py", f"x = 1; {query}\n")])
        padding = "x = " + "1 + " * (capabilities.MAX_LINE_LENGTH // 4 + 1) + "1; "
        self.assertEqual([], list(detect("a.py", padding + query + "\n")))
        long_line = 'DATA = "' + "SELECT a, " * 6000 + '"\n'
        self.assertGreater(len(long_line), 60000)
        seen: list[int] = []
        original = capabilities._SQL_START

        class _Counting:
            def search(self, text: str, *rest: int):
                seen.append(len(text))
                return original.search(text, *rest)

            def match(self, text: str, *rest: int):
                seen.append(len(text))
                return original.match(text, *rest)

        capabilities._SQL_START = _Counting()
        try:
            detect("a.py", long_line + f"{query}\n")
        finally:
            capabilities._SQL_START = original
        self.assertTrue(seen)
        self.assertLessEqual(max(seen), capabilities.MAX_LINE_LENGTH + 1)


class TestNamesTest(unittest.TestCase):
    def test_a_capital_in_a_test_pattern_marks_the_word(self) -> None:
        """/* REQ-ARCH-9350: a test file pattern with a capital letter (*Test.java, *Tests.cs, *Test.cs) is matched as written: OrderTest.java and OrderTests.cs are tests, Latest.java, Contest.java and Greatest.cs are not; the lower-case patterns (*.test.*, test_*.py) match whatever the case of the file name. */"""
        for name in ("src/domain/OrderTest.java", "src/Domain/OrderTests.cs", "src/Domain/OrderTest.cs",
                     "src/models/order.test.ts", "src/models/Order.Test.ts", "app/test_orders.py", "pkg/orders_test.go"):
            self.assertTrue(is_test_file(name, LIBRARY), name)
        for name in ("src/domain/Latest.java", "src/domain/Contest.java", "src/Domain/Greatest.cs",
                     "src/Domain/Protests.cs", "src/models/order.ts"):
            self.assertFalse(is_test_file(name, LIBRARY), name)

    def test_the_django_tests_module_is_a_test(self) -> None:
        """/* REQ-ARCH-9351: tests.py, the test module Django creates in every application, is a test file; a module whose name only ends in tests (contests.py) is not. */"""
        self.assertTrue(is_test_file("shop/orders/tests.py", LIBRARY))
        self.assertFalse(is_test_file("shop/orders/contests.py", LIBRARY))
        self.assertFalse(is_test_file("shop/orders/views.py", LIBRARY))


if __name__ == "__main__":
    unittest.main()
