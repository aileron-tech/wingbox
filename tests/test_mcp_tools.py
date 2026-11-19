"""The tool list of the MCP server: fourteen tools with distinct jobs, one naming pattern, descriptions that tell
neighbours apart."""
from __future__ import annotations

import re
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.test_mcp_server import McpSession, _call, _initialize, _make_shop, _request, _texts

TOOLS = ("build_graph", "analyze_impact", "describe_module", "list_files", "read_source", "search_source", "find_definitions", "find_usages",
         "list_dependencies", "trace_calls", "list_layers", "check_boundaries", "list_changes", "query_graph")
# Every name a tool had before: the first eighteen, then the three and the eleven one-word names that followed.
REMOVED = ("analyze", "quick", "boundary", "deps", "rdeps", "entity", "find_function", "find_type", "find_callers", "find_calls",
           "find_callchain", "diff_functions", "build", "query", "module",
           "graph", "impact", "modules", "definitions", "usages", "dependencies", "calls", "layers", "boundaries", "changes", "facts")
# The tools that have no neighbour to send a caller to.
ALONE = ("build_graph",)
# The tools that look one name up, and so say what an unknown name returns.
LOOKUPS = ("analyze_impact", "describe_module", "read_source", "find_definitions", "find_usages", "list_dependencies", "trace_calls")


def _shop_with_more(root: Path) -> None:
    _make_shop(root)
    (root / "shop" / "models.py").write_text("LIMIT = 3\n\n\nclass Order:\n    def amount(self):\n        return LIMIT\n", encoding="utf-8")
    (root / "shop" / "api.py").write_text("from shop.checkout import pay\n\n\ndef post(items):\n    return pay(items)\n", encoding="utf-8")


