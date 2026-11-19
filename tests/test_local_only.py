import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NETWORK_IMPORT = re.compile(
    r"^\s*(?:import|from)\s+(?:socket|ssl|http|ftplib|smtplib|telnetlib|xmlrpc|requests|httpx|aiohttp|urllib3|websockets?"
    r"|urllib\.request|urllib\.error)\b"
)
REMOTE_RESOURCE = re.compile(r"""(?:<script[^>]*\ssrc=|<link[^>]*\shref=|@import\s|url\(|fetch\(|XMLHttpRequest|WebSocket\()""")


class LocalOnlyTest(unittest.TestCase):
    def test_the_package_imports_no_network_module(self) -> None:
        """/* REQ-SEC-001: the builder, the query commands and the `wingbox` command must not import a module that opens network connections; the README states that nothing leaves the machine. */"""
        offenders = []
        for folder in ("scripts", "src"):
            for path in sorted((ROOT / folder).rglob("*.py")):
                for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                    if NETWORK_IMPORT.match(line):
                        offenders.append(f"{path.relative_to(ROOT).as_posix()}:{number}: {line.strip()}")
        self.assertEqual([], offenders)

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
