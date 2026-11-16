import re
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
                        offenders.append(f"{path.relative_to(ROOT)}:{number}: {line.strip()}")
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


if __name__ == "__main__":
    unittest.main()
