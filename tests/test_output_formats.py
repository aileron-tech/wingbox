"""The forms of an answer: S-expressions by default, text for people with --human, JSON with --json, and the
flat facts of version 0.13 with --sexp."""
from __future__ import annotations

import json
import os
import textwrap
import unittest

from scripts import fact_json, impact_forms
from tests.output_trees import (
    HUMAN_CASES, KEPT_FILE, SEXP_CASES, TREES, WIDE_SERVICES, BuiltTrees, case_key, kept, names_the_folder, read_forms,
)

KEPT = json.loads(KEPT_FILE.read_text(encoding="utf-8"))
REASON_HEADS = (":use", ":reexport", ":glob", ":member", ":macro")
SOURCE_SUFFIXES = (".rs", ".ts", ".py")


def default_rows(form: list) -> list[tuple]:
    """The rows of one `(impact ...)` form: (file, depth, via, through, more, reason lists)."""
    rows: list[tuple] = []
    for group in (item for item in form if isinstance(item, list) and item[0].isdigit()):
        depth = int(group[0])
        blocks = [(None, group[1:])] if depth == 1 else [(block[1], block[2:]) for block in group[1:]]
        for through, items in blocks:
            index = 0
            while index < len(items):
                file, via, more, reasons = items[index], "imports", 0, []
                index += 1
                while index < len(items) and (isinstance(items[index], list) or items[index].startswith(":")):
                    if isinstance(items[index], list):
                        reasons.append(items[index])
                        index += 1
                    elif items[index] == ":via":
                        via, index = items[index + 1], index + 2
                    else:
                        more, index = int(items[index + 1]), index + 2
                rows.append((file, depth, via, through, more, reasons))
    return rows


def json_rows(answer: dict) -> list[tuple]:
    rows = []
    for row in answer["rows"]:
        reasons = []
        for reason in row["reasons"]:
            listed = [":" + reason["way"]] + ([reason["through"]] if reason["through"] else []) + reason["names"]
            reasons.append(listed + ([":of", str(reason["of"])] if reason["of"] > len(reason["names"]) else []))
        rows.append((row["file"], row["depth"], row["via"], row["through"], row["more"], reasons))
    return rows


def usage_rows(form: list) -> list[tuple[str, str, str]]:
    """The uses of one `(usages ...)` form: (file, kind, line:column)."""
    rows = []
    for group in (item for item in form if isinstance(item, list)):
        kind = ""
        for item in group[1:]:
            if item[0].isdigit():
                rows.append((group[0], kind, item))
            else:
                kind = item
    return rows


def text_files(text: str) -> list[str]:
    """The files of the Depth blocks of an impact text."""
    files, inside = [], False
    for line in text.splitlines():
        if line.startswith("Depth "):
            inside = True
        elif not line.startswith("  ") or line.startswith("  ..."):
            inside = False
        elif inside:
            files.append(line.split()[0])
    return files


_BUILT: list[BuiltTrees] = []


def tearDownModule() -> None:
    while _BUILT:
        _BUILT.pop().close()


class OutputFormTest(unittest.TestCase):
    built: BuiltTrees

    @classmethod
    def setUpClass(cls) -> None:
        # The trees are built once for all the classes of this file.
        if not _BUILT:
            _BUILT.append(BuiltTrees())
        cls.built = _BUILT[0]

    def run_ok(self, tree: str, *argv: str) -> str:
        status, output = self.built.run(tree, list(argv))
        self.assertEqual(0, status, output)
        return output


