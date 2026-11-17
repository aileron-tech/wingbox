"""`wingbox compare`: the architectures of the parts of a monorepo, per language."""
import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from wingbox import cli  # noqa: E402

CONFIG = """parts:
  packages/a:
    style: layered
    layers:
      api: [routes]
      service: [services]
  packages/b:
    style: layered
    layers:
      api: [controllers]
      service: [services]
      domain: [routes]
  packages/c:
    style: hexagonal
"""


class CompareTest(unittest.TestCase):
    def test_compare_shows_styles_layers_and_differences_per_language(self) -> None:
        """/* REQ-CMP-001: `wingbox compare` lists each part with its language, style and layers, and the differences within a language: a layer some parts lack, a folder name that means another layer elsewhere, and a style that differs from the majority, which also gets an advice item. */"""
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name).resolve()
        for part, folders in (("a", ("routes", "services")), ("b", ("controllers", "services", "routes")),
                              ("c", ("domain", "ports", "adapters"))):
            (root / "packages" / part).mkdir(parents=True)
            (root / "packages" / part / "package.json").write_text("{}\n", encoding="utf-8")
            for folder in folders:
                (root / "packages" / part / "src" / folder).mkdir(parents=True)
                (root / "packages" / part / "src" / folder / "x.ts").write_text("export const x = 1;\n", encoding="utf-8")
        (root / ".wingbox").mkdir()
        (root / ".wingbox" / "config.yaml").write_text(CONFIG, encoding="utf-8")
        output = io.StringIO()
        cwd = os.getcwd()
        os.chdir(root)
        try:
            with redirect_stdout(output):
                status = cli.main(["compare", "--sexp"])
        finally:
            os.chdir(cwd)
        text = output.getvalue()
        self.assertEqual(0, status, text)
        self.assertIn('(compare-part "packages/a" :language typescript :style layered :layers "api service")', text)
        self.assertIn('(compare-diff typescript :kind missing-layer :detail "packages/a has no domain")', text)
        self.assertIn('(compare-diff typescript :kind folder-meaning :detail "routes/ means different layers: packages/a layered:api; packages/b layered:domain")', text)
        self.assertIn('(compare-diff typescript :kind style :detail "packages/c is hexagonal, 2 of 3 typescript parts are layered")', text)
        self.assertIn('(advice "packages/c" :id ', text)
        self.assertIn(":kind deviates", text)


if __name__ == "__main__":
    unittest.main()
