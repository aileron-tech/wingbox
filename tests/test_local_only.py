import ast
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
# Modules that open a network connection or exist to serve one: each name and everything below it.
# urllib.parse only takes addresses apart and is not listed.
NETWORK_MODULES = frozenset({
    "socket", "socketserver", "ssl", "http", "ftplib", "smtplib", "poplib", "imaplib", "nntplib", "telnetlib", "xmlrpc",
    "urllib.request", "urllib.error", "urllib.response", "urllib.robotparser",
    "requests", "httpx", "aiohttp", "urllib3", "httplib2", "pycurl", "websocket", "websockets",
})
_DYNAMIC_IMPORTS = frozenset({"__import__", "import_module"})


def _denied(name: str) -> bool:
    return any(name == module or name.startswith(module + ".") for module in NETWORK_MODULES)


def network_imports(source: str) -> list[tuple[int, str]]:
    """(line, module) of every import of a network module in Python source, read with `ast`.

    Every way of writing an import is seen: `import a, b`, `import a.b as c`, `from a import b` (which
    imports a.b when b is a module), an import inside a function, and `__import__("a")` or
    `import_module("a")` with the name as a literal."""
    found = []
    for node in ast.walk(ast.parse(source)):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names = [node.module, *(f"{node.module}.{alias.name}" for alias in node.names)]
        elif isinstance(node, ast.Call) and node.args:
            called = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
            literal = node.args[0]
            if called in _DYNAMIC_IMPORTS and isinstance(literal, ast.Constant) and isinstance(literal.value, str):
                names = [literal.value]
        found += [(node.lineno, name) for name in dict.fromkeys(names) if _denied(name)]
    return sorted(found)


REMOTE_RESOURCE = re.compile(r"""(?:<script[^>]*\ssrc=|<link[^>]*\shref=|@import\s|url\(|fetch\(|XMLHttpRequest|WebSocket\()""")


class LocalOnlyTest(unittest.TestCase):
    def test_the_package_imports_no_network_module(self) -> None:
        """/* REQ-SEC-001: the builder, the query commands and the `wingbox` command (`scripts/`, `src/`) and the tools of the repository (`tools/`) must not import a module that opens network connections, however the import is written; the README states that nothing leaves the machine. */"""
        offenders, files = [], 0
        for folder in ("scripts", "src", "tools"):
            for path in sorted((ROOT / folder).rglob("*.py")):
                files += 1
                offenders += [
                    f"{path.relative_to(ROOT).as_posix()}:{number}: {module}"
                    for number, module in network_imports(path.read_text(encoding="utf-8"))
                ]
        self.assertGreater(files, 50, "the folders were not read")
        self.assertEqual([], offenders)

    def test_no_way_of_writing_a_network_import_escapes_the_check(self) -> None:
        """/* REQ-SEC-9945: the check of REQ-SEC-001 reads the imports with `ast`, so it names a network module however it is imported: second in `import os, socket`, as `from urllib import request`, `import urllib.request as r`, `import socketserver`, `from http import client`, inside a function or under `if`, and through `__import__("socket")` or `importlib.import_module("http.client")`; `urllib.parse`, a relative import and a module whose name only starts like one (`sockets`, `http_helpers`) pass. */"""
        for source, modules in (
            ("import os, socket\n", ["socket"]),
            ("from urllib import request\n", ["urllib.request"]),
            ("import urllib.request as fetch\n", ["urllib.request"]),
            ("import socketserver\n", ["socketserver"]),
            ("from http import client\n", ["http", "http.client"]),
            ("from http.server import HTTPServer\n", ["http.server", "http.server.HTTPServer"]),
            ("def load():\n    if True:\n        import ssl\n", ["ssl"]),
            ('module = __import__("socket")\n', ["socket"]),
            ('import importlib\nclient = importlib.import_module("http.client")\n', ["http.client"]),
            ('from importlib import import_module\nclient = import_module("urllib.request")\n', ["urllib.request"]),
            ("import requests\nimport httpx\n", ["requests", "httpx"]),
            ("import xmlrpc.client\n", ["xmlrpc.client"]),
        ):
            with self.subTest(source=source):
                self.assertEqual(sorted(modules), sorted(module for _line, module in network_imports(source)))
        allowed = (
            "import os, sys\nfrom urllib.parse import quote, unquote\nimport urllib.parse\nfrom urllib import parse\n"
            "from . import socket\nfrom .http import client\nimport sockets\nimport http_helpers\nimport webbrowser\n"
            'name = "socket"\nmodule = __import__(name)\ntext = "import socket"\n'
        )
        self.assertEqual([], network_imports(allowed))
        self.assertEqual([(3, "ssl")], network_imports("import os\n\nimport ssl\n"))

    def test_the_map_page_loads_nothing_from_outside(self) -> None:
        """/* REQ-SEC-002: map.html must be one self-contained file: no script, style, font or data loaded from a URL, so that it opens offline. */"""
        template = (ROOT / "src" / "wingbox" / "assets" / "map_template.html").read_text(encoding="utf-8")
        offenders = [
            f"{number}: {line.strip()[:120]}"
            for number, line in enumerate(template.splitlines(), start=1)
            if REMOTE_RESOURCE.search(line)
        ]
        self.assertEqual([], offenders)


class MissingParserTest(unittest.TestCase):
    def test_a_build_that_skips_a_language_loads_no_download_code(self) -> None:
        """/* REQ-SEC-004: a build that meets files of a language whose parser is missing, and a query after it, finish without loading urllib.request, http.client or ssl: the missing parser is reported with the pip extra that installs it, never fetched. */"""
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
            "loaded = sorted(name for name in ('urllib.request', 'urllib.error', 'http.client', 'ssl') if name in sys.modules)\n"
            "print('STATUS', status, 'LOADED', loaded)\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / "Service.cs").write_text("public class Service { }\n", encoding="utf-8")
            (root / "tool.py").write_text("X = 1\n", encoding="utf-8")
            environment = dict(os.environ, WINGBOX_JOBS="1")
            environment["PYTHONPATH"] = os.pathsep.join(filter(None, (str(ROOT / "src"), environment.get("PYTHONPATH", ""))))
            done = subprocess.run([sys.executable, "-c", script, str(root)], capture_output=True, text=True, env=environment)
            self.assertIn("STATUS 0 LOADED []", done.stdout, done.stderr)
            self.assertIn('pip install "wingbox[csharp]"', done.stderr)


if __name__ == "__main__":
    unittest.main()