class ImpactDefaultFormTest(OutputFormTest):
    def test_a_plain_import_is_a_bare_path(self) -> None:
        """/* REQ-CS-104: `impact <file>` prints by default one `(impact <file> :direct N :total M ...)` form: file
        paths with their extensions, never module IDs, the asked file named once; a file that only imports
        the previous one is a bare path, and nothing says `:lang` or `:via imports`. */"""
        self.assertEqual(
            textwrap.dedent(
                """\
                (impact app/models.py :direct 2 :total 2
                 (1 app/__init__.py
                    app/billing.py)
                 (read-first app/models.py app/__init__.py app/billing.py))
                """
            ),
            self.run_ok("python", "impact", "app/models.py"),
        )
        self.assertEqual(
            "(impact src/api/handler.ts :direct 0 :total 0\n (read-first src/api/handler.ts))\n",
            self.run_ok("ts", "impact", "src/api/handler.ts"),
        )

    def test_a_deeper_row_stands_under_the_file_it_is_reached_through(self) -> None:
        """/* REQ-CS-105: rows are grouped by depth and, below depth 1, by the file of the previous depth they are
        reached through, which is named once per group as `(:through <file> ...)`; `:more N` follows a file that
        depends on N other files of that depth as well. */"""
        self.assertEqual(
            textwrap.dedent(
                """\
                (impact src/core/util.ts :direct 2 :total 3
                 (1 src/billing/invoice.ts
                    src/billing/report.ts)
                 (2
                  (:through src/billing/invoice.ts
                   src/api/handler.ts :more 1))
                 (read-first src/core/util.ts src/billing/invoice.ts src/billing/report.ts))
                """
            ),
            self.run_ok("ts", "impact", "src/core/util.ts"),
        )
        self.assertEqual(
            textwrap.dedent(
                """\
                (impact src/native/scripts.rs :direct 2 :total 5
                 (1 src/native/mod.rs
                    src/native/actions.rs (:use other script))
                 (2
                  (:through src/native/mod.rs
                   src/main.rs)
                  (:through src/native/actions.rs
                   src/native/flow/mod.rs (:use HarEntry)
                   src/native/flow/replay.rs (:glob "super" HarEntry)))
                 (read-first src/native/scripts.rs src/native/mod.rs src/native/actions.rs))
                """
            ),
            self.run_ok("rust", "impact", "src/native/scripts.rs"),
        )

    def test_a_path_is_followed_by_the_reason_lists_of_its_edge(self) -> None:
        """/* REQ-CS-106: a path is followed by the name lists of its edge exactly as the graph carries them:
        `(:use A B)`, `(:use A B C :of 5)` when there are more names, `(:reexport "<facade>" A)`,
        `(:glob "<module>" A)`, `(:member "<Type>" a b)`, `(:macro a)`; a relation that is not an import is
        said as `:via <relation>`. */"""
        running = self.run_ok("rust", "impact", "src/settings/running.rs")
        seed = self.run_ok("rust", "impact", "src/settings/seed.rs", "1")
        many = self.run_ok("rust", "impact", "src/many.rs", "1")
        self.assertIn(' src/settings/mod.rs (:use Running)\n', running)
        self.assertIn('    src/store.rs (:reexport "crate::settings" Running))\n', running)
        self.assertIn('    src/store.rs (:member "Running" from_env from_lookup))\n', seed)
        self.assertIn("    src/settings/wide.rs (:use A B C :of 5))\n", many)
        self.assertIn('   src/native/flow/replay.rs (:glob "super" HarEntry)', self.run_ok("rust", "impact", "src/native/scripts.rs"))
        edges = {line for line in (self.built.roots["rust"] / ".wingbox" / "index.sexp").read_text(encoding="utf-8").splitlines()
                 if line.startswith("(imports ")}
        for reason in ('(:reexport "crate::settings" Running)', '(:member "Running" from_env from_lookup)', "(:use A B C :of 5)"):
            self.assertTrue(any(reason in edge for edge in edges), reason)
        line = impact_forms.ImpactLine("src/b.rs", "refs", "", 0, ("(:macro emit)",), "refs")
        found = impact_forms.ImpactFound("src/a.rs", 2, ((line,),), ("src/a.rs", "src/b.rs"))
        self.assertEqual(
            "(impact src/a.rs :direct 1 :total 1\n (1 src/b.rs :via refs (:macro emit))\n (read-first src/a.rs src/b.rs))\n",
            impact_forms.found_sexp(found),
        )
        self.assertEqual(
            [{"way": "macro", "through": "", "names": ["emit"], "of": 1}],
            impact_forms.answer_json(found)["rows"][0]["reasons"],
        )

    def test_read_first_is_one_form_and_the_counts_are_those_of_the_text(self) -> None:
        """/* REQ-CS-107: the files to read first are one `(read-first <file> ...)` form inside the answer; the
        counts, the files of every depth and the read-first files of the default form are those of the text
        form, for every file of the fixture trees. */"""
        for tree in ("rust", "ts", "python"):
            for file in (name for name in TREES[tree] if name.endswith(SOURCE_SUFFIXES)):
                with self.subTest(tree=tree, file=file):
                    form = read_forms(self.run_ok(tree, "impact", file))[0]
                    text = self.run_ok(tree, "impact", file, "--human", "--all")
                    rows = default_rows(form)
                    self.assertEqual(sorted(text_files(text)), sorted(row[0] for row in rows))
                    self.assertIn(f"  direct dependents   {form[form.index(':direct') + 1]}\n", text)
                    self.assertIn(f"  within 2 hops       {form[form.index(':total') + 1]}\n", text)
                    self.assertEqual(len(rows), int(form[form.index(":total") + 1]))
                    read_first = [item for item in form if isinstance(item, list) and item[0] == "read-first"]
                    self.assertEqual(1, len(read_first))
                    if rows:
                        listed = text[text.index("Read first"):].split("\n\n")[0].splitlines()[1:]
                        self.assertEqual([line.strip() for line in listed], read_first[0][1:])


