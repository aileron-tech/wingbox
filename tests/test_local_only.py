import ast
import http.client
import io
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
# The one file that opens a network connection: the download of `wingbox add <language>` (REQ-SEC-003).
DOWNLOAD_FILE = "src/wingbox/fetch.py"
NETWORK_IMPORT = re.compile(
    r"^\s*(?:import|from)\s+(?:socket|ssl|http|ftplib|smtplib|telnetlib|xmlrpc|requests|httpx|aiohttp|urllib3|websockets?"
    r"|urllib\.request|urllib\.error)\b"
)
REMOTE_RESOURCE = re.compile(r"""(?:<script[^>]*\ssrc=|<link[^>]*\shref=|@import\s|url\(|fetch\(|XMLHttpRequest|WebSocket\()""")


class LocalOnlyTest(unittest.TestCase):
    def test_the_package_imports_no_network_module(self) -> None:
        """/* REQ-SEC-001: the builder, the query commands and the `wingbox` command must not import a module that opens network connections; the README states that nothing leaves the machine. The one file that does is the download of `wingbox add`, which only that command reaches (REQ-SEC-003). */"""
        offenders = []
        for folder in ("scripts", "src"):
            for path in sorted((ROOT / folder).rglob("*.py")):
                for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                    if NETWORK_IMPORT.match(line):
                        offenders.append(f"{path.relative_to(ROOT).as_posix()}:{number}: {line.strip()}")
        self.assertEqual([DOWNLOAD_FILE], sorted({offender.split(":")[0] for offender in offenders}), offenders)

    def test_the_map_page_loads_nothing_from_outside(self) -> None:
        """/* REQ-SEC-002: map.html must be one self-contained file: no script, style, font or data loaded from a URL, so that it opens offline. */"""
        template = (ROOT / "src" / "wingbox" / "assets" / "map_template.html").read_text(encoding="utf-8")
        offenders = [
            f"{number}: {line.strip()[:120]}"
            for number, line in enumerate(template.splitlines(), start=1)
            if REMOTE_RESOURCE.search(line)
        ]
        self.assertEqual([], offenders)


