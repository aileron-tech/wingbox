"""`wingbox usages`: every place that uses one symbol, grouped by file, with line:column and the kind of use."""
from __future__ import annotations

import contextlib
import importlib.util
import io
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from scripts.build_code_graph import Ok, build_graph, detect_source_language, parse_file, render_sexp

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "code_graph_search.py"
_SPEC = importlib.util.spec_from_file_location("code_graph_search_for_usages", SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
search = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = search
_SPEC.loader.exec_module(search)

PYTHON = {
    "app/__init__.py": "from .models import Invoice\n",
    "app/models.py": "class Invoice:\n    def total(self): ...\n",
    "app/billing.py": (
        "from app import Invoice\n"
        "from app.models import Invoice as Inv\n"
        "\n"
        "class Draft(Invoice):\n"
        "    pass\n"
        "\n"
        "def make(x: Invoice) -> Inv:\n"
        "    inv = Invoice()\n"
        "    return inv.total()\n"
    ),
    "app/other.py": "def f(x):\n    return x.total()\n",
    "legacy/models.py": "class Invoice:\n    pass\n",
}


def _run(files: dict[str, str], argv: list[str], cargo: bool = False) -> str:
    """Build and load a graph of files, then run `code_graph_search.py <argv>` on it."""
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        if cargo:
            (root / "Cargo.toml").write_text('[package]\nname = "app"\nversion = "0.1.0"\n', encoding="utf-8")
        results = []
        for relative_path, content in files.items():
            path = root / relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            language = detect_source_language(path.name)
            assert language is not None
            results.append(parse_file((str(path), language, root.name)))
        graph = build_graph(results, str(root))
        assert isinstance(graph, Ok)
        map_path = root / ".code_graph.sexp"
        map_path.write_text(render_sexp(graph.value.nodes, graph.value.edges, str(root), "syntax", len(results)),
                            encoding="utf-8")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            search.main([*argv, "--map", str(map_path), "--root", str(root)])
        return output.getvalue()


class UsagesCommandTests(unittest.TestCase):
    def test_text_lists_every_use_by_file_with_position_and_kind(self) -> None:
        """/* REQ-CS-062: `usages <file>::<Name>` prints every use of the definition, through re-exports and aliases, grouped by file, each with line:column and its kind (re-export, import, extends, type, call). */"""
        self.assertEqual(
            _run(PYTHON, ["usages", "app/models.py::Invoice"]),
            textwrap.dedent(
                """\
                Usages of Invoice (class, app/models.py:1)
                  7 uses in 2 files

                app/__init__.py
                  1:21   re-export
                app/billing.py
                  1:17   import
                  2:24   import
                  4:13   extends
                  7:13   type
                  7:25   type
                  8:11   call
                """
            ),
        )

    def test_sexp_facts_carry_the_same_answer(self) -> None:
        """/* REQ-CS-063: `usages ... --sexp` prints usages-target, one usage fact per use and usages-summary; the text is their pretty print. */"""
        output = _run(PYTHON, ["usages", "app/models.py::Invoice", "--sexp"])
        lines = output.splitlines()
        self.assertEqual("(usages-target app/models::Invoice :kind cls :file app/models.py :line 1)", lines[0])
        self.assertIn("(usage app/billing 4 13 :kind extends :file app/billing.py)", lines)
        self.assertIn("(usage app/__init__ 1 21 :kind re-export :file app/__init__.py)", lines)
        self.assertEqual("(usages-summary app/models::Invoice :uses 7 :files 2 :unresolved 0)", lines[-1])

    def test_a_bare_name_with_several_definitions_lists_the_candidates(self) -> None:
        """/* REQ-CS-064: `usages <Name>` answers for the only definition of Name; with several it lists each candidate as <file>::<Name> to ask again, and lists no uses. */"""
        output = _run(PYTHON, ["usages", "Invoice"])
        self.assertIn("2 definitions of Invoice", output)
        self.assertIn("app/models.py::Invoice", output)
        self.assertIn("legacy/models.py::Invoice", output)
        self.assertNotIn("import", output)
        sexp = _run(PYTHON, ["usages", "Invoice", "--sexp"])
        self.assertIn("(usages-candidate app/models::Invoice :kind cls :file app/models.py :line 1)", sexp)
        self.assertIn("app/__init__.py\n  1:21   re-export\n", _run(PYTHON, ["usages", "app/models::Invoice"]))

    def test_method_usages_mark_calls_whose_receiver_type_is_not_in_sight(self) -> None:
        """/* REQ-CS-065: `usages <file>::<Class>.<method>` lists the calls that resolve to the method and, marked unresolved, the calls of a method of that name whose receiver type is not in sight. */"""
        output = _run(PYTHON, ["usages", "app/models.py::Invoice.total"])
        self.assertIn("app/billing.py\n  9:16   call\n", output)
        self.assertIn("app/other.py\n  2:14   unresolved-call\n", output)
        self.assertIn("1 use in 1 file; 1 unresolved call", output)

    def test_typescript_and_rust_kinds(self) -> None:
        """/* REQ-CS-066: TypeScript and Rust uses get their kinds: `export {X} from` and `pub use` are re-exports, extends/implements and `impl Trait for` are extends, annotations are types, `new C()` and calls are calls, a resolved method call is a call. */"""
        ts = _run({
            "src/model.ts": "export class Store {}\nexport interface Saver {}\n",
            "src/index.ts": "export { Store } from './model';\n",
            "src/app.ts": (
                "import { Store, Saver } from './index';\n"
                "export class Big extends Store implements Saver {}\n"
                "export const s: Store = new Store();\n"
            ),
        }, ["usages", "src/model.ts::Store"])
        self.assertIn("src/index.ts\n  1:10   re-export\n", ts)
        self.assertIn("src/app.ts\n  1:10   import\n  2:26   extends\n  3:17   type\n  3:29   call\n", ts)
        rust = _run({
            "src/lib.rs": "pub mod shot;\npub mod run;\npub use shot::Camera;\n",
            "src/shot.rs": "pub struct Camera;\npub trait Lens {}\nimpl Camera { pub fn take(&self) {} }\n",
            "src/run.rs": (
                "use crate::Camera;\n"
                "use crate::shot::Lens;\n"
                "struct Wide;\n"
                "impl Lens for Wide {}\n"
                "fn go(c: &Camera) { c.take(); let d = Camera; d.take(); }\n"
            ),
        }, ["usages", "src/shot.rs::Camera"], cargo=True)
        self.assertIn("src/lib.rs\n  3:15   re-export\n", rust)
        self.assertIn("src/run.rs\n  1:12   import\n  5:11   type\n  5:39   reference\n", rust)
        lens = _run({
            "src/lib.rs": "pub mod shot;\npub mod run;\n",
            "src/shot.rs": "pub trait Lens {}\n",
            "src/run.rs": "use crate::shot::Lens;\nstruct Wide;\nimpl Lens for Wide {}\n",
        }, ["usages", "src/shot.rs::Lens"], cargo=True)
        self.assertIn("src/run.rs\n  1:18   import\n  3:6   extends\n", lens)

    def test_the_answer_stops_at_200_uses_unless_all(self) -> None:
        """/* REQ-CS-067: `usages` lists at most 200 uses and says how many it left out; --all lists every one. */"""
        files = {"lib/core.py": "def f(): ...\n", "lib/__init__.py": ""}
        for index in range(30):
            files[f"lib/user{index:02d}.py"] = "from lib.core import f\n" + "f()\n" * 9
        limited = _run(files, ["usages", "lib/core.py::f"])
        self.assertIn("300 uses in 30 files", limited)
        self.assertIn("... 100 more uses: rerun with --all", limited)
        self.assertEqual(200, limited.count("   call\n") + limited.count("   import\n"))
        everything = _run(files, ["usages", "lib/core.py::f", "--all"])
        self.assertEqual(300, everything.count("   call\n") + everything.count("   import\n"))


if __name__ == "__main__":
    unittest.main()