class ImpactSeveralFilesTest(OutputFormTest):
    def test_one_form_per_file_and_an_unknown_file_does_not_lose_the_others(self) -> None:
        """/* REQ-CS-108: `impact a b c 2` (a last argument that is a whole number is the depth) prints one form
        per file in the order given; a file that is not in the graph gives `(error no-match <file> ...)` in its
        place, the other answers are printed all the same, and the exit status is 1; with every file in the
        graph it is 0. The same holds for --human and --json. */"""
        status, output = self.built.run("ts", ["impact", "src/core/util.ts", "src/nowhere.ts", "src/api/handler.ts", "1"])
        forms = read_forms(output)
        self.assertEqual(1, status)
        self.assertEqual(["graph", "impact", "error", "impact"], [form[0] for form in forms])
        self.assertEqual(["impact", "src/core/util.ts", ":direct", "2", ":total", "2"], forms[1][:6])
        self.assertEqual(["error", "no-match", "src/nowhere.ts"], forms[2][:3])
        self.assertEqual(["impact", "src/api/handler.ts", ":direct", "0", ":total", "0"], forms[3][:6])
        self.assertEqual(0, self.built.run("ts", ["impact", "src/core/util.ts", "src/api/handler.ts"])[0])
        status, text = self.built.run("ts", ["impact", "src/core/util.ts", "src/nowhere.ts", "src/api/handler.ts", "1", "--human"])
        self.assertEqual(1, status)
        blocks = text.split("\n\n(error no-match src/nowhere")
        self.assertEqual(2, len(blocks), text)
        self.assertEqual(self.run_ok("ts", "impact", "src/core/util.ts", "1", "--human"), blocks[0] + "\n")
        self.assertTrue(text.endswith("\n\n" + self.run_ok("ts", "impact", "src/api/handler.ts", "1", "--human")), text)
        status, document = self.built.run("ts", ["impact", "src/core/util.ts", "src/nowhere.ts", "src/api/handler.ts", "1", "--json"])
        answers = json.loads(document)["impact"]
        self.assertEqual(1, status)
        self.assertEqual(["src/core/util.ts", "src/nowhere.ts", "src/api/handler.ts"], [answer["file"] for answer in answers])
        self.assertEqual("no-match", answers[1]["error"])
        self.assertEqual([2, 0], [answers[0]["total"], answers[2]["total"]])

    def test_a_single_unknown_file_and_a_single_number(self) -> None:
        """/* REQ-CS-109: one asked file that is not in the graph gives its error form and exit status 1; a single
        argument is a file even when it is a number, and a last number outside the depth range is an invalid
        argument (exit status 2), as before. */"""
        status, output = self.built.run("rust", ["impact", "src/nowhere.rs"])
        self.assertEqual(1, status)
        self.assertTrue(output.startswith("(error no-match src/nowhere.rs :suggestions src/main.rs "), output)
        self.assertEqual((1, "(error no-match 2)\n"), self.built.run("rust", ["impact", "2"]))
        status, output = self.built.run("rust", ["impact", "src/main.rs", "999"])
        self.assertEqual(2, status)
        self.assertTrue(output.startswith("(error invalid-argument "), output)

    def test_a_graph_fact_opens_the_answer_for_several_files(self) -> None:
        """/* REQ-CS-110: the answer for several files opens with one `(graph :built <UTC time> :files <n>
        :stale <n>)` fact: when the graph file was written, how many files it holds and how many changed files
        it is known to miss (`unknown` when nothing checked it, as with --map); an answer for one file has no
        such line, and --json always carries the same three values under "graph". */"""
        output = self.run_ok("ts", "impact", "src/core/util.ts", "src/api/handler.ts")
        self.assertRegex(output.splitlines()[0], r"^\(graph :built \d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ :files 4 :stale unknown\)$")
        self.assertNotIn("(graph ", self.run_ok("ts", "impact", "src/core/util.ts"))
        for argv in (("src/core/util.ts",), ("src/core/util.ts", "src/api/handler.ts")):
            graph = json.loads(self.run_ok("ts", "impact", *argv, "--json"))["graph"]
            self.assertEqual(["built", "files", "stale"], list(graph))
            self.assertEqual((4, None), (graph["files"], graph["stale"]))
        header = impact_forms.GraphHeader("2026-01-31T09:15:00Z", 12, 3)
        self.assertEqual("(graph :built 2026-01-31T09:15:00Z :files 12 :stale 3)", impact_forms.header_sexp(header))
        self.assertEqual({"built": "2026-01-31T09:15:00Z", "files": 12, "stale": 3}, impact_forms.header_json(header))


