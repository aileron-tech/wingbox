"""Layers a project names itself in .wingbox/config.yaml, and `wingbox init --from-folders`, which writes
them from the order of the folders of a repository that fits no layer style."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src"

# core <- store <- shop <- app. shop uses store twice; store/catalog.py imports shop.cart once, against the order.
FILES = {
    "core/__init__.py": "", "store/__init__.py": "", "shop/__init__.py": "", "app/__init__.py": "",
    "core/base.py": "VALUE = 1\n",
    "core/test_base.py": "import shop.cart\n\n\ndef test_value():\n    assert shop.cart\n",
    "store/catalog.py": "import core.base\nimport shop.cart\n",
    "shop/cart.py": "import store.catalog\nimport core.base\n",
    "shop/order.py": "import store.catalog\nimport shop.cart\n",
    "app/main.py": "import shop.order\nimport store.catalog\nimport core.base\n",
}
ORDERED = """parts:
  .:
    style: declared
    order:
      - core: [core]
      - store: [store]
      - sales: [shop, app]
"""


def _write(root: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text, encoding="utf-8")


def _run(root: Path, *args: str) -> subprocess.CompletedProcess:
    environment = dict(os.environ, PYTHONPATH=str(SOURCE) + os.pathsep + os.environ.get("PYTHONPATH", ""), WINGBOX_JOBS="1")
    return subprocess.run([sys.executable, "-m", "wingbox", *args], capture_output=True, env=environment, cwd=str(root), timeout=180, text=True)


def _project(test: unittest.TestCase, files: dict[str, str]) -> Path:
    folder = tempfile.TemporaryDirectory()
    test.addCleanup(folder.cleanup)
    root = Path(os.path.realpath(folder.name))
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    _write(root, files)
    return root


class OrderedLayersTest(unittest.TestCase):
    def test_a_part_states_its_layers_in_order(self) -> None:
        """/* REQ-ARCH-057: a part of `.wingbox/config.yaml` with `style: declared` may state `order:`, a list of `<layer>: [<folder>, ...]` entries, lowest layer first, the folders relative to the part (`.` for the files directly in it). A file is in the layer whose folder holds it; a file may depend on its own layer and on the layers below, so an import of a higher layer is a boundary violation with the rule `"<from> -> <to>"`; test files are in no layer. `wingbox layers` names the config file as the rule. An `order` that is no list of such entries, or that names a folder twice, stops the build with a message that names `order`. */"""
        root = _project(self, {**FILES, ".wingbox/config.yaml": ORDERED})
        build = _run(root, "build", ".")
        self.assertEqual(0, build.returncode, build.stderr)
        layers = _run(root, "layers").stdout
        self.assertIn('(layer-member core core/base :lang py :part . :style declared :rule ".wingbox/config.yaml: layer core" :match declared)', layers)
        self.assertIn("(layer-member sales app/main ", layers)
        self.assertIn("(layer-member sales shop/cart ", layers)
        self.assertIn("(layer-member test core/test_base ", layers)
        boundaries = _run(root, "boundaries").stdout
        self.assertIn('(violation store/catalog shop/cart :src-layer store :tgt-layer sales :via imports :lang py :part . :style declared :rule "store -> sales"', boundaries)
        self.assertIn("(boundaries-summary :violations 1 ", boundaries)
        for wrong, said in (("order: core\n", "order"), ("order:\n      - core: [core]\n      - again: [core]\n", "core")):
            broken = _project(self, {**FILES, ".wingbox/config.yaml": "parts:\n  .:\n    style: declared\n    " + wrong})
            answer = _run(broken, "build", ".")
            self.assertNotEqual(0, answer.returncode)
            self.assertIn("order", answer.stderr + answer.stdout)
            self.assertIn(said, answer.stderr + answer.stdout)

    def test_init_writes_the_layers_from_the_order_of_the_folders(self) -> None:
        """/* REQ-ARCH-058: `wingbox init --from-folders <part>` (`.` for the repository) reads the graph, updates the project .gitignore and writes that part into `.wingbox/config.yaml` with `style: declared` and an `order:` of one layer per folder of the part, lowest first: a folder stands below the folders that depend on it; folders that depend on each other are ordered so that the fewest imports go upward. It prints the order and the imports that go upward under it, by pair of folders (`--sexp`: `(layers-from-folders ...)`, `(layer-order ...)`, `(upward ...)`, `(arch-config-written ...)`). A part that has layer rules already is kept unless `--force`; without a graph, and for a part with fewer than two folders, nothing is written and the answer says why. */"""
        root = _project(self, FILES)
        early = _run(root, "init", "--from-folders", ".")
        self.assertEqual(1, early.returncode)
        self.assertIn("wingbox build", early.stdout + early.stderr)
        self.assertFalse((root / ".wingbox" / "config.yaml").exists())
        _run(root, "build", ".")
        answer = _run(root, "init", "--from-folders", ".", "--sexp")
        self.assertEqual(0, answer.returncode, answer.stderr)
        self.assertIn("(layers-from-folders . :folders 4 ", answer.stdout)
        self.assertIn("(layer-order . core store shop app)", answer.stdout)
        self.assertIn("(upward . store shop :imports 1)", answer.stdout)
        self.assertEqual(1, answer.stdout.count("(upward "))
        self.assertIn("(arch-config-written .wingbox/config.yaml)", answer.stdout)
        self.assertEqual(b"/.wingbox/\n", (root / ".gitignore").read_bytes())
        config = (root / ".wingbox" / "config.yaml").read_text(encoding="utf-8")
        self.assertIn("style: declared", config)
        position = [config.index(f"- {layer}: [{layer}]") for layer in ("core", "store", "shop", "app")]
        self.assertEqual(sorted(position), position)
        kept = _run(root, "init", "--from-folders", ".", "--sexp")
        self.assertIn("(arch-config-kept", kept.stdout)
        text = _run(root, "init", "--from-folders", ".", "--force")
        self.assertIn("core, store, shop, app", text.stdout)
        self.assertIn("store -> shop: 1", text.stdout)
        small = _run(root, "init", "--from-folders", "shop")
        self.assertEqual(1, small.returncode)
        self.assertIn("fewer than two folders", small.stdout + small.stderr)

    def test_the_written_layers_are_checked_and_todays_breaks_can_be_accepted(self) -> None:
        """/* REQ-ARCH-059: after `wingbox init --from-folders` and a build, `wingbox boundaries` reports the imports that go upward under the written order as violations; `wingbox align --learn` records them in `.wingbox/baseline.sexp`, after which they are counted as known and a new upward import is the only violation. */"""
        root = _project(self, FILES)
        _run(root, "build", ".")
        self.assertEqual(0, _run(root, "init", "--from-folders", ".").returncode)
        _run(root, "build", ".")
        first = _run(root, "boundaries").stdout
        self.assertIn("(boundaries-summary :violations 1 :known 0 ", first)
        self.assertIn(':rule "store -> shop"', first)
        learned = _run(root, "align", "--learn")
        self.assertEqual(0, learned.returncode, learned.stderr + learned.stdout)
        self.assertIn("store/catalog", (root / ".wingbox" / "baseline.sexp").read_text(encoding="utf-8"))
        _run(root, "build", ".")
        self.assertIn("(boundaries-summary :violations 0 :known 1 ", _run(root, "boundaries").stdout)
        _write(root, {"core/base.py": "import app.main\n\nVALUE = 1\n"})
        _run(root, "build", ".")
        after = _run(root, "boundaries").stdout
        self.assertIn("(boundaries-summary :violations 1 :known 1 ", after)
        self.assertIn(':rule "core -> app"', after)

    def test_a_part_of_c_and_cpp_files_gets_layers(self) -> None:
        """/* REQ-ARCH-060: C and C++ files are source files of a part: `wingbox init --from-folders <folder>` on a folder of C++ code that is no part yet adds it as a part, the build keeps the part, its files get the layers of the order and an include of a higher layer is a violation. Before, a part that held only C or C++ files was dropped and nothing in it was checked. */"""
        root = _project(self, {
            "native/CMakeLists.txt": "target_include_directories(native PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/src)\n",
            "native/src/base/clock.h": "int now();\n",
            "native/src/base/clock.cpp": '#include "base/clock.h"\n#include "scene/stage.h"\nint now() { return stage_size(); }\n',
            "native/src/scene/stage.h": '#include "base/clock.h"\nint stage_size();\n',
            "native/src/scene/stage.cpp": '#include "scene/stage.h"\nint stage_size() { return now(); }\n',
            "native/src/play/round.cpp": '#include "scene/stage.h"\n#include "base/clock.h"\nint play() { return stage_size() + now(); }\n',
            "tools/report.py": "def report():\n    return 0\n",
        })
        _run(root, "build", ".")
        answer = _run(root, "init", "--from-folders", "native/src", "--sexp")
        self.assertEqual(0, answer.returncode, answer.stderr + answer.stdout)
        self.assertIn("(layer-order native/src base scene play)", answer.stdout)
        self.assertIn("(upward native/src base scene :imports 1)", answer.stdout)
        _run(root, "build", ".")
        graph = (root / ".wingbox" / "index.sexp").read_text(encoding="utf-8")
        self.assertIn('(arch-part "native/src" :style declared :state declared', graph)
        boundaries = _run(root, "boundaries").stdout
        self.assertIn(':src-layer base :tgt-layer scene', boundaries)
        self.assertIn("(boundaries-summary :violations 1 ", boundaries)
        self.assertIn("(layer-member play native/src/play/round.cpp ", _run(root, "layers").stdout)


if __name__ == "__main__":
    unittest.main()
