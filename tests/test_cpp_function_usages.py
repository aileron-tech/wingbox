"""`usages` of C and C++ functions: prototypes behind an export macro, a declaration with its definition."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src"

PROJECT = {
    "native/CMakeLists.txt": "target_include_directories(native PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/include)\n",
    # A C API: every prototype starts with an export macro, inside an extern "C" block.
    "native/include/depot.h": (
        "#ifndef DEPOT_H\n#define DEPOT_H\n#define DEPOT_API __attribute__((visibility(\"default\")))\n"
        "#ifdef __cplusplus\nextern \"C\" {\n#endif\n"
        "typedef struct crate_s { int size; } crate_t;\n"
        "DEPOT_API crate_t crate_init(int size);\n"
        "DEPOT_API int crate_start(crate_t* crate, int delay);\n"
        "DEPOT_API void crate_close(crate_t* crate);\n"
        "int crate_plain(int size);\n"
        "#ifdef __cplusplus\n}\n#endif\n#endif\n"
    ),
    "native/src/crate.c": (
        '#include "depot.h"\n'
        "static int check(int size);\n"
        "crate_t crate_init(int size) { crate_t crate; crate.size = check(size); return crate; }\n"
        "int crate_start(crate_t* crate, int delay) { return crate->size + delay; }\n"
        "void crate_close(crate_t* crate) { crate->size = 0; }\n"
        "int crate_plain(int size) { return crate_start(0, size); }\n"
        "static int check(int size) { return size; }\n"
    ),
    "native/src/loader.c": '#include "depot.h"\nstatic int check(int size) { return size + 1; }\nint load(void) { crate_t crate = crate_init(check(2)); return crate_start(&crate, 1); }\n',
    "native/tests/test_crate.c": '#include "depot.h"\nint main(void) { crate_t crate = crate_init(3); crate_start(&crate, 0); crate_close(&crate); return crate_plain(1); }\n',
}


def _write(root: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text, encoding="utf-8")


def _run(root: Path, *args: str) -> subprocess.CompletedProcess:
    environment = dict(os.environ, PYTHONPATH=str(SOURCE) + os.pathsep + os.environ.get("PYTHONPATH", ""), WINGBOX_JOBS="1")
    return subprocess.run([sys.executable, "-m", "wingbox", *args], capture_output=True, env=environment, cwd=str(root), timeout=180, text=True)


def _places(root: Path, symbol: str) -> tuple[dict, set[str]]:
    answer = json.loads(_run(root, "usages", symbol, "--json").stdout)["usages"]
    return answer, {f"{row['file']}:{row['line']}" for row in answer.get("rows", [])}


class FunctionUsagesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        folder = tempfile.TemporaryDirectory()
        cls.addClassCleanup(folder.cleanup)
        cls.root = Path(os.path.realpath(folder.name))
        subprocess.run(["git", "init", "-q", str(cls.root)], check=True)
        _write(cls.root, PROJECT)
        _run(cls.root, "build", ".")

    def test_a_prototype_behind_an_export_macro_is_a_declaration(self) -> None:
        """/* REQ-CPP-017: a function prototype that starts with a macro (`DEPOT_API int crate_start(...);`, an export or visibility macro, also inside `extern "C"`) is a declaration of its header, as a prototype without one is: the calls in the files that include the header are uses of it, and a question that names the header or the source file answers for the function as a whole. Before, such a function had no declaration in the graph and `usages` of it answered "0 uses". */"""
        answer, places = _places(self.root, "native/include/depot.h::crate_start")
        self.assertEqual("native/include/depot.h", answer["file"])
        # Asked by the header, the answer is for the function: the call in the file that defines it counts too.
        self.assertEqual({"native/src/loader.c:3", "native/tests/test_crate.c:2", "native/src/crate.c:6"}, places)
        _answer, plain = _places(self.root, "native/include/depot.h::crate_plain")
        self.assertIn("native/tests/test_crate.c:2", plain)
        _answer, returned = _places(self.root, "native/include/depot.h::crate_init")
        self.assertTrue({"native/src/loader.c:3", "native/tests/test_crate.c:2"} <= returned, returned)

    def test_a_bare_name_answers_for_the_declaration_and_its_definition(self) -> None:
        """/* REQ-CPP-018: `usages <name>` of a C or C++ function that a header declares and a source file that includes this header defines answers for the function, not with two candidates: the uses of the declaration and of the definition together, each place once, `:at` the definition. A declaration and a definition of one name in one file are one function. Functions of one name in files that share no declaring header stay candidates, and listing them does not fail when two of them are in one file. */"""
        answer, places = _places(self.root, "crate_start")
        self.assertNotIn("candidates", answer)
        self.assertEqual(("native/src/crate.c", 4), (answer["file"], answer["line"]))
        self.assertEqual({"native/src/crate.c:6", "native/src/loader.c:3", "native/tests/test_crate.c:2"}, places)
        compact = _run(self.root, "usages", "crate_close").stdout
        self.assertIn("(usages crate_close fn :at native/src/crate.c:5 :uses 1 :files 1", compact)
        ambiguous = _run(self.root, "usages", "check")
        self.assertEqual(0, ambiguous.returncode, ambiguous.stderr)
        self.assertNotIn("Traceback", ambiguous.stderr)
        self.assertIn("native/src/crate.c", ambiguous.stdout)
        self.assertIn("native/src/loader.c", ambiguous.stdout)
        own, own_places = _places(self.root, "native/src/crate.c::check")
        self.assertEqual({"native/src/crate.c:3"}, own_places)
        flat = _run(self.root, "usages", "check", "--sexp")
        self.assertNotIn("Traceback", flat.stderr)


if __name__ == "__main__":
    unittest.main()