class JsonFormTest(OutputFormTest):
    def test_impact_json_says_what_the_default_form_says(self) -> None:
        """/* REQ-CS-111: `impact ... --json` prints one JSON object {"graph", "impact": [one object per asked
        file]}; each has file, direct, total, rows, read_first and advice, and every row has file, depth, via,
        through, more and reasons. Its files, depths, relations, through files, counts and name lists are
        those of the default form, for every file of the fixture trees and for a call with several files. */"""
        calls = [(tree, (file,)) for tree in ("rust", "ts", "python") for file in TREES[tree] if file.endswith(SOURCE_SUFFIXES)]
        calls += [("wide", ("src/core/hub.ts",)), ("rust", ("src/native/scripts.rs", "src/many.rs", "src/settings/running.rs", "3"))]
        for tree, argv in calls:
            with self.subTest(tree=tree, argv=argv):
                forms = [form for form in read_forms(self.run_ok(tree, "impact", *argv)) if form[0] == "impact"]
                answers = json.loads(self.run_ok(tree, "impact", *argv, "--json"))["impact"]
                self.assertEqual(len(forms), len(answers))
                for form, answer in zip(forms, answers):
                    self.assertEqual(["file", "direct", "total", "rows", "read_first", "advice"], list(answer))
                    self.assertEqual(form[1:6], [answer["file"], ":direct", str(answer["direct"]), ":total", str(answer["total"])])
                    self.assertEqual(default_rows(form), json_rows(answer))
                    self.assertEqual(next(item for item in form if isinstance(item, list) and item[0] == "read-first")[1:], answer["read_first"])
                    for row in answer["rows"]:
                        self.assertEqual(["file", "depth", "via", "through", "more", "reasons"], list(row))

    def test_usages_json_says_what_the_default_form_says(self) -> None:
        """/* REQ-CS-112: `usages ... --json` prints {"usages": {symbol, kind, file, line, uses, files, unresolved,
        rows}} with one row {file, line, column, kind} per use; the rows are those of the default form. */"""
        for tree, symbol in (("python", "app/models.py::Invoice"), ("python", "app/models.py::Invoice.total"),
                             ("rust", "src/native/actions.rs::HarEntry"), ("wide", "src/core/hub.ts::clamp")):
            with self.subTest(symbol=symbol):
                form = read_forms(self.run_ok(tree, "usages", symbol))[0]
                answer = json.loads(self.run_ok(tree, "usages", symbol, "--json"))["usages"]
                self.assertEqual(["symbol", "kind", "file", "line", "uses", "files", "unresolved", "rows"], list(answer))
                from_form = usage_rows(form)
                from_json = [(row["file"], row["kind"], f"{row['line']}:{row['column']}") for row in answer["rows"]]
                self.assertEqual(sorted(from_form), sorted(from_json))
                self.assertEqual(form[1:4], [answer["symbol"], answer["kind"], ":at"])
                self.assertEqual(form[4], f"{answer['file']}:{answer['line']}")
                self.assertEqual(form[form.index(":uses") + 1], str(answer["uses"]))
                self.assertEqual(form[form.index(":files") + 1], str(answer["files"]))

    def test_every_other_command_prints_its_facts_as_json_objects(self) -> None:
        """/* REQ-CS-113: every query command takes --json and prints one JSON object; for the commands whose
        answer is a list of facts it is {"facts": [...]}, one object per line of the default form in the same
        order, made by the rules of REQ-CS-114. */"""
        for tree, argv in HUMAN_CASES:
            if "--all" in argv or (tree, argv[:2]) == ("wide", ("analyze", "src/core/hub")):
                continue
            with self.subTest(tree=tree, argv=argv):
                _status, default = self.built.run_raw(tree, list(argv))
                _status, document = self.built.run_raw(tree, [*argv, "--json"])
                parsed = json.loads(document)
                self.assertEqual(1, len(document.splitlines()))
                if argv[0] in ("impact", "usages"):
                    self.assertIn(argv[0], parsed)
                else:
                    self.assertEqual({"facts": fact_json.facts_json(default)}, parsed)
        facts = json.loads(self.run_ok("rust", "deps", "src/native/actions", "2", "--json"))["facts"]
        self.assertEqual({"fact": "deps-root", "args": ["src/native/actions"], "lang": "rs"}, facts[0])
        self.assertEqual({"fact": "dep", "args": ["src/native/actions", "src/native/scripts"], "lang": "rs", "via": "imports", "depth": 1}, facts[1])

    def test_a_fact_line_becomes_an_object_with_stable_keys(self) -> None:
        """/* REQ-CS-114: a fact line `(head a b :key value :flag (:tag x y))` becomes {"fact": "head", "args": [a, b],
        "key": value, "flag": true, "tag": [x, y]}: atoms outside keywords are args, also after a keyword's value;
        a keyword takes one value or is true; `:suggestions`, `:others` and the `:via` of analyze-dependent take
        every atom up to the next keyword and are always lists; unquoted digits are numbers, quoted ones text;
        the lists of names of an import are collected under "names"; a path holding brackets stays one atom;
        a comment line is {"note": ...} and any other line {"line": ...}. */"""
        cases = {
            '(node app/a :lang py "general module" :file "/x y/a.py")':
                {"fact": "node", "args": ["app/a", "general module"], "lang": "py", "file": "/x y/a.py"},
            "(dep a b :lang rs :via imports :depth 2 :cycle)":
                {"fact": "dep", "args": ["a", "b"], "lang": "rs", "via": "imports", "depth": 2, "cycle": True},
            "(error no-match app/x :suggestions app/a app/b)":
                {"fact": "error", "args": ["no-match", "app/x"], "suggestions": ["app/a", "app/b"]},
            "(error no-match app/x :suggestions app/a)":
                {"fact": "error", "args": ["no-match", "app/x"], "suggestions": ["app/a"]},
            "(analyze-dependent a b :lang ts :via imports refs :file b.ts)":
                {"fact": "analyze-dependent", "args": ["a", "b"], "lang": "ts", "via": ["imports", "refs"], "file": "b.ts"},
            "(cls app/a Invoice 4 :lang py (:bases Base) (:methods total pay))":
                {"fact": "cls", "args": ["app/a", "Invoice", 4], "lang": "py", "bases": ["Base"], "methods": ["total", "pay"]},
            '(imports a b :candidate :item (:glob "super" HarEntry) (:use A B C :of 5))':
                {"fact": "imports", "args": ["a", "b"], "candidate": True, "item": True, "names": [
                    {"way": "glob", "through": "super", "names": ["HarEntry"], "of": 1},
                    {"way": "use", "through": "", "names": ["A", "B", "C"], "of": 5}]},
            "(layer-member feature docs/app/(docs)/[[...slug]]/page :lang tsx)":
                {"fact": "layer-member", "args": ["feature", "docs/app/(docs)/[[...slug]]/page"], "lang": "tsx"},
            '(grep-fn a::f :line 3 :match "return (1); // \\"x\\"")':
                {"fact": "grep-fn", "args": ["a::f"], "line": 3, "match": 'return (1); // "x"'},
            '(key "12" 12 :id "007")': {"fact": "key", "args": ["12", 12], "id": "007"},
            "(caller a::f -> b::g :lang py :file b.py :type-only)":
                {"fact": "caller", "args": ["a::f", "->", "b::g"], "lang": "py", "file": "b.py", "type-only": True},
            "; no calls edges found": {"note": "no calls edges found"},
            "/tmp/project/src/store.rs": {"line": "/tmp/project/src/store.rs"},
        }
        for line, expected in cases.items():
            with self.subTest(line=line):
                self.assertEqual(expected, fact_json.fact_json(line))
        self.assertEqual([], fact_json.facts_json("\n\n"))