def _module_of(path: Path) -> str:
    """The dotted name a file is imported by: wingbox.cli for src/wingbox/cli.py, lang.ondemand for scripts/lang/ondemand.py."""
    relative = path.relative_to(ROOT / ("src" if (ROOT / "src") in path.parents else "scripts"))
    parts = list(relative.with_suffix("").parts)
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def _imports(path: Path) -> list[tuple[str, str]]:
    """(imported module, name of the enclosing function or "") for every import statement of a file."""
    found: list[tuple[str, str]] = []
    package = _module_of(path).split(".")[:-1]

    def visit(node: ast.AST, function: str) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            function = function or node.name
        if isinstance(node, ast.Import):
            found.extend((alias.name, function) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = package[: len(package) - node.level + 1] if node.level else []
            module = ".".join(base + ([node.module] if node.module else []))
            found.append((module, function))
            found.extend((f"{module}.{alias.name}" if module else alias.name, function) for alias in node.names)
        for child in ast.iter_child_nodes(node):
            visit(child, function)

    visit(ast.parse(path.read_text(encoding="utf-8")), "")
    return found


class DownloadReachTest(unittest.TestCase):
    def sources(self) -> list[Path]:
        return [path for folder in ("scripts", "src") for path in sorted((ROOT / folder).rglob("*.py"))]

    def test_the_download_is_reachable_only_from_the_add_command(self) -> None:
        """/* REQ-SEC-003: nothing goes to the network on its own: the download module (wingbox/fetch.py) is imported by wingbox/add.py alone, add.py by one function of the command line alone, and that function is called only for the command `add`; no build, refresh, hook, query or MCP module imports either, by an import statement or by name. */"""
        importers: dict[str, set[tuple[str, str]]] = {"wingbox.fetch": set(), "wingbox.add": set()}
        named = []
        for path in self.sources():
            relative = path.relative_to(ROOT).as_posix()
            for module, function in _imports(path):
                if module in importers:
                    importers[module].add((relative, function))
            text = path.read_text(encoding="utf-8")
            if relative not in ("src/wingbox/add.py", "src/wingbox/cli.py") and re.search(
                r"""(?:import_module|__import__|load_script)\(\s*["'](?:wingbox\.)?(?:fetch|add)["']""", text
            ):
                named.append(relative)
        self.assertEqual([], named)
        self.assertEqual({"src/wingbox/add.py"}, {relative for relative, _function in importers["wingbox.fetch"]})
        self.assertTrue(all(function for _relative, function in importers["wingbox.fetch"]), importers["wingbox.fetch"])
        self.assertEqual({("src/wingbox/cli.py", "_add_command")}, importers["wingbox.add"])
        tree = ast.parse((ROOT / "src" / "wingbox" / "cli.py").read_text(encoding="utf-8"))
        calls = []
        for function in (node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)):
            for branch in (node for node in ast.walk(function) if isinstance(node, ast.If)):
                for node in (inner for statement in branch.body for inner in ast.walk(statement)):
                    if isinstance(node, ast.Name) and node.id == "_add_command":
                        calls.append((function.name, ast.unparse(branch.test)))
        uses = [node for node in ast.walk(tree) if isinstance(node, ast.Name) and node.id == "_add_command"]
        self.assertEqual([("_run", "command == 'add'")], calls)
        self.assertEqual(1, len(uses))

    def test_a_build_that_skips_a_language_loads_no_download_code(self) -> None:
        """/* REQ-SEC-004: a build that meets files of a language whose parser is missing, and a query after it, finish without loading the download module, the add command or urllib.request: the missing parser is reported, never fetched. */"""
        script = (
            "import importlib.metadata as metadata, sys\n"
            "real = metadata.version\n"
            "def version(name):\n"
            "    if name == 'tree-sitter-c-sharp':\n"
            "        raise metadata.PackageNotFoundError(name)\n"
            "    return real(name)\n"
            "metadata.version = version\n"
            "from wingbox import cli\n"
            "root = sys.argv[1]\n"
            "status = cli.main(['build', root]) or cli.main(['impact', 'tool.py', '--root', root])\n"
            "loaded = sorted(name for name in ('wingbox.fetch', 'wingbox.add', 'urllib.request') if name in sys.modules)\n"
            "print('STATUS', status, 'LOADED', loaded)\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / "Service.cs").write_text("public class Service { }\n", encoding="utf-8")
            (root / "tool.py").write_text("X = 1\n", encoding="utf-8")
            environment = dict(os.environ, WINGBOX_PARSERS_DIR=str(root / "no-parsers"), WINGBOX_JOBS="1")
            environment["PYTHONPATH"] = os.pathsep.join(filter(None, (str(ROOT / "src"), environment.get("PYTHONPATH", ""))))
            done = subprocess.run([sys.executable, "-c", script, str(root)], capture_output=True, text=True, env=environment)
            self.assertIn("STATUS 0 LOADED []", done.stdout, done.stderr)
            self.assertIn("wingbox add csharp", done.stderr)
            self.assertFalse((root / "no-parsers").exists())

    def test_the_download_takes_only_https_files_of_the_package_index(self) -> None:
        """/* REQ-SEC-005: the download accepts only an https URL on files.pythonhosted.org and follows no redirect; any other URL is refused before a connection is opened; a body longer than the pinned size is refused and leaves no file. */"""
        sys.path.insert(0, str(ROOT / "src"))
        from wingbox import fetch

        with tempfile.TemporaryDirectory() as directory:
            target = os.path.join(directory, "file.whl")
            with mock.patch.object(fetch, "_open", side_effect=AssertionError("a connection was opened")):
                for url in (
                    "http://files.pythonhosted.org/packages/aa/x.whl",
                    "https://example.org/packages/aa/x.whl",
                    "https://files.pythonhosted.org.example.org/packages/aa/x.whl",
                    "https://user@files.pythonhosted.org/packages/aa/x.whl",
                    "https://files.pythonhosted.org:8443/packages/aa/x.whl",
                    "file:///etc/hosts",
                    "ftp://files.pythonhosted.org/packages/aa/x.whl",
                ):
                    with self.subTest(url=url), self.assertRaises(OSError):
                        fetch.download(url, target, 10)
            self.assertFalse(os.path.exists(target))
            url = "https://files.pythonhosted.org/packages/aa/x.whl"
            with self.assertRaises(OSError):
                fetch.NoRedirect().redirect_request(
                    fetch.urllib.request.Request(url), io.BytesIO(), 302, "Found", http.client.HTTPMessage(),
                    "https://example.org/x.whl",
                )
            with mock.patch.object(fetch, "_open", return_value=io.BytesIO(b"0123456789")) as opened:
                fetch.download(url, target, 10)
            self.assertEqual(url, opened.call_args.args[0])
            self.assertEqual(b"0123456789", Path(target).read_bytes())
            os.unlink(target)
            with mock.patch.object(fetch, "_open", return_value=io.BytesIO(b"0123456789 and more")):
                with self.assertRaises(OSError):
                    fetch.download(url, target, 10)
            self.assertFalse(os.path.exists(target))


if __name__ == "__main__":
    unittest.main()
