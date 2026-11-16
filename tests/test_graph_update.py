import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from wingbox import cli  # noqa: E402

builder = cli.load_script("build_code_graph")
graph_update = cli.load_script("graph_update")

PYTHON_FILES = {
    "shop/__init__.py": "",
    "shop/pricing.py": "class Pricer:\n    def total(self, items):\n        return sum(items)\n\n\ndef order_total(items):\n    return Pricer().total(items)\n",
    "shop/cart.py": "from shop.pricing import order_total\n\n\ndef pay(items):\n    return order_total(items)\n",
    "shop/checkout.py": "from shop.discounts import best_discount\nfrom shop import cart\n\n\ndef checkout(items):\n    return best_discount(items) + cart.pay(items)\n",
    "shop/report.py": "from shop.pricing import Pricer\n\n\ndef report(items):\n    return Pricer().total(items)\n",
}
TS_FILES = {
    "web/src/util.ts": "export function fmt(n: number): string { return String(n); }\nexport interface Shape { area(): number }\n",
    "web/src/view.ts": 'import { fmt } from "./util";\nexport function show(n: number) { return fmt(n); }\n',
    "web/src/index.ts": 'export * from "./util";\nexport { show } from "./view";\n',
    "web/src/app.ts": 'import { show } from "./index";\nimport { load } from "./data/load";\nexport const run = () => show(load());\n',
}