class HumanFormTest(OutputFormTest):
    def test_human_prints_what_version_0_13_0_printed(self) -> None:
        """/* REQ-CS-115: with --human every query command prints, byte for byte, what version 0.13.0 printed for
        the same arguments without a form option: the text of impact and usages (with their limits and --all),
        and the fact lines of the other commands, `boundaries --strict` with its exit status among them. Kept in
        tests/data/outputs_0_13_0.json: the output of the 0.13.0 scripts on the fixture trees, long outputs as
        length and SHA-256. The exit status is the same but for `impact` of a file that is not in the graph. */"""
        for tree, argv in HUMAN_CASES:
            if os.sep != "/" and names_the_folder(argv):
                continue
            with self.subTest(tree=tree, argv=argv):
                status, output = self.built.run(tree, [*argv, "--human"])
                if argv[0] == "impact" and "nowhere" in argv[1]:
                    self.assertEqual(1, status)   # REQ-CS-109: the exit status now says the file is unknown
                    status = 0
                self.assertEqual(KEPT[case_key(tree, argv)], kept(status, output))

    def test_the_default_form_of_the_fact_commands_keeps_its_shape(self) -> None:
        """/* REQ-CS-116: the commands that printed fact lines before (analyze, quick, module, boundary, deps, rdeps,
        entity, flow, search, layers, boundaries, invariants, find_function, find_type, find_callers, find_calls,
        find_callchain, grep_functions, query) print the same lines by default, with the same exit status
        (`boundaries --strict` among them), without the limits of --human. */"""
        for tree, argv in HUMAN_CASES:
            if argv[0] in ("impact", "usages") or tree == "wide" or (os.sep != "/" and names_the_folder(argv)):
                continue
            with self.subTest(tree=tree, argv=argv):
                self.assertEqual(KEPT[case_key(tree, argv)], kept(*self.built.run(tree, list(argv))))


