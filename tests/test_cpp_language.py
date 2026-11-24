"""C and C++: what the parser reads from a file, and how an `#include` finds its file."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.build_code_graph import Ok, build_graph, parse_file
from scripts.lang import language_for_file
from scripts.lang.cpp import CPP_LANGUAGE, parse_cpp
from scripts.lang.interface import FileResult

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src"

HEADER = """
#pragma once
#include <vector>
#include "core/shape.h"
#if defined(WITH_AUDIO)
#  include "audio/mixer.hpp"
#endif

#define MAX_LAYERS 8
#define CLAMP(value, low, high) ((value) < (low) ? (low) : (value))

namespace studio::render {

enum class Mode { Flat, Lit };

struct Vertex { float x; float y; };

using Index = unsigned int;

class Painter : public core::Shape, private Loggable {
public:
    Painter();
    virtual ~Painter();
    void paint(const Vertex& vertex);
    virtual int layers() const = 0;
    static Painter* create();
private:
    int count_;
};

int blend(int left, int right);

}  // namespace studio::render
""".lstrip()

SOURCE_FILE = """
#include "render/painter.h"

namespace studio::render {

void Painter::paint(const Vertex& vertex) { blend(1, 2); }

int blend(int left, int right) { return left + right; }

static int local_helper() { return 0; }

}  // namespace studio::render

