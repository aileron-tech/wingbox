"""The tool list of the MCP server: eleven tools with distinct jobs, one naming pattern, descriptions that tell
neighbours apart."""
from __future__ import annotations

import re
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.test_mcp_server import McpSession, _call, _initialize, _make_shop, _request, _texts

TOOLS = ("graph", "impact", "modules", "definitions", "usages", "dependencies", "calls", "layers", "boundaries", "changes", "facts")
REMOVED = ("analyze", "quick", "boundary", "deps", "rdeps", "entity", "find_function", "find_type", "find_callers", "find_calls",
           "find_callchain", "diff_functions", "build", "query", "module")
# Names of the query language and words of plain English that a description may use although a removed tool had the name.
ALLOWED_IN_TEXT = {"facts": ("deps", "rdeps", "query"), "graph": ("build",), "modules": ("module",), "dependencies": ("module",),
                   "boundaries": ("module",), "layers": ("module",), "impact": ("module",)}


def _shop_with_more(root: Path) -> None:
    _make_shop(root)
    (root / "shop" / "models.py").write_text("LIMIT = 3\n\n\nclass Order:\n    def amount(self):\n        return LIMIT\n", encoding="utf-8")
    (root / "shop" / "api.py").write_text("from shop.checkout import pay\n\n\ndef post(items):\n    return pay(items)\n", encoding="utf-8")


class ToolListTest(unittest.TestCase):
    def setUp(self) -> None:
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        self.root = Path(tmpdir.name).resolve()

    def test_the_server_offers_eleven_tools_named_after_their_answers(self) -> None:
        """/* REQ-MCP-029: `tools/list` offers exactly eleven tools (within the 3 to 15 a tool directory grades
        best), in this order: graph, impact, modules, definitions, usages, dependencies, calls, layers,
        boundaries, changes, facts. Every name is one lowercase noun, the name of what the tool answers; no name is a prefix of another or shares
        its first six letters with another; the names of the merged and removed tools are gone. */"""
        tools = McpSession(self.root, [_initialize(), _request(2, "tools/list")]).reply(2)["result"]["tools"]
        names = [tool["name"] for tool in tools]
        self.assertEqual(list(TOOLS), names)
        self.assertTrue(3 <= len(names) <= 15)
        for name in names:
            self.assertRegex(name, r"^[a-z]+$")
            self.assertNotIn(name, REMOVED)
        for first in names:
            for second in names:
                if first != second:
                    self.assertNotEqual(first[:6], second[:6], (first, second))

    def test_the_instructions_name_only_tools_that_exist(self) -> None:
        """/* REQ-MCP-030: the instructions the server gives at `initialize` and `server/discover` name tools of
        the current list only: no merged or removed tool name is left in them as a tool to call, and they name
        impact, modules, usages and graph. */"""
        instructions = McpSession(self.root, [_initialize()]).reply(1)["result"]["instructions"]
        named = set(re.findall(r"`([a-z_]+)`", instructions))
        self.assertEqual(set(), named & set(REMOVED))
        self.assertLessEqual({"impact", "modules", "usages", "graph"}, named)
        self.assertLessEqual(named, set(TOOLS))

    def test_every_description_says_what_comes_back_and_when_to_use_a_neighbour(self) -> None:
        """/* REQ-MCP-021: the description of every tool starts with a verb that says what the tool does
        ("Lists ...", "Traces ..."), says when to use the tool ("Use it ..."), and names the neighbour to use
        for another question as "use `name`" (graph and facts have no neighbour). A tool that takes `all` says
        that a list stops at 200 items; a tool that looks a name up says what an unknown name returns. A
        description is at most 700 characters and names no removed tool. */"""
        tools = McpSession(self.root, [_initialize(), _request(2, "tools/list")]).reply(2)["result"]["tools"]
        for tool in tools:
            name, text = tool["name"], tool["description"]
            self.assertLessEqual(len(text), 700, name)
            self.assertRegex(text, r"^[A-Z][a-z]+s ", name)
            self.assertIn("Use it ", text, name)
            for old in REMOVED:
                if old not in ALLOWED_IN_TEXT.get(name, ()):
                    self.assertNotRegex(text, rf"\b{old}\b", name)
            if name not in ("graph", "facts"):
                others = [other for other in TOOLS if other != name and f"use `{other}`" in text]
                self.assertTrue(others, f"{name}: no neighbour named in {text!r}")
            for other in re.findall(r"use `([a-z_]+)`", text):
                self.assertIn(other, TOOLS, name)
            if "all" in tool["inputSchema"]["properties"]:
                self.assertIn("200", text, name)
            if name in ("impact", "modules", "definitions", "usages", "dependencies", "calls"):
                self.assertIn("is answered with an error", text, name)

    def test_facts_lists_its_functions(self) -> None:
        """/* REQ-MCP-031: the expression `(help)`, which the description of `facts` names, returns the list of
        the functions of the query language and is not an error. */"""
        _make_shop(self.root)
        session = McpSession(self.root, [_initialize(), _call(2, "graph", {}), _call(3, "facts", {"expression": "(help)"})])
        reply = session.reply(3)
        self.assertFalse(reply["result"]["isError"], reply)
        text = "\n".join(_texts(reply))
        for function in ("(deps ", "(rdeps ", "(intersect ", "(files "):
            self.assertIn(function, text)

    def test_the_three_renamed_tools_answer_under_their_new_names(self) -> None:
        """/* REQ-MCP-032: `graph` builds the code graph (the only tool that writes), `modules` describes a
        module and `facts` evaluates a query; `build`, `module` and `query` are unknown tools. */"""
        _make_shop(self.root)
        session = McpSession(self.root, [
            _initialize(), _request(2, "tools/list"), _call(3, "graph", {}), _call(4, "modules", {"module": "shop/checkout"}),
            _call(5, "facts", {"expression": '(count (rdeps "shop/pricing" 1))'}),
            _call(6, "build", {}), _call(7, "module", {"module": "shop/checkout"}), _call(8, "query", {"expression": "(help)"}),
        ])
        hints = {tool["name"]: tool["annotations"]["readOnlyHint"] for tool in session.reply(2)["result"]["tools"]}
        self.assertEqual(["graph"], [name for name, read_only in hints.items() if not read_only])
        self.assertFalse(session.reply(3)["result"]["isError"], session.reply(3))
        self.assertIn("shop/pricing", "\n".join(_texts(session.reply(4))))
        self.assertIn("1", "\n".join(_texts(session.reply(5))))
        for identifier in (6, 7, 8):
            self.assertEqual(-32602, session.reply(identifier)["error"]["code"])

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
            _initialize(), _call(2, "graph", {}),
            _call(10, "dependencies", {"module": "shop/checkout", "depth": 1}),
            _call(11, "dependencies", {"module": "shop/pricing", "direction": "in", "depth": 1}),
            _call(12, "dependencies", {"module": "shop/pricing", "direction": "sideways"}),
            _call(20, "calls", {"function": "total"}),
            _call(21, "calls", {"function": "pay", "direction": "out"}),
            _call(22, "calls", {"function": "post", "to": "total"}),
            _call(30, "definitions", {"name": "total", "kind": "function"}),
            _call(31, "definitions", {"name": "Order", "kind": "type"}),
            _call(32, "definitions", {"name": "LIMIT"}),
            _call(33, "definitions", {"name": "Order"}),
            _call(40, "modules", {"module": "shop/checkout"}),
            _call(41, "modules", {"module": "pricing"}),
            _call(42, "modules", {"module": "no_such_thing_anywhere"}),
            _call(50, "changes", {}),
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