class CompleteAndCompactTest(OutputFormTest):
    def test_only_the_text_for_people_is_cut(self) -> None:
        """/* REQ-CS-117: the default form and --json are complete: `impact`, `usages` and `analyze` list every
        dependent and every use of a hub with more than 200 of them and carry no `...-more` fact, no `:hidden` and no `:rerun`;
        --all changes nothing there. --human stops at its limits and says how to see the rest, and
        --human --all prints everything. */"""
        impact = self.run_ok("wide", "impact", "src/core/hub.ts")
        usages = self.run_ok("wide", "usages", "src/core/hub.ts::clamp")
        analyze = self.run_ok("wide", "analyze", "src/core/hub")
        self.assertEqual(2 * WIDE_SERVICES + 1, len(default_rows(read_forms(impact)[0])))
        self.assertEqual(3 * WIDE_SERVICES, len(usage_rows(read_forms(usages)[0])))
        self.assertEqual(WIDE_SERVICES, analyze.count("(analyze-dependent "))
        for output in (impact, usages, analyze):
            for word in ("-more ", ":hidden", ":rerun", "... "):
                self.assertNotIn(word, output[:200] if word in output else output)
        self.assertEqual(impact, self.run_ok("wide", "impact", "src/core/hub.ts", "--all"))
        self.assertEqual(usages, self.run_ok("wide", "usages", "src/core/hub.ts::clamp", "--all"))
        self.assertEqual(analyze, self.run_ok("wide", "analyze", "src/core/hub", "--all"))
        self.assertEqual(len(json.loads(self.run_ok("wide", "impact", "src/core/hub.ts", "--json"))["impact"][0]["rows"]), 2 * WIDE_SERVICES + 1)
        self.assertEqual(len(json.loads(self.run_ok("wide", "usages", "src/core/hub.ts::clamp", "--json"))["usages"]["rows"]), 3 * WIDE_SERVICES)
        self.assertIn(f"  ... {2 * WIDE_SERVICES + 1 - 200} more files: rerun with --all\n", self.run_ok("wide", "impact", "src/core/hub.ts", "--human"))
        self.assertIn("more uses: rerun with --all\n", self.run_ok("wide", "usages", "src/core/hub.ts::clamp", "--human"))
        self.assertIn("(analyze-more src/core/hub :dependents 5 ", self.run_ok("wide", "analyze", "src/core/hub", "--human"))
        self.assertEqual(2 * WIDE_SERVICES + 1, len(text_files(self.run_ok("wide", "impact", "src/core/hub.ts", "--human", "--all"))))

    def test_the_default_form_is_not_larger_than_the_text(self) -> None:
        """/* REQ-CS-118: for every file of the fixture trees (a Rust crate whose edges carry name lists, TypeScript
        and Python packages, a hub with 205 direct and 206 deeper dependents) the default form of `impact` has
        no more characters than its complete text (--human --all); the same holds for `usages` of their
        symbols. */"""
        for tree, files in TREES.items():
            for file in (name for name in files if name.endswith(SOURCE_SUFFIXES)):
                if tree == "wide" and not file.endswith(("hub.ts", "service007.ts", "handle007.ts", "facade.ts")):
                    continue
                with self.subTest(tree=tree, file=file):
                    default = self.run_ok(tree, "impact", file)
                    text = self.run_ok(tree, "impact", file, "--human", "--all")
                    self.assertLessEqual(len(default), len(text), default)
        symbols = (("python", "app/models.py::Invoice"), ("python", "app/models.py::Invoice.total"), ("python", "Invoice"),
                   ("rust", "src/native/actions.rs::HarEntry"), ("rust", "script"), ("rust", "src/many.rs::A"),
                   ("ts", "src/core/util.ts::limit"), ("wide", "src/core/hub.ts::clamp"), ("wide", "src/core/hub.ts::limit"),
                   ("wide", "src/services/service007.ts::service007"))
        for tree, symbol in symbols:
            with self.subTest(symbol=symbol):
                default = self.run_ok(tree, "usages", symbol)
                text = self.run_ok(tree, "usages", symbol, "--human", "--all")
                self.assertLessEqual(len(default), len(text), default)


