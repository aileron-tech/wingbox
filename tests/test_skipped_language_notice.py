"""A language a build skipped (its parser is not installed) is said where an agent and a person read:
on the query commands' stderr, at the head of MCP answers, in the search hook's context, in the build's
last lines and in REPORT.md. Nothing is added when no language was skipped."""
import importlib.metadata
import io
import subprocess
import sys
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from wingbox import cli, freshness, mcp_server, overview  # noqa: E402

from tests.test_freshness import FreshnessTestCase, _environment  # noqa: E402

FACT = '(skipped-language csharp :files 2 :add "wingbox add csharp")'
CSHARP = {
    "storefront/Service.cs": "namespace Shop { public class Service { } }\n",
    "storefront/Order.cs": "namespace Shop { public class Order { } }\n",
}


@contextmanager
def without_csharp_parser():
    real_version = importlib.metadata.version

    def version(name: str) -> str:
        if name == "tree-sitter-c-sharp":
            raise importlib.metadata.PackageNotFoundError(name)
        return real_version(name)

    with mock.patch.object(importlib.metadata, "version", version):
        yield


class SkippedLanguageNoticeTest(FreshnessTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.write(CSHARP)

    def impact(self) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", "wingbox.cli", "impact", "shop/pricing.py"],
            capture_output=True, text=True, cwd=self.root, env=_environment(),
        )

    def test_a_skipped_language_is_said_wherever_the_graph_is_read(self) -> None:
        """/* REQ-FRESH-052: after a build that skipped a language, every reader of the graph carries the one-line fact `(skipped-language csharp :files N :add "wingbox add csharp")`: a query command prints it on stderr, an MCP answer starts with it, the search hook adds it to the context it gives (and stays silent when it has no context), the build ends with a line that names the command, and REPORT.md says it in its key findings. */"""
        out = io.StringIO()
        with without_csharp_parser(), redirect_stdout(out), redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(["build", str(self.root)]), 0)
        self.assertEqual(FACT, freshness.skipped_languages_line(str(self.root)))
        last_lines = out.getvalue().splitlines()[-3:]
        self.assertTrue(any("2 C# files" in line and "wingbox add csharp" in line for line in last_lines), last_lines)
        query = self.impact()
        self.assertEqual(0, query.returncode, query.stderr)
        self.assertIn(FACT, query.stderr)
        self.assertIn("shop/cart", query.stdout)
        server = mcp_server.Server(str(self.root))
        answer = mcp_server.TOOLS_BY_NAME["impact"].run(server, {"target": "shop/pricing.py"})
        self.assertTrue(answer.texts[0].startswith(FACT + "\n"), answer.texts[0][:200])
        with without_csharp_parser():
            rebuilt = mcp_server.TOOLS_BY_NAME["build"].run(server, {})
        self.assertIn("wingbox add csharp", rebuilt.texts[0])
        context = self.context("shop/pricing.py")
        self.assertIn("(wingbox-file shop/pricing.py", context)
        self.assertEqual(1, context.count(FACT), context)
        self.assertEqual("", self.grep("no_such_name_anywhere_in_this_project"))
        report = (self.root / ".wingbox" / "REPORT.md").read_text(encoding="utf-8")
        self.assertIn("2 C# files", report)
        self.assertIn("`wingbox add csharp`", report)

    def test_nothing_is_added_when_no_language_was_skipped(self) -> None:
        """/* REQ-FRESH-053: a build that parsed every language leaves no skipped-language fact: the record of an earlier build is removed, and the query commands, the MCP answers, the hook context and REPORT.md say nothing about skipped languages. */"""
        with without_csharp_parser(), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(["build", str(self.root)]), 0)
        self.assertEqual(FACT, freshness.skipped_languages_line(str(self.root)))
        self.build()
        self.assertEqual("", freshness.skipped_languages_line(str(self.root)))
        query = self.impact()
        self.assertNotIn("skipped", query.stderr)
        server = mcp_server.Server(str(self.root))
        answer = mcp_server.TOOLS_BY_NAME["impact"].run(server, {"target": "shop/pricing.py"})
        self.assertNotIn("skipped-language", answer.texts[0])
        self.assertNotIn("skipped-language", self.context("shop/pricing.py"))
        report = (self.root / ".wingbox" / "REPORT.md").read_text(encoding="utf-8")
        self.assertNotIn("wingbox add", report)
        self.assertIn("storefront", (self.root / ".wingbox" / "index.sexp").read_text(encoding="utf-8"))

    def test_the_report_names_each_skipped_language_and_its_command(self) -> None:
        """/* REQ-OUT-019: REPORT.md lists under its key findings every language the build left out, with the number of files and the command that adds its parser; without skipped languages the report has no such line. */"""
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(["build", str(self.root)]), 0)
        search = cli.load_script("code_graph_search")
        graph = search.CodeGraph()
        graph.load(freshness.graph_path(str(self.root)), str(self.root))
        view = overview.build_overview(graph, search, "project")
        plain = overview.render_report(view, "2026-11-18", "9.9.9")
        self.assertNotIn("wingbox add", plain)
        import dataclasses

        skipped = dataclasses.replace(view, skipped_languages=(("C#", "csharp", 214), ("Go", "go", 1)))
        report = overview.render_report(skipped, "2026-11-18", "9.9.9")
        self.assertIn("- Not in this graph: 214 C# files (the C# parser is not installed). Run `wingbox add csharp`, then `wingbox build`.", report)
        self.assertIn("- Not in this graph: 1 Go file (the Go parser is not installed). Run `wingbox add go`, then `wingbox build`.", report)
        self.assertEqual((("C#", "csharp", 2),), overview.skipped_languages(FACT, {"csharp": "C#"}))
