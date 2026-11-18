"""The tool list of the MCP server: eleven tools with distinct jobs, one naming pattern, descriptions that tell
neighbours apart."""
from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.test_mcp_server import McpSession, _call, _initialize, _make_shop, _request, _texts

TOOLS = ("build", "impact", "module", "definitions", "usages", "dependencies", "calls", "layers", "boundaries", "changes", "query")
REMOVED = ("analyze", "quick", "boundary", "deps", "rdeps", "entity", "find_function", "find_type", "find_callers", "find_calls",
           "find_callchain", "diff_functions")


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
        """/* REQ-MCP-020: `tools/list` offers exactly eleven tools (within the 3 to 15 a tool directory grades
        best), in this order: build, impact, module, definitions, usages, dependencies, calls, layers,
        boundaries, changes, query. Every name is one lowercase word; no name is a prefix of another or shares
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

    def test_every_description_says_when_to_take_a_neighbour(self) -> None:
        """/* REQ-MCP-021: the description of every tool that has a neighbour names it: it says for which question
        to take another tool, by that tool's name (build and query have no neighbour). A description is one
        line of at most 400 characters and names no removed tool (the example of `query` is in the query
        language, whose functions keep their names). */"""
        tools = McpSession(self.root, [_initialize(), _request(2, "tools/list")]).reply(2)["result"]["tools"]
        for tool in tools:
            text = tool["description"]
            self.assertLessEqual(len(text), 400, tool["name"])
            if tool["name"] != "query":   # its example is in the query language, whose functions keep their names
                for old in REMOVED:
                    self.assertNotRegex(text, rf"\b{old}\b", tool["name"])
            if tool["name"] not in ("build", "query"):
                others = [name for name in TOOLS if name != tool["name"] and f"take {name}" in text]
                self.assertTrue(others, f"{tool['name']}: no neighbour named in {text!r}")

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
            _initialize(), _call(2, "build", {}),
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
            _call(40, "module", {"module": "shop/checkout"}),
            _call(41, "module", {"module": "pricing"}),
            _call(42, "module", {"module": "no_such_thing_anywhere"}),
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
        """/* REQ-MCP-026: `module` analyzes the module it is given by ID or file path; given a word that is no
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