class UsagesDefaultFormTest(OutputFormTest):
    def test_the_symbol_and_each_file_are_named_once(self) -> None:
        """/* REQ-CS-119: `usages <symbol>` prints by default one `(usages <symbol> <kind> :at <file>:<line> :uses U
        :files M ...)` form with one `(<file> <kind> <line>:<column> ... <kind> ...)` group per file, the uses
        of a file grouped by kind; `:unresolved N` is said only when there are unresolved calls, and the kind
        of `Class.method` is `method`. Counts and positions are those of the text. */"""
        self.assertEqual(
            textwrap.dedent(
                """\
                (usages Invoice cls :at app/models.py:1 :uses 7 :files 2
                 (app/__init__.py re-export 1:21)
                 (app/billing.py import 1:17 2:24 extends 4:13 type 7:13 7:25 call 8:11))
                """
            ),
            self.run_ok("python", "usages", "app/models.py::Invoice"),
        )
        self.assertEqual(
            textwrap.dedent(
                """\
                (usages Invoice.total method :at app/models.py:1 :uses 1 :files 1 :unresolved 1
                 (app/billing.py call 9:16)
                 (app/other.py unresolved-call 2:14))
                """
            ),
            self.run_ok("python", "usages", "app/models.py::Invoice.total"),
        )

    def test_candidates_are_written_as_they_can_be_asked(self) -> None:
        """/* REQ-CS-120: a bare name with several definitions gives `(usages-ambiguous <Name> :definitions N
        (<file>::<Name> <kind> <line>) ...)` and lists no uses; a symbol that is not defined gives the
        `(error no-match ...)` line in every form. */"""
        self.assertEqual(
            "(usages-ambiguous Invoice :definitions 2\n (app/models.py::Invoice cls 1)\n (legacy/models.py::Invoice cls 1))\n",
            self.run_ok("python", "usages", "Invoice"),
        )
        candidates = json.loads(self.run_ok("python", "usages", "Invoice", "--json"))["usages"]["candidates"]
        self.assertEqual(["app/models.py::Invoice", "legacy/models.py::Invoice"], [candidate["target"] for candidate in candidates])
        self.assertEqual("(error no-match Nowhere)\n", self.run_ok("rust", "usages", "Nowhere"))
        self.assertEqual("no-match", json.loads(self.run_ok("rust", "usages", "Nowhere", "--json"))["usages"]["error"])


