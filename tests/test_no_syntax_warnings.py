import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class NoSyntaxWarningsTest(unittest.TestCase):
    def test_sources_compile_without_warnings(self) -> None:
        """/* REQ-PORT-010: every source file compiles with warnings turned into errors, so a user never sees a SyntaxWarning (an invalid escape sequence, for example) the first time Wingbox runs on a new Python version. */"""
        offenders = []
        for folder in ("src", "scripts", "tools"):
            for path in sorted((ROOT / folder).rglob("*.py")):
                result = subprocess.run(
                    [sys.executable, "-W", "error", "-c", "import sys; compile(open(sys.argv[1], encoding='utf-8').read(), sys.argv[1], 'exec')", str(path)],
                    capture_output=True, text=True, encoding="utf-8", errors="replace",
                )
                if result.returncode != 0:
                    offenders.append(f"{path.relative_to(ROOT).as_posix()}: {result.stderr.strip().splitlines()[-1]}")
        self.assertEqual([], offenders)


if __name__ == "__main__":
    unittest.main()