class ToolListTest(unittest.TestCase):
    def setUp(self) -> None:
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        self.root = Path(tmpdir.name).resolve()

    def test_the_server_offers_fourteen_tools_named_verb_noun(self) -> None:
        """/* REQ-MCP-029: `tools/list` offers exactly fourteen tools (within the 3 to 15 a tool directory grades
        best), in this order: build_graph, analyze_impact, describe_module, list_files, read_source, search_source,
        find_definitions, find_usages, list_dependencies, trace_calls, list_layers, check_boundaries, list_changes,
        query_graph. Every name is a verb and a noun in lowercase snake_case; no name is a prefix of another;
        no name a tool had before is left. */"""
        tools = McpSession(self.root, [_initialize(), _request(2, "tools/list")]).reply(2)["result"]["tools"]
        names = [tool["name"] for tool in tools]
        self.assertEqual(list(TOOLS), names)
        self.assertTrue(3 <= len(names) <= 15)
        for name in names:
            self.assertRegex(name, r"^[a-z]+_[a-z]+$")
            self.assertNotIn(name, REMOVED)
        for first in names:
            for second in names:
                if first != second:
                    self.assertFalse(second.startswith(first), (first, second))

    def test_the_instructions_name_only_tools_that_exist(self) -> None:
        """/* REQ-MCP-030: the instructions the server gives at `initialize` and `server/discover` name tools of
        the current list only: no earlier tool name is left in them as a tool to call, and they name
        analyze_impact, describe_module, find_usages, read_source and build_graph. */"""
        instructions = McpSession(self.root, [_initialize()]).reply(1)["result"]["instructions"]
        named = set(re.findall(r"`([a-z_]+)`", instructions))
        self.assertEqual(set(), named & set(REMOVED))
        self.assertLessEqual({"analyze_impact", "describe_module", "find_usages", "read_source", "build_graph"}, named)
        self.assertLessEqual(named, set(TOOLS))

    def test_every_description_says_what_comes_back_and_when_to_use_a_neighbour(self) -> None:
        """/* REQ-MCP-021: the description of every tool starts with a verb that says what the tool does
        ("Lists ...", "Traces ..."), says when to use the tool ("Use it ..."), names the neighbour to use for
        another question as "use `name`" (build_graph has no neighbour), and names every
        parameter of the tool except `all` in backticks, so that it says more about them than the schema. A tool
        that takes `all` says that a list stops at 200 items; a tool that looks a name up says what an unknown
        name returns. A description is at most 900 characters and sends a caller to existing tools only. */"""
        tools = McpSession(self.root, [_initialize(), _request(2, "tools/list")]).reply(2)["result"]["tools"]
        for tool in tools:
            name, text = tool["name"], tool["description"]
            self.assertLessEqual(len(text), 900, name)
            self.assertRegex(text, r"^[A-Z][a-z]+s ", name)
            self.assertIn("Use it ", text, name)
            if name not in ALONE:
                others = [other for other in TOOLS if other != name and f"use `{other}`" in text]
                self.assertTrue(others, f"{name}: no neighbour named in {text!r}")
            for other in re.findall(r"use `([a-z_]+)`", text):
                self.assertIn(other, TOOLS, name)
            for old in REMOVED:
                self.assertNotIn(f"`{old}`", text.replace("`module`", ""), name)
            properties = tool["inputSchema"]["properties"]
            for parameter in properties:
                if parameter != "all":
                    self.assertIn(f"`{parameter}", text, f"{name}: parameter {parameter} is not explained")
            if "all" in properties:
                self.assertIn("200", text, name)
            if name in LOOKUPS:
                self.assertIn("is answered with an error", text, name)

    def test_facts_lists_its_functions(self) -> None:
        """/* REQ-MCP-031: the expression `(help)`, which the description of `query_graph` names, returns the list of
        the functions of the query language and is not an error. */"""
        _make_shop(self.root)
        session = McpSession(self.root, [_initialize(), _call(2, "build_graph", {}), _call(3, "query_graph", {"expression": "(help)"})])
        reply = session.reply(3)
        self.assertFalse(reply["result"]["isError"], reply)
        text = "\n".join(_texts(reply))
        for function in ("(deps ", "(rdeps ", "(intersect ", "(files "):
            self.assertIn(function, text)

    def test_only_the_new_names_answer(self) -> None:
        """/* REQ-MCP-032: `build_graph` builds the code graph and is the only tool that writes, `describe_module`
        describes a module and `query_graph` evaluates a query; every name a tool had before (`build`, `graph`,
        `impact`, `modules`, `facts`, ...) is an unknown tool. */"""
        _make_shop(self.root)
        old = ("build", "graph", "impact", "modules", "facts", "usages", "module", "query")
        session = McpSession(self.root, [
            _initialize(), _request(2, "tools/list"), _call(3, "build_graph", {}), _call(4, "describe_module", {"module": "shop/checkout"}),
            _call(5, "query_graph", {"expression": '(count (rdeps "shop/pricing" 1))'}),
            *[_call(100 + index, name, {}) for index, name in enumerate(old)],
        ])
        hints = {tool["name"]: tool["annotations"]["readOnlyHint"] for tool in session.reply(2)["result"]["tools"]}
        self.assertEqual(["build_graph"], [name for name, read_only in hints.items() if not read_only])
        self.assertFalse(session.reply(3)["result"]["isError"], session.reply(3))
        self.assertIn("shop/pricing", "\n".join(_texts(session.reply(4))))
        self.assertIn("1", "\n".join(_texts(session.reply(5))))
        for index in range(len(old)):
            self.assertEqual(-32602, session.reply(100 + index)["error"]["code"], old[index])

    def test_files_and_source_are_read_through_the_server(self) -> None:
        """/* REQ-MCP-034: `list_files` lists the source files of the graph, all or those whose path matches
        `pattern`; `read_source` returns the numbered lines of a file of the graph, of a range of it, or of one
        definition. A path outside the repository, a file that is not in the graph and an unknown name are tool
        errors, and no line of any file is returned for them. */"""
        _make_shop(self.root)
        (self.root.parent / "outside_secret.py").write_text("TOKEN = 'outside'\n", encoding="utf-8")
        self.addCleanup((self.root.parent / "outside_secret.py").unlink)
        (self.root / "notes.txt").write_text("not source\n", encoding="utf-8")
        session = McpSession(self.root, [
            _initialize(), _call(2, "build_graph", {}),
            _call(3, "list_files", {}), _call(4, "list_files", {"pattern": "check"}),
            _call(5, "read_source", {"target": "shop/pricing.py"}),
            _call(6, "read_source", {"target": "pay"}),
            _call(7, "read_source", {"target": "shop/checkout.py", "first": 4, "last": 4}),
            _call(8, "read_source", {"target": "../outside_secret.py"}),
            _call(9, "read_source", {"target": str(self.root.parent / "outside_secret.py")}),
            _call(10, "read_source", {"target": "notes.txt"}),
            _call(11, "read_source", {"target": "no_such_name"}),
            _call(12, "read_source", {"target": "shop/pricing.py", "first": 0}),
        ])
        everything = "\n".join(_texts(session.reply(3)))
        for path in ("shop/pricing.py", "shop/checkout.py"):
            self.assertIn(f"(file {path} ", everything)
        self.assertNotIn("notes.txt", everything)
        some = "\n".join(_texts(session.reply(4)))
        self.assertIn("(file shop/checkout.py ", some)
        self.assertNotIn("shop/pricing.py", some)
        whole = "\n".join(_texts(session.reply(5))).splitlines()
        self.assertEqual("(source shop/pricing.py :first 1 :last 2 :of 2)", whole[0])
        self.assertEqual('(line 1 "def total(items):")', whole[1])
        one = "\n".join(_texts(session.reply(6)))
        self.assertIn(":entity shop/checkout::pay)", one)
        self.assertIn('"def pay(items):")', one)
        self.assertEqual(["(source shop/checkout.py :first 4 :last 4 :of 5)", '(line 4 "def pay(items):")'], "\n".join(_texts(session.reply(7))).splitlines())
        for identifier in (8, 9, 10, 11, 12):
            reply = session.reply(identifier)
            self.assertTrue(reply["result"]["isError"], (identifier, reply))
            text = "\n".join(_texts(reply))
            self.assertNotIn("(line ", text, identifier)
            self.assertNotIn("outside", text.replace("outside_secret", ""), identifier)

    def test_text_is_searched_through_the_server(self) -> None:
        """/* REQ-MCP-036: `search_source` returns the lines of the source files of the graph that hold a text,
        each with its file and line number, in all files or in those whose path matches `path`. A text no line
        holds is an empty answer, not an error; a pattern without text is a tool error. A file that is not a
        source file of the graph is never searched. The limits its description names are those of the `grep`
        command. */"""
        from wingbox import cli, mcp_server

        _make_shop(self.root)
        (self.root / "notes.txt").write_text("def total(items): a note, not source\n", encoding="utf-8")
        session = McpSession(self.root, [
            _initialize(), _call(2, "build_graph", {}),
            _call(3, "search_source", {"pattern": "TOTAL(items)"}),
            _call(4, "search_source", {"pattern": "total", "path": "checkout"}),
            _call(5, "search_source", {"pattern": "nowhere_at_all"}),
            _call(6, "search_source", {"pattern": ".*"}),
            _call(7, "search_source", {"pattern": "a note"}),
        ])
        found = "\n".join(_texts(session.reply(3))).splitlines()
        self.assertIn('(match shop/pricing.py 1 "def total(items):")', found)
        self.assertTrue(found[-1].startswith("(matches-summary :matches "), found)
        self.assertNotIn("notes.txt", "\n".join(found))
        narrowed = "\n".join(_texts(session.reply(4))).splitlines()
        self.assertTrue(all(line.startswith("(match shop/checkout.py ") for line in narrowed[:-1]) and len(narrowed) > 1, narrowed)
        self.assertFalse(session.reply(5)["result"]["isError"])
        self.assertTrue("\n".join(_texts(session.reply(5))).startswith("(matches-empty nowhere_at_all "))
        self.assertTrue(session.reply(6)["result"]["isError"])
        self.assertTrue("\n".join(_texts(session.reply(7))).startswith("(matches-empty "))
        search = cli.load_script("code_graph_search")
        description = mcp_server.TOOLS_BY_NAME["search_source"].description
        self.assertEqual((search.GREP_LINE_CHARS, search.GREP_MATCH_LIMIT), (mcp_server.MATCH_LINE_CHARS, mcp_server.MATCHES_PER_CALL))
        self.assertIn(f"longer than {search.GREP_LINE_CHARS} characters", description)
        self.assertIn(f"at most {search.GREP_MATCH_LIMIT:,} matches", description)

    def test_a_malformed_query_is_a_tool_error(self) -> None:
        """/* REQ-MCP-037: `query_graph` answers a malformed expression (a parenthesis that is not closed, a
        function the language does not have) with a tool error whose text says what is wrong, as its
        description says; a selection that holds nothing is an empty result and no error. */"""
        _make_shop(self.root)
        session = McpSession(self.root, [
            _initialize(), _call(2, "build_graph", {}),
            _call(3, "query_graph", {"expression": '(deps "shop/checkout" 1'}),
            _call(4, "query_graph", {"expression": '(nosuch "x")'}),
            _call(5, "query_graph", {"expression": '(deps "no_such_module" 1)'}),
        ])
        for identifier, reason in ((3, "a parenthesis is not closed"), (4, "unknown function: nosuch")):
            reply = session.reply(identifier)
            self.assertTrue(reply["result"]["isError"], reply)
            self.assertIn(reason, "\n".join(_texts(reply)))
        self.assertFalse(session.reply(5)["result"]["isError"], session.reply(5))
        from wingbox import mcp_server

        description = mcp_server.TOOLS_BY_NAME["query_graph"].description
        self.assertIn("malformed expression", description)
        self.assertIn("error line", description)

    def test_the_line_limit_of_read_source_is_the_one_of_the_command(self) -> None:
        """/* REQ-MCP-035: the number of lines the description of `read_source` names is the limit of the
        `source` command. */"""
        from wingbox import cli, mcp_server

        search = cli.load_script("code_graph_search")
        self.assertEqual(search.SOURCE_LINE_LIMIT, mcp_server.SOURCE_LINES_PER_CALL)
        self.assertIn(f"at most {search.SOURCE_LINE_LIMIT} lines", mcp_server.TOOLS_BY_NAME["read_source"].description)

    def test_every_parameter_is_described_and_every_choice_is_closed(self) -> None:
        """/* REQ-MCP-028: every parameter of every tool has a description of at least ten characters; a parameter
        that is a choice lists its values (`enum`) and has a default among them; an integer parameter has a
        lower and an upper bound. */"""
        tools = McpSession(self.root, [_initialize(), _request(2, "tools/list")]).reply(2)["result"]["tools"]
        for tool in tools:
            for name, rule in tool["inputSchema"]["properties"].items():
                where = f"{tool['name']}.{name}"
                self.assertGreaterEqual(len(rule.get("description", "").strip()), 10, where)
                if "enum" in rule:
                    self.assertIn(rule.get("default"), rule["enum"], where)
                if rule.get("type") == "integer":
                    self.assertIn("minimum", rule, where)
                    self.assertIn("maximum", rule, where)

    def test_a_removed_tool_is_an_unknown_tool(self) -> None:
        """/* REQ-MCP-022: a call of a removed tool name gets the unknown-tool error (-32602), as any unknown tool. */"""
        session = McpSession(self.root, [_initialize(), _call(2, "find_callers", {"function": "pay"}), _call(3, "rdeps", {"module": "shop/pricing"})])
        self.assertEqual(-32602, session.reply(2)["error"]["code"])
        self.assertEqual(-32602, session.reply(3)["error"]["code"])


class MergedToolsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls._tmp.name).resolve()
        _shop_with_more(cls.root)
        git = ["git", "-C", str(cls.root), "-c", "user.name=t", "-c", "user.email=t@example.invalid"]
        subprocess.run([*git, "add", "-A"], check=True)
        subprocess.run([*git, "commit", "-q", "-m", "base"], check=True)
        (cls.root / "shop" / "pricing.py").write_text("def total(items):\n    return sum(items) + 0\n", encoding="utf-8")
        cls.session = McpSession(cls.root, [
            _initialize(), _call(2, "build_graph", {}),
            _call(10, "list_dependencies", {"module": "shop/checkout", "depth": 1}),
            _call(11, "list_dependencies", {"module": "shop/pricing", "direction": "in", "depth": 1}),
            _call(12, "list_dependencies", {"module": "shop/pricing", "direction": "sideways"}),
            _call(20, "trace_calls", {"function": "total"}),
            _call(21, "trace_calls", {"function": "pay", "direction": "out"}),
            _call(22, "trace_calls", {"function": "post", "to": "total"}),
            _call(30, "find_definitions", {"name": "total", "kind": "function"}),
            _call(31, "find_definitions", {"name": "Order", "kind": "type"}),
            _call(32, "find_definitions", {"name": "LIMIT"}),
            _call(33, "find_definitions", {"name": "Order"}),
            _call(40, "describe_module", {"module": "shop/checkout"}),
            _call(41, "describe_module", {"module": "pricing"}),
            _call(42, "describe_module", {"module": "no_such_thing_anywhere"}),
            _call(50, "list_changes", {}),
        ])

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def text(self, identifier: int) -> str:
        reply = self.session.reply(identifier)
        self.assertFalse(reply["result"]["isError"], reply)
        return "\n".join(_texts(reply))

    def test_dependencies_follows_a_module_out_or_in(self) -> None:
        """/* REQ-MCP-023: `dependencies` gives what a module uses (direction out, the default) or who uses it
        (direction in), hop by hop; any other direction is refused by the input schema. */"""
        self.assertFalse(self.session.reply(2)["result"]["isError"], self.session.reply(2))
        self.assertIn("(dep shop/checkout shop/pricing", self.text(10))
        self.assertIn("(rdep shop/pricing shop/checkout", self.text(11))
        self.assertTrue(self.session.reply(12)["result"]["isError"])

    def test_calls_follows_a_function_in_out_or_to_another(self) -> None:
        """/* REQ-MCP-024: `calls` gives who calls a function (direction in, the default), what it calls (out), or,
        with `to`, a call path from the function to another one. */"""
        self.assertIn("(callers-target shop/pricing::total", self.text(20))
        self.assertIn("shop/checkout", self.text(20))
        self.assertIn("(calls-source shop/checkout::pay", self.text(21))
        self.assertIn("shop/pricing", self.text(21))
        path = self.text(22)
        for step in ("shop/api", "shop/checkout", "shop/pricing"):
            self.assertIn(step, path)
        self.assertLess(path.index("shop/api"), path.index("shop/pricing"))

    def test_definitions_finds_functions_types_and_constants(self) -> None:
        """/* REQ-MCP-025: `definitions` says where a name is defined, with file and line: kind function or type
        narrows it; without a kind it finds functions, types and constants, and prints definitions only (no
        list of the modules that refer to the name). */"""
        self.assertIn("shop/pricing", self.text(30))
        self.assertIn("shop/models", self.text(31))
        self.assertIn("Order", self.text(31))
        constant = self.text(32)
        self.assertIn("LIMIT", constant)
        self.assertIn("shop/models", constant)
        both = self.text(33)
        self.assertIn("shop/models", both)
        for line in both.splitlines():
            self.assertFalse(line.startswith(("(refs ", "(imports ")), line)

    def test_module_takes_a_module_or_a_keyword(self) -> None:
        """/* REQ-MCP-026: `modules` analyzes the module it is given by ID or file path; given a word that is no
        module it analyzes the best matching module; a word that matches nothing is a tool error. */"""
        self.assertIn("shop/checkout", self.text(40))
        self.assertIn("shop/pricing", self.text(41))
        self.assertTrue(self.session.reply(42)["result"]["isError"], self.session.reply(42))

    def test_changes_lists_the_functions_changed_since_a_ref(self) -> None:
        """/* REQ-MCP-027: `changes` lists the functions changed in the working files against a git ref (default
        HEAD). */"""
        self.assertIn("total", self.text(50))


if __name__ == "__main__":
    unittest.main()
