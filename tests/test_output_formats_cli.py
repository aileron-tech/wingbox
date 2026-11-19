"""The forms of an answer through the `wingbox` command, the MCP server, the benchmark readers and the skill.

Every command runs as a process, as a user or an agent runs it.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from tests.output_trees import CLI_CASES, CLI_TEXT_COMMANDS, KEPT_FILE, ROOT_MARK, BuiltProject, case_key, kept  # noqa: E402
from wingbox import freshness, install, mcp_server  # noqa: E402

KEPT = json.loads(KEPT_FILE.read_text(encoding="utf-8"))
# The benchmark scripts that run a Wingbox query command and read or measure what it prints.
BENCHMARK_READERS = ("benchmarks/impact/impact100.py", "benchmarks/php/tools/verify.py", "benchmarks/usages/usages.py",
                     "benchmarks/graphify/tools/command_check.py", "benchmarks/graphify/tools/formula_test.py")
_QUERY_CALL = re.compile(r'"(impact|usages)", |wb\("(analyze|quick|impact)"')


def load_script(path: Path):
    """A benchmark script as a module, loaded by path with its folder on the import path."""
    sys.path.insert(0, str(path.parent))
    try:
        spec = importlib.util.spec_from_file_location(path.stem, path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(path.parent))
    return module


_BUILT: list[BuiltProject] = []


def tearDownModule() -> None:
    while _BUILT:
        _BUILT.pop().close()


class BuiltProjectTest(unittest.TestCase):
    project: BuiltProject
    root: Path

    @classmethod
    def setUpClass(cls) -> None:
        # The project is built once for all the classes of this file.
        if not _BUILT:
            _BUILT.append(BuiltProject())
        cls.project = _BUILT[0]
        cls.root = cls.project.root

    def wingbox(self, *args: str, **env: str):
        return self.project.wingbox(*args, **env)


class WingboxImpactCommandTest(BuiltProjectTest):
    def test_several_files_in_one_call_with_the_state_of_the_graph(self) -> None:
        """/* REQ-CLI-039: `wingbox impact a b 2` prints `(graph :built <time> :files <n> :stale 0)` for a graph that
        is up to date, then one form per file in the order given; an unknown file gives its error form in place
        and exit status 1, and the other answers are printed. `--json` carries the same under "graph" and
        "impact"; `--human` prints one text block per file. */"""
        done = self.wingbox("impact", "src/routes/routes0.ts", "src/nowhere.ts", "src/models/models0.ts", "2")
        lines = done.stdout.splitlines()
        self.assertEqual(1, done.returncode, done.stderr)
        self.assertRegex(lines[0], r"^\(graph :built \d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ :files 14 :stale 0\)$")
        heads = ("(impact src/routes/routes0.ts :direct 1 :total 2", "(error no-match src/nowhere.ts",
                 "(impact src/models/models0.ts :direct 1 :total 1")
        self.assertEqual(list(heads), [head for line in lines for head in heads if line.startswith(head)])
        self.assertIn("  (:through src/models/models0.ts\n   src/services/services0.ts))\n", done.stdout)
        both = self.wingbox("impact", "src/routes/routes0.ts", "src/models/models0.ts")
        self.assertEqual(0, both.returncode, both.stderr)
        answer = json.loads(self.wingbox("impact", "src/routes/routes0.ts", "src/nowhere.ts", "--json").stdout)
        self.assertEqual({"files": 14, "stale": 0}, {key: answer["graph"][key] for key in ("files", "stale")})
        self.assertEqual(["src/models/models0.ts", "src/services/services0.ts"], [row["file"] for row in answer["impact"][0]["rows"]])
        self.assertEqual("no-match", answer["impact"][1]["error"])
        human = self.wingbox("impact", "src/routes/routes0.ts", "src/models/models0.ts", "--human").stdout
        self.assertEqual(2, human.count("Impact of changing "))
        self.assertNotIn("(graph ", human)

    def test_a_stale_graph_says_how_many_files_it_misses(self) -> None:
        """/* REQ-CLI-040: with WINGBOX_AUTO_REFRESH=0 and one file changed since the build, the graph fact of
        `wingbox impact a b` says `:stale 1`; known_stale gives 0 for a fresh graph, the number of changed files
        for a stale one and nothing for a graph that was not built from this folder. */"""
        changed = self.root / "src" / "routes" / "routes3.ts"
        original = changed.read_text(encoding="utf-8")
        try:
            changed.write_text(original + "export const more = 1;\n", encoding="utf-8")
            done = self.wingbox("impact", "src/routes/routes0.ts", "src/models/models0.ts", WINGBOX_AUTO_REFRESH="0")
            self.assertRegex(done.stdout.splitlines()[0], r"^\(graph :built \S+ :files 14 :stale 1\)$")
        finally:
            changed.write_text(original, encoding="utf-8")
        self.assertEqual(0, freshness.known_stale(freshness.Fresh(False)))
        self.assertEqual(3, freshness.known_stale(freshness.Stale("auto-refresh-off", 3)))
        self.assertIsNone(freshness.known_stale(freshness.Unconfirmed("moved")))
        self.assertIsNone(freshness.known_stale(freshness.Unmanaged()))

    def test_the_architecture_answers_have_the_three_forms(self) -> None:
        """/* REQ-CLI-041: `advice`, `align`, `place` and `compare` print their facts by default, the text for people
        with --human, and with --json one object {"facts": [...]} made of the same facts; two form options at
        once are an error (exit status 2). */"""
        for argv, head in ((("advice",), "(advice"), (("align",), "(align-part "), (("compare",), "(compare-part "),
                           (("place", "a new route for users"), "(place ")):
            with self.subTest(argv=argv):
                default = self.wingbox(*argv)
                self.assertEqual(0, default.returncode, default.stderr)
                self.assertTrue(default.stdout.startswith(head), default.stdout[:200])
                human = self.wingbox(*argv, "--human").stdout
                self.assertFalse(human.startswith("("), human[:200])
                facts = json.loads(self.wingbox(*argv, "--json").stdout)["facts"]
                self.assertEqual([line.split()[0].strip("()") for line in default.stdout.splitlines()], [fact["fact"] for fact in facts])
                self.assertEqual(2, self.wingbox(*argv, "--human", "--json").returncode)
                self.assertEqual(2, self.wingbox(*argv, "--sexp", "--json").returncode)


class CompatibilityTest(BuiltProjectTest):
    def test_what_scripts_written_for_0_13_read_is_printed_as_before(self) -> None:
        """/* REQ-CLI-042: the calls a script written for version 0.13 makes print, byte for byte and with the same
        exit status, what 0.13.0 printed (kept in tests/data/outputs_0_13_0.json): `impact <file> <hops> --sexp
        --all` (also for a file that is not in the graph: its error line and exit status 0), `usages <target>
        --sexp --all`, `module <id>`, `boundaries`, `boundaries --strict`, and `advice`, `align`, `compare` and
        `place` with --sexp. The calls without a form option that printed text for people (`impact`, `usages`,
        `advice`, `align`, `compare`, `place`) print that text with --human. An output that holds absolute paths is
        compared where paths are written with /. */"""
        for argv in CLI_CASES:
            text_before = argv[0] in CLI_TEXT_COMMANDS and "--sexp" not in argv
            if os.sep != "/" and ROOT_MARK in str(KEPT[case_key("cli", argv)].get("output", "")):
                continue
            with self.subTest(argv=argv):
                self.assertEqual(KEPT[case_key("cli", argv)], kept(*self.project.run(*argv, *(["--human"] if text_before else []))))

    def test_the_item_lines_of_a_module_are_the_default_answer(self) -> None:
        """/* REQ-CLI-043: `wingbox module <id>` without an option prints one `(fn|cls|enm|typ|ifc|var|mod <module>
        <name> <line> ...)` line per item of the module, as the pull request check reads them; --human and --sexp
        print the same lines and --json the same facts as objects. */"""
        status, output = self.project.run("module", "src/models/models0")
        self.assertEqual(0, status)
        self.assertIn("(fn src/models/models0 models0 2 :lang ts)\n", output)
        self.assertRegex(output, r"(?m)^\((fn|cls|enm|typ|ifc|var|mod) \S+ \S+ \d+ ")
        self.assertEqual(output, self.project.run("module", "src/models/models0", "--human")[1])
        self.assertEqual(output, self.project.run("module", "src/models/models0", "--sexp")[1])
        facts = json.loads(self.project.run("module", "src/models/models0", "--json")[1])["facts"]
        self.assertIn({"fact": "fn", "args": ["src/models/models0", "models0", 2], "lang": "ts"}, facts)


class McpImpactTest(BuiltProjectTest):
    def test_the_impact_tool_takes_a_list_and_returns_the_default_form(self) -> None:
        """/* REQ-MCP-033: the MCP `impact` tool takes `target` (one file) or `targets` (a list); it returns the
        default form of `wingbox impact`, complete: one form per file in the order given after the graph fact (each
        followed by the advice that concerns the file), the error form in place of a file that is not in the
        graph; with neither argument the answer is an error. `all` is still accepted and changes nothing. */"""
        server = mcp_server.Server(str(self.root))
        tool = mcp_server.TOOLS_BY_NAME["impact"]
        self.assertEqual([], mcp_server.argument_errors(tool.input_schema(), {"targets": ["a.ts", "b.ts"], "depth": 1}))
        self.assertEqual([], mcp_server.argument_errors(tool.input_schema(), {"target": "a.ts", "all": True}))
        self.assertEqual(1, len(mcp_server.argument_errors(tool.input_schema(), {"targets": "a.ts"})))
        self.assertEqual(["a.ts", "b.ts"], mcp_server.impact_targets({"target": "a.ts", "targets": ["b.ts", "a.ts"]}))
        several = tool.run(server, {"targets": ["src/routes/routes0.ts", "src/nowhere.ts", "src/models/models0.ts"]})
        self.assertFalse(several.is_error, several.texts)
        heads = [line.split(" :")[0] for line in several.texts[0].splitlines() if line.startswith(("(graph", "(impact", "(error"))]
        self.assertEqual(
            ["(graph", "(impact src/routes/routes0.ts", "(error no-match src/nowhere.ts", "(impact src/models/models0.ts"], heads)
        self.assertIn(" :stale 0)", several.texts[0].splitlines()[0])
        one = tool.run(server, {"target": "src/routes/routes0.ts", "depth": 1})
        self.assertTrue(one.texts[0].startswith("(impact src/routes/routes0.ts :direct 1 :total 1\n (1 src/models/models0.ts)\n"
                                                " (read-first src/routes/routes0.ts src/models/models0.ts))"), one.texts[0])
        self.assertEqual(one.texts, tool.run(server, {"target": "src/routes/routes0.ts", "depth": 1, "all": True}).texts)
        self.assertTrue(tool.run(server, {"target": "src/nowhere.ts"}).is_error)
        neither = tool.run(server, {"depth": 2})
        self.assertTrue(neither.is_error)
        self.assertIn("target", neither.texts[0])


class BenchmarkReaderTest(BuiltProjectTest):
    def test_the_benchmark_scripts_name_the_form_they_read(self) -> None:
        """/* REQ-BENCH-020: every call of a Wingbox query command in the benchmark scripts names the form it reads:
        --sexp for the flat facts, and for the text a published size was measured on the option text_form gives
        for the Wingbox under test (`--human` when its help names `--human|--json`, nothing for an earlier
        release, whose default is that text). So a run parses and measures what it did before the default
        changed, with a pinned release as with this checkout: wingbox_files of benchmarks/impact and
        benchmarks/php finds the dependent files in `impact <file> 2 --all --human`, and the patterns of
        benchmarks/usages find the use sites in `usages <target> --sexp --all`. */"""
        for script in BENCHMARK_READERS:
            text = (REPO / script).read_text(encoding="utf-8")
            calls = [line.strip() for line in text.splitlines() if _QUERY_CALL.search(line)]
            self.assertTrue(calls, script)
            self.assertIn('return ["--human"] if "--human|--json" in help_text else []', text, script)
            for call in calls:
                with self.subTest(script=script, call=call):
                    self.assertRegex(call, r'"--sexp"|\*text_option|\*TEXT_OPTION|\*LIMITED')
        help_now = self.wingbox("help").stdout
        help_0_13 = "wingbox learn [PART] [--agent|--human] [--sexp] [--again] [--yes]\nwingbox <command> [arguments]"
        text = self.wingbox("impact", "src/routes/routes0.ts", "2", "--all", "--human").stdout
        for script in ("benchmarks/impact/impact100.py", "benchmarks/php/tools/verify.py"):
            with self.subTest(script=script):
                reader = load_script(REPO / script)
                self.assertEqual((["--human"], []), (reader.text_form(help_now), reader.text_form(help_0_13)))
                self.assertEqual({"src/models/models0.ts", "src/services/services0.ts"}, reader.wingbox_files(text))
        usages = load_script(REPO / "benchmarks" / "usages" / "usages.py")
        self.assertEqual((["--human"], []), (usages.text_form(help_now), usages.text_form(help_0_13)))
        flat = self.wingbox("usages", "src/routes/routes0::routes0", "--sexp", "--all").stdout.splitlines()
        self.assertTrue(flat[0].startswith("(usages-target "))
        sites = [(found.group(5).strip('"'), int(found.group(2)), int(found.group(3))) for found in map(usages.WB_USAGE.match, flat) if found]
        self.assertEqual([("src/models/models0.ts", 1, 10), ("src/models/models0.ts", 2, 24)], sites)


class SkillTextTest(unittest.TestCase):
    def test_the_skill_and_the_block_say_how_a_script_reads_an_answer(self) -> None:
        """/* REQ-INSTALL-045: SKILL.md has a short section for use from code with the one call a script needs
        (`wingbox impact <files> 2 --json`) and how to read it, and says that each row carries the reason it is
        listed; the always-on block says the same in one line. Neither gives a benchmark number or tells the
        agent not to search, and the skill names `--sexp` only for the commands that talk to a person (init,
        learn and the setup commands) and where it says what the option still prints. */"""
        skill = (REPO / "SKILL.md").read_text(encoding="utf-8")
        section = skill[skill.index("### Use from code"):].split("\n## ")[0]
        self.assertLess(len(section.splitlines()), 20)
        self.assertIn('["wingbox", "impact", *files, "2", "--json"]', section)
        self.assertIn('answer["impact"]', section)
        self.assertIn("Each row carries\nthe reason it is listed", section)
        block = install.ALWAYS_ON_BLOCK
        self.assertIn("`wingbox impact <file> [<file> ...] 2 --json`", block)
        self.assertIn("Each row carries the reason it is listed.", block)
        for text in (section, block):
            self.assertIsNone(re.search(r"\d+ ?%|\btokens?\b|do not (search|grep)|never (search|grep)|instead of (grep|search)", text, re.IGNORECASE), text)
        for line in (line for line in skill.splitlines() if "--sexp" in line):
            self.assertRegex(line, r"wingbox (init|learn)|Each of these prints facts as text|flat facts", line)


if __name__ == "__main__":
    unittest.main()
