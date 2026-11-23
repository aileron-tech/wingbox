"""Links that no import states: a file a generator writes and a script another file launches, each
marked as declared or inferred."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts import generation

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src"

# A schema, the generator that reads it, the two files it writes (Python and C++) and their users.
PROJECT = {
    "schema/formats.yaml": "formats: [crate, pallet]\n",
    "tools/make_layouts.py": "def main():\n    return 0\n",
    "depot/__init__.py": "",
    "depot/layouts.py": "CRATE = 1\n",
    "depot/reader.py": "from depot import layouts\n\n\ndef read():\n    return layouts.CRATE\n",
    "depot/report.py": "from depot import reader\n\n\ndef report():\n    return reader.read()\n",
    "native/CMakeLists.txt": "target_include_directories(native PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/src)\n",
    "native/src/layouts.h": "struct CrateHeader { int size; };\n",
    "native/src/crate.cpp": '#include "layouts.h"\nint size(CrateHeader header) { return header.size; }\n',
}
DECLARED = (
    "generators:\n"
    "  - run: tools/make_layouts.py\n"
    "    inputs: [schema/formats.yaml]\n"
    "    outputs: [depot/layouts.py, native/src/layouts.h]\n"
)


def _write(root: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text, encoding="utf-8")


def _run(root: Path, *args: str) -> subprocess.CompletedProcess:
    environment = dict(os.environ, PYTHONPATH=str(SOURCE) + os.pathsep + os.environ.get("PYTHONPATH", ""), WINGBOX_JOBS="1")
    return subprocess.run([sys.executable, "-m", "wingbox", *args], capture_output=True, env=environment, cwd=str(root), timeout=180, text=True)


def _rows(root: Path, target: str) -> dict[str, tuple[int, str, str]]:
    """file -> (depth, via, basis) of every row of `impact <target> 2 --json`."""
    answer = json.loads(_run(root, "impact", target, "2", "--json").stdout)["impact"][0]
    assert "rows" in answer, answer
    return {row["file"]: (row["depth"], row["via"], row.get("basis", "source")) for row in answer["rows"]}


class GenerationLinksTest(unittest.TestCase):
    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(os.path.realpath(folder.name))
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)

    def test_a_declared_generator_links_its_outputs(self) -> None:
        """/* REQ-CS-151: with `generators:` in `.wingbox/config.yaml` (a list of `run`, `inputs`, `outputs`, paths from the repository root), the files of `outputs` depend on the file of `run`: `wingbox impact <generator> 2 --json` lists them at depth 1 with `via` `generated` and `basis` `declared`, and the files that import or include them at depth 2 with `via` `imports` and no `basis` key (the code states the edge); the compact form writes `:via generated :basis declared`. */"""
        _write(self.root, {**PROJECT, ".wingbox/config.yaml": DECLARED})
        build = _run(self.root, "build", ".")
        self.assertEqual(0, build.returncode, build.stderr)
        self.assertEqual(
            {
                "depot/layouts.py": (1, "generated", "declared"),
                "native/src/layouts.h": (1, "generated", "declared"),
                "depot/reader.py": (2, "imports", "source"),
                "native/src/crate.cpp": (2, "imports", "source"),
            },
            _rows(self.root, "tools/make_layouts.py"),
        )
        compact = _run(self.root, "impact", "tools/make_layouts.py").stdout
        self.assertIn("depot/layouts.py :via generated :basis declared", compact)

    def test_the_input_of_a_generator_has_an_impact_although_it_is_not_in_the_graph(self) -> None:
        """/* REQ-CS-152: a file that is no source of a parsed language and that a generator reads (`inputs`) answers `wingbox impact`: at depth 1 the generated files (`via` `generated`) and the generator (`via` `reads`), at depth 2 the files that depend on those; without a generation link the same file answers `not-in-graph`. */"""
        _write(self.root, {**PROJECT, ".wingbox/config.yaml": DECLARED})
        _run(self.root, "build", ".")
        self.assertEqual(
            {
                "depot/layouts.py": (1, "generated", "declared"),
                "native/src/layouts.h": (1, "generated", "declared"),
                "tools/make_layouts.py": (1, "reads", "declared"),
                "depot/reader.py": (2, "imports", "source"),
                "native/src/crate.cpp": (2, "imports", "source"),
            },
            _rows(self.root, "schema/formats.yaml"),
        )
        (self.root / ".wingbox" / "config.yaml").unlink()
        _run(self.root, "build", ".")
        self.assertIn("(error not-in-graph schema/formats.yaml :reason not-source)", _run(self.root, "impact", "schema/formats.yaml").stdout)

    def test_a_marker_at_the_head_of_a_generated_file_gives_inferred_links(self) -> None:
        """/* REQ-CS-153: a line among the first ten of a file that says `Generated by <path>` (and `edit <path>` for the input), in a docstring or a comment of any parsed language, links the file to that generator and input with `basis` `inferred`, with no configuration; a declaration of the same pair in the config file makes it `declared`; a marker that names no file of the repository gives no link and a warning of the build. */"""
        _write(self.root, {
            **PROJECT,
            "depot/layouts.py": '"""Generated by tools/make_layouts.py; edit schema/formats.yaml."""\nCRATE = 1\n',
            "native/src/layouts.h": "// Generated by tools/make_layouts.py; edit schema/formats.yaml.\nstruct CrateHeader { int size; };\n",
            "depot/stale.py": "# Generated by tools/gone.py\nVALUE = 1\n",
        })
        build = _run(self.root, "build", ".")
        self.assertIn("depot/stale.py line 1 names tools/gone.py as the generator: no such file in the repository", build.stderr)
        rows = _rows(self.root, "tools/make_layouts.py")
        self.assertEqual((1, "generated", "inferred"), rows["depot/layouts.py"])
        self.assertEqual((1, "generated", "inferred"), rows["native/src/layouts.h"])
        self.assertEqual((1, "reads", "inferred"), _rows(self.root, "schema/formats.yaml")["tools/make_layouts.py"])
        _write(self.root, {".wingbox/config.yaml": DECLARED})
        _run(self.root, "build", ".")
        self.assertEqual((1, "generated", "declared"), _rows(self.root, "tools/make_layouts.py")["depot/layouts.py"])

    def test_a_declared_path_that_does_not_exist_is_unresolved(self) -> None:
        """/* REQ-CS-154: a path of `generators:` that names no file of the repository is reported by the build (`... names <path> as an output: no such file in the repository`) and kept in the graph as `(generation-unresolved "<path>" :role output :declared ...)`; the other paths of the entry still link. An entry that is not `{run, inputs, outputs}` is one warning and declares nothing. */"""
        _write(self.root, {**PROJECT, ".wingbox/config.yaml": DECLARED.replace("native/src/layouts.h", "native/src/missing.h")})
        build = _run(self.root, "build", ".")
        self.assertIn("names native/src/missing.h as an output: no such file in the repository", build.stderr)
        graph = (self.root / ".wingbox" / "index.sexp").read_text(encoding="utf-8")
        self.assertIn('(generation-unresolved "native/src/missing.h" :role output :declared', graph)
        self.assertEqual((1, "generated", "declared"), _rows(self.root, "tools/make_layouts.py")["depot/layouts.py"])
        _write(self.root, {".wingbox/config.yaml": "generators:\n  - run: tools/make_layouts.py\n    writes: [depot/layouts.py]\n"})
        build = _run(self.root, "build", ".")
        self.assertIn("generators[1]: expected `outputs`", build.stderr)
        self.assertNotIn("depot/layouts.py", _rows(self.root, "tools/make_layouts.py"))

    def test_a_custom_command_of_cmake_gives_inferred_links(self) -> None:
        """/* REQ-CS-155: `add_custom_command(OUTPUT <file> COMMAND ... <script> ... DEPENDS <file>)` of a CMake file, with `${CMAKE_CURRENT_SOURCE_DIR}`, `${CMAKE_SOURCE_DIR}`, `${PROJECT_SOURCE_DIR}` or a relative path, links an output that is a file of the repository to the script (`generated`, `inferred`) and to the files of DEPENDS; an output in the build folder links nothing. */"""
        _write(self.root, {
            **PROJECT,
            "CMakeLists.txt": "project(depot)\nadd_subdirectory(native)\n",
            "native/CMakeLists.txt": (
                "target_include_directories(native PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/src)\n"
                "add_custom_command(\n"
                "    OUTPUT ${CMAKE_CURRENT_SOURCE_DIR}/src/layouts.h ${CMAKE_CURRENT_BINARY_DIR}/stamp.h\n"
                "    COMMAND ${Python3_EXECUTABLE} ${CMAKE_SOURCE_DIR}/tools/make_layouts.py --cpp\n"
                "    DEPENDS ${PROJECT_SOURCE_DIR}/schema/formats.yaml\n"
                "    COMMENT \"layouts\")\n"
            ),
        })
        _run(self.root, "build", ".")
        self.assertEqual(
            {"native/src/layouts.h": (1, "generated", "inferred"), "native/src/crate.cpp": (2, "imports", "source")},
            _rows(self.root, "tools/make_layouts.py"),
        )
        self.assertEqual((1, "generated", "inferred"), _rows(self.root, "schema/formats.yaml")["native/src/layouts.h"])

    def test_a_script_launched_as_a_process_is_a_dependency(self) -> None:
        """/* REQ-PY-024: a Python file that launches a script of the repository by a literal path (`subprocess.run([sys.executable, "tools/report.py"])`, `check_call`, `Popen`, `os.system("python tools/report.py")`, the literal tail of `os.path.join(HERE, "tools", "report.py")` or of `ROOT / "tools" / "report.py"`) depends on it: `wingbox impact <script>` lists the launcher with `via` `runs` and `basis` `inferred`; a path that names no file of the graph, and a call of another function, give nothing. */"""
        _write(self.root, {
            "tools/report.py": "def main():\n    return 0\n",
            "tools/audit.py": "def main():\n    return 0\n",
            "tools/sweep.py": "def main():\n    return 0\n",
            "run_all.py": (
                "import os\nimport subprocess\nimport sys\nfrom pathlib import Path\n\n"
                "HERE = os.path.dirname(os.path.abspath(__file__))\nROOT = Path(__file__).parent\n\n"
                "def main():\n"
                "    subprocess.run([sys.executable, \"tools/report.py\", \"--all\"], check=True)\n"
                "    subprocess.check_call([sys.executable, os.path.join(HERE, \"tools\", \"audit.py\")])\n"
                "    os.system(\"python tools/sweep.py --quick\")\n"
                "    subprocess.run([sys.executable, \"tools/gone.py\"])\n"
                "    print(\"tools/report.py\")\n"
            ),
            "tools/nightly.py": "import subprocess\nfrom pathlib import Path\n\nROOT = Path(__file__).parent.parent\n\n"
                                "def main():\n    subprocess.Popen([\"python3\", str(ROOT / \"tools\" / \"report.py\")])\n",
        })
        _run(self.root, "build", ".")
        self.assertEqual(
            {"run_all.py": (1, "runs", "inferred"), "tools/nightly.py": (1, "runs", "inferred")},
            _rows(self.root, "tools/report.py"),
        )
        self.assertEqual({"run_all.py": (1, "runs", "inferred")}, _rows(self.root, "tools/audit.py"))
        self.assertEqual({"run_all.py": (1, "runs", "inferred")}, _rows(self.root, "tools/sweep.py"))

    def test_an_include_found_without_the_compiler_folders_is_inferred(self) -> None:
        """/* REQ-CPP-012: an include found through the include folders of the CMake files, or as the one file whose path ends with the name, is an edge with `basis` `inferred`; an include found beside the including file or in an include folder of `compile_commands.json` has no `basis`. */"""
        _write(self.root, {
            "native/CMakeLists.txt": "target_include_directories(native PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/src)\n",
            "native/src/scene/stage.h": "struct Stage {};\n",
            "native/src/scene/stage.cpp": '#include "stage.h"\n',
            "native/src/play/round.cpp": '#include "scene/stage.h"\n',
        })
        _run(self.root, "build", ".")
        self.assertEqual(
            {"native/src/scene/stage.cpp": (1, "imports", "source"), "native/src/play/round.cpp": (1, "imports", "inferred")},
            _rows(self.root, "native/src/scene/stage.h"),
        )
        _write(self.root, {"compile_commands.json": json.dumps([
            {"directory": str(self.root), "file": "native/src/play/round.cpp", "arguments": ["c++", "-Inative/src", "-c", "native/src/play/round.cpp"]},
        ])})
        _run(self.root, "build", ".")
        self.assertEqual((1, "imports", "source"), _rows(self.root, "native/src/scene/stage.h")["native/src/play/round.cpp"])

    def test_markers_are_read_from_the_head_only(self) -> None:
        """/* REQ-CS-156: a generation marker is read from the first ten lines of a file and needs a line that starts, after the comment signs, with `generated by` (or `with`, `using`; `This file is`, `auto-` before it) and a path; `edit <path>` without such a line, a path in line eleven, a sentence without a path and a sentence that only contains the words (`Decodes the blocks generated by block_builder.cc`) give no marker. */"""
        self.assertEqual(
            [{"role": "by", "path": "tools/make_layouts.py", "line": 1}, {"role": "from", "path": "schema/formats.yaml", "line": 1}],
            generation.read_markers('"""Generated by tools/make_layouts.py; edit schema/formats.yaml."""\n'),
        )
        self.assertEqual([{"role": "by", "path": "gen.py", "line": 2}], generation.read_markers("// Copyright\n// AUTO-GENERATED WITH gen.py, DO NOT EDIT.\n"))
        self.assertEqual([], generation.read_markers("# edit schema/formats.yaml to change this\n"))
        self.assertEqual([], generation.read_markers("\n" * 10 + "# Generated by tools/make_layouts.py\n"))
        self.assertEqual([], generation.read_markers("# This file is generated by hand, with care.\n"))
        self.assertEqual([], generation.read_markers("// Decodes the blocks generated by block_builder.cc.\n"))
        self.assertEqual([{"role": "by", "path": "tools/gen.py", "line": 1}], generation.read_markers("/* This file was automatically generated by tools/gen.py */\n"))


if __name__ == "__main__":
    unittest.main()