class FormatOptionTest(OutputFormTest):
    def test_one_form_at_a_time_and_no_unknown_option(self) -> None:
        """/* REQ-CS-121: two of --human, --json and --sexp together are an invalid argument (exit status 2), for
        every query command; `impact` and `usages` take no option but these three and --all, so any other option
        is an invalid argument too and is never read as a file or a symbol. */"""
        for argv in (["impact", "src/main.rs", "--human", "--json"], ["deps", "src/main", "--json", "--human"],
                     ["impact", "src/main.rs", "--sexp", "--json"], ["usages", "script", "--human", "--sexp"],
                     ["impact", "src/main.rs", "--verbose"], ["usages", "script", "--verbose"]):
            with self.subTest(argv=argv):
                status, output = self.built.run("rust", argv)
                self.assertEqual(2, status)
                self.assertTrue(output.startswith("(error invalid-argument "), output)
        status, output = self.built.run("rust", ["impact", "src/main.rs", "--verbose", "--json"])
        self.assertEqual((2, "error"), (status, json.loads(output)["facts"][0]["fact"]))


class FlatFactsTest(OutputFormTest):
    def test_sexp_prints_the_flat_facts_of_version_0_13_0(self) -> None:
        """/* REQ-CS-122: `--sexp` stays an option of `impact` and `usages` and prints, byte for byte and with the
        same exit status, what version 0.13.0 printed with it: one flat fact per line (`impact-root`,
        `(impact <root> <dependent> :lang .. :via .. :depth N ...)`, `impact-summary`, `read-first`;
        `usages-target`, `usage`, `usages-summary`), module IDs, the list limits unless --all, and exit status 0
        for a file that is not in the graph. Scripts written for 0.13 and earlier (the pull request check) read
        these lines. Kept in tests/data/outputs_0_13_0.json. */"""
        for tree, argv in SEXP_CASES:
            with self.subTest(tree=tree, argv=argv):
                self.assertEqual(KEPT[case_key(tree, argv)], kept(*self.built.run(tree, list(argv))))
        flat = self.run_ok("rust", "impact", "src/native/scripts.rs", "2", "--sexp", "--all")
        self.assertIn("(impact src/native/scripts src/native/actions :lang rs :via imports :depth 1 (:use other script))\n", flat)
        self.assertIn("(impact-summary src/native/scripts :direct 2 :total 5 :depth 2)\n", flat)
        self.assertNotIn("(graph ", flat)

    def test_the_other_commands_take_sexp_and_print_what_they_printed(self) -> None:
        """/* REQ-CS-123: every other query command takes `--sexp` as well and prints with it what it printed in
        version 0.13.0 without a form option (its fact lines, with the list limits of `analyze` unless --all). */"""
        for tree, argv in HUMAN_CASES:
            if argv[0] in ("impact", "usages") or (os.sep != "/" and names_the_folder(argv)):
                continue
            with self.subTest(tree=tree, argv=argv):
                self.assertEqual(KEPT[case_key(tree, argv)], kept(*self.built.run(tree, [*argv, "--sexp"])))


if __name__ == "__main__":
    unittest.main()