int main(int argc, char** argv) { return studio::render::blend(argc, 1); }
""".lstrip()


def _write(root: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text, encoding="utf-8")


def _import_edges(root: Path) -> set[tuple[str, str]]:
    """The import edges of the graph of every C and C++ file under root."""
    results = []
    for directory, _, names in os.walk(root):
        for name in sorted(names):
            if language_for_file(name) is CPP_LANGUAGE:
                result = parse_file((os.path.join(directory, name), "cpp", "fixture"))
                assert result is not None, name
                results.append(result)
    graph = build_graph(results, str(root))
    assert isinstance(graph, Ok), graph
    return {(edge["source"], edge["target"]) for edge in graph.value.edges if edge["type"] == "IMPORTS"}


def _wingbox(root: Path, *args: str) -> str:
    environment = dict(os.environ, PYTHONPATH=str(SOURCE) + os.pathsep + os.environ.get("PYTHONPATH", ""), WINGBOX_JOBS="1")
    done = subprocess.run([sys.executable, "-m", "wingbox", *args], capture_output=True, env=environment, cwd=str(root), timeout=180)
    return done.stdout.decode("utf-8").replace("\\", "/")


class CppParserTest(unittest.TestCase):
    def _parse(self, root: Path, relative_path: str, content: str) -> FileResult:
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return parse_cpp(str(path), content, root.name, path.stat().st_mtime)

    def test_a_header_gives_its_includes_types_functions_and_macros(self) -> None:
        """/* REQ-CPP-001: a C++ header gives its includes as written, quoted ones plain and angle ones in their brackets, also inside a conditional block, each with its line; classes with their methods and base classes, structs, enums, type aliases, function declarations inside namespaces and macros that have a value; an include guard and `#pragma once` give nothing, and a class with a pure virtual method is not reported as an interface with providers: the dependencies of C and C++ files are their includes. */"""
        with tempfile.TemporaryDirectory() as directory:
            result = self._parse(Path(directory), "render/painter.h", HEADER)
        entities = {entity["name"]: entity for entity in result.entities}
        self.assertEqual("cpp", result.language)
        self.assertEqual(["<vector>", "core/shape.h", "audio/mixer.hpp"], result.imports)
        self.assertEqual({"<vector>": [2], "core/shape.h": [3], "audio/mixer.hpp": [5]}, result.import_lines)
        self.assertEqual("class", entities["Painter"]["kind"])
        self.assertEqual(["Painter", "~Painter", "paint", "layers", "create"], entities["Painter"].get("methods", []))
        self.assertEqual(["core::Shape", "Loggable"], entities["Painter"].get("bases", []))
        self.assertEqual("class", entities["Vertex"]["kind"])
        self.assertEqual("enum", entities["Mode"]["kind"])
        self.assertEqual("type", entities["Index"]["kind"])
        self.assertEqual("function", entities["blend"]["kind"])
        self.assertEqual("function", entities["CLAMP"]["kind"])
        self.assertEqual("function", entities["MAX_LAYERS"]["kind"])
        self.assertEqual([], result.exports_abstract)
        self.assertEqual(["core::Shape", "Loggable"], result.extends)

    def test_a_source_file_gives_its_function_definitions(self) -> None:
        """/* REQ-CPP-002: a C++ source file gives the functions it defines, inside a namespace or not, with their lines; a method defined outside its class (`void Painter::paint()`) is the entity `Painter.paint`, the form of every method that has a line of its own. */"""
        with tempfile.TemporaryDirectory() as directory:
            result = self._parse(Path(directory), "render/painter.cpp", SOURCE_FILE)
        entities = {entity["name"]: entity for entity in result.entities}
        self.assertEqual(["render/painter.h"], result.imports)
        self.assertEqual(["Painter.paint", "blend", "local_helper", "main"], [entity["name"] for entity in result.entities])
        self.assertEqual(5, entities["Painter.paint"]["line"])
        self.assertEqual(13, entities["main"]["line"])

    def test_a_c_file_is_read_with_the_same_parser(self) -> None:
        """/* REQ-CPP-003: a `.c` file is a file of the language `c`: its includes, its functions, its `typedef struct` and its structs are read; names that are keywords of C++ only (`new`, `class`) do not stop the parse. */"""
        text = '#include "tape.h"\n#include <stdio.h>\ntypedef struct { int size; } reel_t;\nstruct deck { int new; };\nint wind(reel_t* reel, int class) { return reel->size + class; }\n'
        with tempfile.TemporaryDirectory() as directory:
            result = self._parse(Path(directory), "tape.c", text)
        self.assertEqual("c", result.language)
        self.assertEqual(["tape.h", "<stdio.h>"], result.imports)
        names = {entity["name"]: entity["kind"] for entity in result.entities}
        self.assertEqual("type", names["reel_t"])
        self.assertEqual("class", names["deck"])
        self.assertEqual("function", names["wind"])

    def test_the_language_owns_the_files_of_c_and_cpp(self) -> None:
        """/* REQ-CPP-004: the files `.c .cc .cpp .cxx .h .hh .hpp .hxx .inl .ipp` belong to the language; a module id is the path with its extension, so `painter.h` and `painter.cpp` are two modules; the language server is clangd. */"""
        for name in ("a.c", "a.cc", "a.cpp", "a.cxx", "a.h", "a.hh", "a.hpp", "a.hxx", "a.inl", "a.ipp", "A.CPP"):
            self.assertIs(CPP_LANGUAGE, language_for_file(name), name)
        self.assertIsNot(CPP_LANGUAGE, language_for_file("a.py"))
        self.assertEqual("render/painter.h", CPP_LANGUAGE.normalize_module_id("render/painter.h"))
        self.assertEqual("render/painter.cpp", CPP_LANGUAGE.normalize_module_id("render/painter.cpp"))
        self.assertEqual(("clangd",), CPP_LANGUAGE.lsp_command)
        self.assertEqual("c", CPP_LANGUAGE.output_language_code("c"))
        self.assertEqual("cpp", CPP_LANGUAGE.output_language_code("cpp"))
        self.assertEqual("c", CPP_LANGUAGE.lsp_language_id("tape.c"))
        self.assertEqual("cpp", CPP_LANGUAGE.lsp_language_id("tape.h"))


class CppIncludeTest(unittest.TestCase):
    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(os.path.realpath(folder.name))

    def test_a_quoted_include_is_looked_for_beside_the_file_first(self) -> None:
        """/* REQ-CPP-005: `#include "shape.h"` is the file of that name in the folder of the including file, also when an include folder of the compilation database holds a file of the same name; `#include <shape.h>` is never looked for beside the file. */"""
        _write(self.root, {
            "app/main.cpp": '#include "shape.h"\n#include <shape.h>\nint main() { return 0; }\n',
            "app/shape.h": "struct Local {};\n",
            "include/shape.h": "struct Shared {};\n",
            "compile_commands.json": json.dumps([
                {"directory": str(self.root), "file": "app/main.cpp", "arguments": ["c++", "-Iinclude", "-c", "app/main.cpp"]},
            ]),
        })
        self.assertEqual(
            {("app/main.cpp", "app/shape.h"), ("app/main.cpp", "include/shape.h")}, _import_edges(self.root)
        )

    def test_the_compilation_database_gives_the_include_folders(self) -> None:
        """/* REQ-CPP-006: with a `compile_commands.json` at the root or in `build/`, an include is looked for in the include folders of the entry of the file, in their order: `-I dir`, `-Idir`, `-isystem dir`, `-iquote dir`, `/Idir`, in `arguments` or in `command`, relative to `directory`; a header has no entry and takes the folders of the entries of the nearest folder; a name no folder holds (`<vector>`) is no dependency. */"""
        build = self.root / "build"
        _write(self.root, {
            "studio/src/scene/stage.h": "#include <vector>\nstruct Stage {};\n",
            "studio/src/scene/stage.cpp": '#include "scene/stage.h"\n#include "codec.h"\n',
            "studio/src/play/round.h": '#include "scene/stage.h"\n',
            "studio/src/play/round.cpp": '#include "play/round.h"\n#include <pack/zip.h>\n',
            "studio/third/codec.h": "struct Codec {};\n",
            "studio/old/codec.h": "struct OldCodec {};\n",
            "studio/libs/pack/zip.h": "struct Zip {};\n",
            "build/compile_commands.json": json.dumps([
                {"directory": str(build), "file": "../studio/src/scene/stage.cpp",
                 "command": "c++ -I ../studio/src -iquote ../studio/third -I../studio/old -c ../studio/src/scene/stage.cpp"},
                {"directory": str(build), "file": str(self.root / "studio/src/play/round.cpp"),
                 "arguments": ["cl.exe", "/I" + str(self.root / "studio/src"), "-isystem", "../studio/libs", "/c", "round.cpp"]},
            ]),
        })
        self.assertEqual(
            {
                ("studio/src/scene/stage.cpp", "studio/src/scene/stage.h"),
                ("studio/src/scene/stage.cpp", "studio/third/codec.h"),
                ("studio/src/play/round.h", "studio/src/scene/stage.h"),
                ("studio/src/play/round.cpp", "studio/src/play/round.h"),
                ("studio/src/play/round.cpp", "studio/libs/pack/zip.h"),
            },
            _import_edges(self.root),
        )

    def test_without_a_database_the_cmake_files_and_the_tree_decide(self) -> None:
        """/* REQ-CPP-007: without a compilation database an include is looked for in the folders that the CMake files of the repository name literally (`target_include_directories`, `include_directories`, with `${CMAKE_CURRENT_SOURCE_DIR}`, `${CMAKE_CURRENT_LIST_DIR}`, `${PROJECT_SOURCE_DIR}`, `${CMAKE_SOURCE_DIR}` or a relative path), then, for a quoted name, as the one file of the repository whose path ends with the written path; a path that two files end with is no dependency, nor is a name in angle brackets or the name of a header of the C or C++ standard library (`<clock.h>`, `"math.h"`) that only the tree would find. */"""
        _write(self.root, {
            "CMakeLists.txt": "cmake_minimum_required(VERSION 3.20)\nproject(atelier)\nadd_subdirectory(studio)\n",
            "studio/CMakeLists.txt": "add_library(studio src/scene/stage.cpp)\n"
                                     "target_include_directories(studio PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/src\n    PRIVATE \"${PROJECT_SOURCE_DIR}/studio/third\")\n",
            "studio/src/scene/stage.h": "struct Stage {};\n",
            "studio/src/scene/stage.cpp": '#include "scene/stage.h"\n#include "codec.h"\n#include "pack/zip.h"\n#include "twice.h"\n#include <clock.h>\n#include "math.h"\n',
            "util/clock.h": "struct Clock {};\n",
            "util/math.h": "struct Vector {};\n",
            "studio/third/codec.h": "struct Codec {};\n",
            "tools/probe/codec.h": "struct ProbeCodec {};\n",
            "libs/pack/zip.h": "struct Zip {};\n",
            "one/twice.h": "struct A {};\n",
            "two/twice.h": "struct B {};\n",
        })
        self.assertEqual(
            {
                ("studio/src/scene/stage.cpp", "studio/src/scene/stage.h"),
                ("studio/src/scene/stage.cpp", "studio/third/codec.h"),
                ("studio/src/scene/stage.cpp", "libs/pack/zip.h"),
            },
            _import_edges(self.root),
        )

    def test_impact_of_a_header_names_the_files_that_include_it(self) -> None:
        """/* REQ-CPP-008: `wingbox impact <header> 2 --json` in a repository of C++ and Python lists the files that include the header at depth 1 and the files that include those at depth 2, each `via` `imports`; the header and the source file of the same stem are two rows; the build counts the files as `cpp`. */"""
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        _write(self.root, {
            "studio/CMakeLists.txt": "target_include_directories(studio PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/src)\n",
            "studio/src/scene/stage.h": "struct Stage { int run(); };\n",
            "studio/src/scene/stage.cpp": '#include "scene/stage.h"\nint Stage::run() { return 0; }\n',
            "studio/src/play/round.h": '#include "scene/stage.h"\nint play(Stage& stage);\n',
            "studio/src/play/round.cpp": '#include "play/round.h"\nint play(Stage& stage) { return stage.run(); }\n',
            "studio/src/main.cpp": '#include "play/round.h"\nint main() { Stage stage; return play(stage); }\n',
            "tools/report.py": "def report():\n    return 0\n",
        })
        build = subprocess.run(
            [sys.executable, "-m", "wingbox", "build", "."], capture_output=True, cwd=str(self.root), timeout=180,
            env=dict(os.environ, PYTHONPATH=str(SOURCE) + os.pathsep + os.environ.get("PYTHONPATH", ""), WINGBOX_JOBS="1"),
        )
        self.assertIn("5 cpp", build.stderr.decode("utf-8") + build.stdout.decode("utf-8"))
        answer = json.loads(_wingbox(self.root, "impact", "studio/src/scene/stage.h", "2", "--json"))
        rows = {row["file"]: (row["depth"], row["via"]) for row in answer["impact"][0]["rows"]}
        self.assertEqual(
            {
                "studio/src/scene/stage.cpp": (1, "imports"),
                "studio/src/play/round.h": (1, "imports"),
                "studio/src/play/round.cpp": (2, "imports"),
                "studio/src/main.cpp": (2, "imports"),
            },
            rows,
        )

    def test_a_base_class_adds_no_dependency_beyond_the_include(self) -> None:
        """/* REQ-CPP-013: a class that derives from a class of another file depends on that file through its include only: a file that names a base class it does not include (a class of the same name declared elsewhere) gets no edge, and the graph of C and C++ files has no edge kind but the includes. */"""
        _write(self.root, {
            "shapes/shape.h": "class Shape { public: virtual int area() const = 0; };\n",
            "shapes/square.h": '#include "shape.h"\nclass Square : public Shape { public: int area() const override; };\n',
            "other/round.h": "class Shape;\nclass Round : public Shape { public: int area() const; };\n",
        })
        results = [parse_file((str(self.root / name), "cpp", "fixture")) for name in ("shapes/shape.h", "shapes/square.h", "other/round.h")]
        graph = build_graph(results, str(self.root))
        self.assertIsInstance(graph, Ok)
        module_edges = {(edge["source"], edge["target"], edge["type"]) for edge in graph.value.edges if "::" not in edge["target"]}
        self.assertEqual({("shapes/square.h", "shapes/shape.h", "IMPORTS")}, module_edges)


USAGE_FILES = {
    "CMakeLists.txt": "include_directories(${CMAKE_CURRENT_SOURCE_DIR}/src)\n",
    "src/render/painter.h": (
        "#pragma once\n"
        "enum class Mode { Flat, Lit };\n"
        "struct Vertex { float x; };\n"
        "class Painter {\n"
        "public:\n"
        "    static Painter* create();\n"
        "    void paint(const Vertex& vertex);\n"
        "};\n"
        "int blend(int left, int right);\n"
        "#define MAX_LAYERS 8\n"
    ),
    "src/render/painter.cpp": (
        '#include "render/painter.h"\n'
        "Painter* Painter::create() { return nullptr; }\n"
        "void Painter::paint(const Vertex& vertex) { (void)vertex; }\n"
        "int blend(int left, int right) { return left + right; }\n"
    ),
    "src/scene/scene.h": '#pragma once\n#include "render/painter.h"\nint draw(Mode mode);\n',
    "src/scene/scene.cpp": (
        '#include "scene/scene.h"\n'
        "int draw(Mode mode) {\n"
        "    Vertex corner{1.0f};\n"
        "    Painter* painter = Painter::create();\n"
        "    painter->paint(corner);\n"
        "    if (mode == Mode::Lit) { return blend(1, MAX_LAYERS); }\n"
        "    return 0;\n"
        "}\n"
    ),
    "src/tools/shadow.cpp": (
        "int local_only(int blend) {\n"
        "    int Vertex = blend + 1;\n"
        "    return Vertex;\n"
        "}\n"
    ),
}


class CppUsagesTest(unittest.TestCase):
    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(os.path.realpath(folder.name))
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        _write(self.root, USAGE_FILES)
        _wingbox(self.root, "build", ".")

    def _sites(self, target: str) -> list[tuple[str, int, str]]:
        answer = json.loads(_wingbox(self.root, "usages", target, "--json"))
        self.assertIn("usages", answer, answer)
        return [(row["file"], row["line"], row["kind"]) for row in answer["usages"]["rows"]]

    def test_usages_of_a_name_follow_the_includes(self) -> None:
        """/* REQ-CPP-009: `wingbox usages <header>::<name>` lists, with their lines, the uses of a function, a type and a macro of a header in the header itself and in the files that include it directly or through another header: a call is `call`, a type is `type`; a file that does not include the header and has a parameter or a local variable of the same name is not listed. */"""
        self.assertEqual([("src/scene/scene.cpp", 6, "call")], self._sites("src/render/painter.h::blend"))
        self.assertEqual(
            [("src/render/painter.cpp", 3, "type"), ("src/render/painter.h", 7, "type"), ("src/scene/scene.cpp", 3, "type")],
            self._sites("src/render/painter.h::Vertex"),
        )
        self.assertEqual([("src/scene/scene.cpp", 6, "reference")], self._sites("src/render/painter.h::MAX_LAYERS"))
        self.assertEqual(
            [("src/scene/scene.cpp", 2, "type"), ("src/scene/scene.cpp", 6, "reference"), ("src/scene/scene.h", 3, "type")],
            self._sites("src/render/painter.h::Mode"),
        )

    def test_usages_of_a_member_name_its_class_or_stay_unresolved(self) -> None:
        """/* REQ-CPP-010: a member written with its class or enum (`Painter::create()`, `Mode::Lit`) is a use of `Painter.create` and of `Mode.Lit`; a method called on an object (`painter->paint()`) is listed as an unresolved call of `Painter.paint`, because the type of the object is not read. */"""
        self.assertEqual([("src/scene/scene.cpp", 4, "call")], self._sites("src/render/painter.h::Painter.create"))
        self.assertEqual([("src/scene/scene.cpp", 6, "call")], self._sites("src/render/painter.h::Mode.Lit"))
        self.assertEqual([("src/scene/scene.cpp", 5, "unresolved-call")], self._sites("src/render/painter.h::Painter.paint"))

    def test_a_function_two_reachable_headers_declare_is_one_function(self) -> None:
        """/* REQ-CPP-011: when two files that a file reaches through its includes both declare a function (the same prototype in two headers, or the prototype in a header and the definition in a source file that is included), a call in that file is a use of that function: it is listed under the header that comes first by path, and `usages <name>` lists it once with the uses of the other declarations. A name of another kind that two reachable headers both define (a type) stays attributed to neither. Until 0.19.0 the function was attributed to neither as well, and its calls were missing from every answer. */"""
        _write(self.root, {
            "src/audio/mixer.h": "#pragma once\nint volume(int channel);\nstruct Level { int value; };\n",
            "src/audio/legacy.h": "#pragma once\nint volume(int channel);\nstruct Level { int value; };\n",
            "src/audio/both.cpp": '#include "audio/mixer.h"\n#include "audio/legacy.h"\nint loud() { Level level; return volume(1); }\n',
            "src/audio/one.cpp": '#include "audio/mixer.h"\nint quiet() { return volume(0); }\n',
        })
        _wingbox(self.root, "build", ".")
        self.assertEqual([("src/audio/one.cpp", 2, "call")], self._sites("src/audio/mixer.h::volume"))
        self.assertEqual([("src/audio/both.cpp", 3, "call")], self._sites("src/audio/legacy.h::volume"))
        self.assertEqual([("src/audio/both.cpp", 3, "call"), ("src/audio/one.cpp", 2, "call")], self._sites("volume"))
        self.assertEqual([], [site for site in self._sites("src/audio/mixer.h::Level") if site[0] == "src/audio/both.cpp"])
        self.assertEqual([], [site for site in self._sites("src/audio/legacy.h::Level") if site[0] == "src/audio/both.cpp"])


if __name__ == "__main__":
    unittest.main()