def _write(root: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def _build(root: Path, *extra: str) -> int:
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return builder.main(["--root", str(root), *extra])


def _published(root: Path) -> dict[str, bytes]:
    names = (".code_graph.sexp", ".code_graph.uses.sexp", ".code_graph_cache.json", ".wingbox/graph-fragments.json")
    return {name: (root / name).read_bytes() for name in names}


class PartialUpdateTest(unittest.TestCase):
    """A partial update publishes the graph a full build of the same tree publishes, byte for byte."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(os.path.realpath(self._tmp.name)) / "project"
        self.root.mkdir()
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        _write(self.root, PYTHON_FILES)
        _write(self.root, TS_FILES)
        self.assertEqual(_build(self.root), 0)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def update(self, changed: list[str]):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return graph_update.update_graph(
                str(self.root), changed, str(self.root / ".wingbox" / "graph-fragments.json")
            )

    def assert_same_as_full_build(self, changed: list[str]) -> "graph_update.Updated":
        result = self.update(changed)
        self.assertIsInstance(result, graph_update.Updated, result)
        patched = _published(self.root)
        self.assertEqual(_build(self.root, "--full"), 0)
        rebuilt = _published(self.root)
        for name in patched:
            self.assertEqual(patched[name], rebuilt[name], f"{name} differs after {changed}")
        return result

    def test_an_edit_that_adds_an_import_is_patched_like_a_full_build(self) -> None:
        """/* REQ-FRESH-001: after an edit that adds an import, a partial update must publish the same graph, use file, cache and fragment store as a full build, and redo fewer modules than the project has. */"""
        (self.root / "shop/cart.py").write_text(
            "from shop.pricing import order_total, Pricer\n\n\ndef pay(items):\n    return order_total(items) + Pricer().total(items)\n",
            encoding="utf-8",
        )
        result = self.assert_same_as_full_build(["shop/cart.py"])
        self.assertLess(result.redone, result.modules)
        self.assertIn("(imports", (self.root / ".code_graph.sexp").read_text(encoding="utf-8"))

    def test_a_created_module_and_its_importer(self) -> None:
        """/* REQ-FRESH-002: a created module and an edit that imports it must be patched like a full build. */"""
        _write(self.root, {"shop/tax.py": "def vat(x):\n    return x * 0.2\n"})
        (self.root / "shop/cart.py").write_text(
            "from shop.pricing import order_total\nfrom shop.tax import vat\n\n\ndef pay(items):\n    return vat(order_total(items))\n",
            encoding="utf-8",
        )
        self.assert_same_as_full_build(["shop/tax.py", "shop/cart.py"])
        self.assertIn(" tax)", (self.root / ".code_graph.sexp").read_text(encoding="utf-8"))

    def test_a_created_module_that_an_existing_import_was_waiting_for(self) -> None:
        """/* REQ-FRESH-003: creating the module an unchanged file already imports must redo that importer: same graph as a full build. */"""
        _write(self.root, {"shop/discounts.py": "def best_discount(items):\n    return 0\n"})
        self.assert_same_as_full_build(["shop/discounts.py"])
        _write(self.root, {"web/src/data/load.ts": "export function load(): number { return 1; }\n"})
        self.assert_same_as_full_build(["web/src/data/load.ts"])

    def test_a_deleted_module(self) -> None:
        """/* REQ-FRESH-004: deleting a module others import must remove it and its edges like a full build. */"""
        (self.root / "shop/pricing.py").unlink()
        self.assert_same_as_full_build(["shop/pricing.py"])
        self.assertNotIn("pricing", (self.root / ".code_graph.sexp").read_text(encoding="utf-8"))

    def test_a_renamed_module_with_and_without_its_importers_fixed(self) -> None:
        """/* REQ-FRESH-005: a rename (old path deleted, new path created), then the importers fixed, must each give the graph of a full build. */"""
        os.rename(self.root / "web/src/util.ts", self.root / "web/src/format.ts")
        self.assert_same_as_full_build(["web/src/util.ts", "web/src/format.ts"])
        for name in ("web/src/view.ts", "web/src/index.ts"):
            path = self.root / name
            path.write_text(path.read_text(encoding="utf-8").replace("./util", "./format"), encoding="utf-8")
        self.assert_same_as_full_build(["web/src/view.ts", "web/src/index.ts"])

    def test_an_export_change_reaches_the_files_that_use_it_through_a_barrel(self) -> None:
        """/* REQ-FRESH-006: renaming an export that importers reach through a re-export barrel must redo them: same graph as a full build. */"""
        (self.root / "web/src/util.ts").write_text(
            "export function format(n: number): string { return String(n); }\nexport function fmt(n: number) { return format(n); }\nexport type Shape = { area(): number };\n",
            encoding="utf-8",
        )
        self.assert_same_as_full_build(["web/src/util.ts"])

    def test_a_resolution_config_or_another_language_takes_an_incremental_build(self) -> None:
        """/* REQ-FRESH-007: a changed package.json, tsconfig.json or pyproject.toml, or a changed source of a language whose imports depend on other files' contents, must not be patched (NotApplicable). */"""
        for name in ("package.json", "web/tsconfig.json", "pyproject.toml"):
            _write(self.root, {name: "{}\n" if name.endswith(".json") else "[project]\nname='x'\n"})
            self.assertIsInstance(self.update([name]), graph_update.NotApplicable)
        _write(self.root, {"svc/Main.java": "package svc;\nclass Main {}\n"})
        self.assertIsInstance(self.update(["svc/Main.java"]), graph_update.NotApplicable)

    def test_a_created_file_under_git_ignore_rules_stays_out(self) -> None:
        """/* REQ-FRESH-008: a created source that the Git ignore rules exclude must stay out of the patched graph, as discovery leaves it out. */"""
        _write(self.root, {".gitignore": "generated/\n"})
        self.assertEqual(_build(self.root), 0)
        _write(self.root, {"generated/out.py": "X = 1\n"})
        result = self.update(["generated/out.py"])
        self.assertIsInstance(result, graph_update.Updated)
        self.assertNotIn("generated", (self.root / ".code_graph.sexp").read_text(encoding="utf-8"))

    def test_a_store_that_does_not_match_the_graph_is_not_used(self) -> None:
        """/* REQ-FRESH-009: when the published graph was built after the fragment store (another key), the update must not apply. */"""
        (self.root / "shop/cart.py").write_text("def pay(items):\n    return 0\n", encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            # a build elsewhere (no fragment store), then published in place of the graph
            self.assertEqual(builder.main(["--root", str(self.root), "--output", str(self.root / "other.sexp")]), 0)
        os.replace(self.root / "other.sexp", self.root / ".code_graph.sexp")
        os.replace(self.root / "other.uses.sexp", self.root / ".code_graph.uses.sexp")
        self.assertIsInstance(self.update(["shop/cart.py"]), graph_update.NotApplicable)

    def test_files_wingbox_writes_do_not_change_the_cache_fingerprint(self) -> None:
        """/* REQ-FRESH-028: the files Wingbox keeps in .wingbox/ (a refresh log the repository ignores with *.log) must not change the build cache fingerprint, so the next build parses nothing again. */"""
        _write(self.root, {".gitignore": "*.log\n"})
        self.assertEqual(_build(self.root), 0)
        before = json.loads((self.root / ".code_graph_cache.json").read_text(encoding="utf-8"))["builder_fingerprint"]
        _write(self.root, {".wingbox/refresh.log": "x\n", "wingbox-out/notes.log": "y\n"})
        self.assertEqual(_build(self.root), 0)
        after = json.loads((self.root / ".code_graph_cache.json").read_text(encoding="utf-8"))["builder_fingerprint"]
        self.assertEqual(before, after)

    def test_parallel_resolution_gives_the_same_graph(self) -> None:
        """/* REQ-FRESH-010: resolving the modules in forked worker processes must give the same nodes and edges as one process. */"""
        files = sorted(str(path) for path in self.root.rglob("*") if path.suffix in (".py", ".ts"))
        results = [
            builder.parse_file((path, builder.language_for_file(os.path.basename(path)).name, "project", str(self.root)))
            for path in files
        ]
        for count in range(450):
            path = self.root / "many" / f"m{count}.py"
            path.parent.mkdir(exist_ok=True)
            path.write_text(f"from shop.pricing import order_total\nfrom many import m{(count * 7) % 450}\n\n\ndef f{count}(items):\n    return order_total(items)\n", encoding="utf-8")
            results.append(builder.parse_file((str(path), "python", "project", str(self.root))))
        results.sort(key=lambda result: os.path.relpath(result.path, self.root))
        one = builder.build_graph(results, str(self.root)).value
        many = builder.build_graph(results, str(self.root), jobs=4).value
        self.assertEqual(
            builder.render_sexp(one.nodes, one.edges, str(self.root), "syntax", len(results)),
            builder.render_sexp(many.nodes, many.edges, str(self.root), "syntax", len(results)),
        )
        self.assertEqual(one.nodes, many.nodes)


if __name__ == "__main__":
    unittest.main()
