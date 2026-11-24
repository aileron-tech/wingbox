"""Uses of C and C++ functions and methods that a check against the compiler found missing, one test per cause."""
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
    "native/include/depot.h": (
        "#ifndef DEPOT_H\n#define DEPOT_H\n#define DEPOT_API\n#define DEPOT_EXPORT(declaration) static declaration\n"
        "struct heap { int size; };\n"
        "DEPOT_EXPORT(void heap_put(struct heap* heap, int value));\n"
        "DEPOT_EXPORT(void heap_put(struct heap* heap, int value)) { heap->size += value; }\n"
        "DEPOT_API const char* depot_name(int id);\n"
        "void depot_free(void* pointer);\n"
        "typedef void (*depot_cb)(int);\n"
        "void depot_later(depot_cb cb);\n"
        "#endif\n"
    ),
    # The grammar loses track here: two branches open the same block.
    "native/src/poll.c": (
        '#include "depot.h"\n'
        "static int ready(int fd) {\n"
        "#ifdef DEPOT_FAST\n  if (fd > 2) {\n#else\n  if (fd > 0) {\n#endif\n    return 1;\n  }\n  return 0;\n}\n"
        "\n"
        "static void on_poll(int status) {\n  if (status) depot_later(on_poll);\n}\n"
        "\n"
        "int poll_start(int fd) {\n  struct heap heap;\n  heap_put(&heap, fd);\n  depot_later(on_poll);\n  return ready(fd);\n}\n"
        "\n"
        "int poll_stop(void* state) {\n  int rc = ready(1);\n  if (rc) goto error;\n  return 0;\nerror:\n#ifndef DEPOT_FAST\n  depot_free(state);\n#endif\n  return rc;\n}\n"
        "const char* poll_name(void) { return depot_name(1); }\n"
    ),
    # C++: a class with an export macro, a class nested in it, methods called without an object.
    "native/include/crate.h": (
        "#pragma once\n#define CRATE_API\n"
        "class CRATE_API Crate {\n public:\n"
        "  class CRATE_API Handler {\n   public:\n    virtual void Put(int value) = 0;\n  };\n"
        "  Crate();\n  int Size() const;\n  void Add(int value);\n  static Crate* Default();\n"
        "  Crate& operator=(const Crate& other);\n"
        " private:\n  void Grow();\n  int size_;\n};\n"
        "class Shelf {\n public:\n  int Count() const { return Width() + 1; }\n  int Width() const;\n};\n"
    ),
    "native/src/crate.cpp": (
        '#include "crate.h"\n'
        "Crate::Crate() : size_(0) { Grow(); }\n"
        "int Crate::Size() const { return size_; }\n"
        "void Crate::Add(int value) { size_ += value; this->Grow(); }\n"
        "void Crate::Grow() { size_ += 1; }\n"
        "Crate& Crate::operator=(const Crate& other) { size_ = other.size_; Grow(); return *this; }\n"
        "Crate* Crate::Default() { static Crate crate; return &crate; }\n"
        "int Shelf::Width() const { return 2; }\n"
    ),
    "native/tests/test_crate.cpp": (
        '#include "crate.h"\n'
        "#define TEST_F(fixture, name) struct fixture##name : fixture { void Body(); }; void fixture##name::Body()\n"
        "struct CrateTest { int Fill(int n) { return n; } };\n"
        "TEST_F(CrateTest, Fills) {\n  Fill(3);\n}\n"
        "int by_object() {\n  Crate crate;\n  crate.Add(1);\n  struct Local { static int Twice(int x) { return 2 * x; } };\n"
        "  return Local::Twice(crate.Size()) + Crate::Default()->Size();\n}\n"
    ),
    # More shapes of real code: a base class after the macro, attribute macros after the parameters, a friend
    # function, a method named without a call from a nested class, a call in brackets.
    "native/include/gate.h": (
        "#pragma once\n#define GATE_API\n#define GATE_LOCKED(mutex)\n"
        "struct GateBase { int base; };\n"
        "GATE_API GateBase MakeBase();\n"
        "class GATE_API Gate : public GateBase {\n public:\n"
        "  void Open()\n      GATE_LOCKED(mutex_) {\n    Count();\n  }\n"
        "  void Trace() GATE_LOCKED(mutex_);\n"
        "  int Count() const { return 1; }\n"
        "  int mutex_;\n};\n"
        "class Lever {\n public:\n"
        "  friend int lever_force(const Lever& lever) { return lever.arm_; }\n"
        "  static const char* Describe() { return \"lever\"; }\n"
        "  class Printer {\n   public:\n    const char* (*Pick())() { return Describe; }\n  };\n"
        "  int Pull() { return lever_force(*this); }\n"
        "  int arm_;\n};\n"
    ),
    "native/src/gate.cpp": (
        '#include "gate.h"\n'
        "int use_gate() {\n  Gate gate;\n  gate.Trace();\n  (gate.Open)();\n  GateBase base = MakeBase();\n  return base.base;\n}\n"
    ),
    # A class the grammar cuts short (a macro before a destructor), and one it takes for a return type.
    "native/include/buffer.h": (
        "#pragma once\n#define BUF_CONSTEXPR\n#define BUF_BEGIN\n"
        "BUF_BEGIN\n"
        "class Buffer {\n public:\n"
        "  BUF_CONSTEXPR ~Buffer() { release(); }\n"
        "  BUF_CONSTEXPR auto capacity() const -> int { return limit(); }\n"
        "  int limit() const { return 8; }\n"
        " private:\n  void release();\n};\n"
        "class Reader {\n public:\n  int read(Buffer& buffer) { return buffer.capacity(); }\n};\n"
    ),
    "tools/report.py": "class Crate:\n    def Add(self, value):\n        return value\n\n\ndef run(crate):\n    return crate.Add(1)\n",
}


def _write(root: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text, encoding="utf-8")


def _run(root: Path, *args: str) -> subprocess.CompletedProcess:
    environment = dict(os.environ, PYTHONPATH=str(SOURCE) + os.pathsep + os.environ.get("PYTHONPATH", ""), WINGBOX_JOBS="1")
    return subprocess.run([sys.executable, "-m", "wingbox", *args], capture_output=True, env=environment, cwd=str(root), timeout=180, text=True)


class CompleteUsagesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        folder = tempfile.TemporaryDirectory()
        cls.addClassCleanup(folder.cleanup)
        cls.root = Path(os.path.realpath(folder.name))
        subprocess.run(["git", "init", "-q", str(cls.root)], check=True)
        _write(cls.root, PROJECT)
        _run(cls.root, "build", ".")

    def places(self, symbol: str) -> set[str]:
        answer = json.loads(_run(self.root, "usages", symbol, "--json").stdout)["usages"]
        self.assertNotIn("error", answer, answer)
        return {f"{row['file']}:{row['line']}" for row in answer["rows"]}

    def test_functions_of_a_file_the_grammar_loses_track_of(self) -> None:
        """/* REQ-CPP-019: where the grammar cannot read a stretch of a C or C++ file (two preprocessor branches that open the same block), the functions defined below are still found, by their heading at the margin, and so are their uses; a function that names itself in its own body (it hands itself on as a callback) is a use of that function; a call that follows a label and a preprocessor line, which the grammar reads as a declaration, is a call. */"""
        self.assertEqual({"native/src/poll.c:21", "native/src/poll.c:25"}, self.places("native/src/poll.c::ready"))
        self.assertEqual({"native/src/poll.c:14", "native/src/poll.c:20"}, self.places("native/src/poll.c::on_poll"))
        self.assertEqual({"native/src/poll.c:30"}, self.places("depot_free"))

    def test_functions_declared_through_a_macro(self) -> None:
        """/* REQ-CPP-020: a function declared or defined inside a macro call (`DEPOT_EXPORT(void heap_put(struct heap* heap, int value)) { ... }`) is a function of its real name, and a prototype that starts with a macro and returns a pointer (`DEPOT_API const char* depot_name(int id);`) is a declaration: their calls are uses. */"""
        self.assertEqual({"native/src/poll.c:19"}, self.places("heap_put"))
        self.assertEqual({"native/src/poll.c:34"}, self.places("depot_name"))

    def test_methods_of_a_class_with_an_export_macro(self) -> None:
        """/* REQ-CPP-021: a class written with a macro between the keyword and its name (`class CRATE_API Crate { ... };`) is a class with its methods, also those declared after a class with a macro nested in it. A call inside the class or inside `Crate::method` without an object, and a call through `this`, is a use of `Crate.method`; so is a call inside a body written after a macro call whose first argument names the class (`TEST_F(CrateTest, Fills) { Fill(3); }`). A class defined inside a function is a class as well. */"""
        self.assertEqual({"native/src/crate.cpp:2", "native/src/crate.cpp:4", "native/src/crate.cpp:6"},
                         {place for place in self.places("native/include/crate.h::Crate.Grow")})
        self.assertIn("native/include/crate.h:20", self.places("native/include/crate.h::Shelf.Width"))
        self.assertIn("native/tests/test_crate.cpp:5", self.places("native/tests/test_crate.cpp::CrateTest.Fill"))
        self.assertIn("native/tests/test_crate.cpp:11", self.places("native/tests/test_crate.cpp::Local.Twice"))
        self.assertIn("native/tests/test_crate.cpp:11", self.places("native/include/crate.h::Crate.Default"))

    def test_a_bare_method_name(self) -> None:
        """/* REQ-CPP-022: `usages <name>` of a name that C or C++ classes declare as a method no longer answers no-match: with one such class it answers for `Class.name`, with several (or a function of the name besides) it lists them as candidates `<file>::<Class>.<name>`. A call on an object whose type is not derived is listed as an unresolved call of the method only from files of C and C++: a call of the same name in a Python file is not. */"""
        self.assertIn("native/tests/test_crate.cpp:5", self.places("Fill"))
        answer = json.loads(_run(self.root, "usages", "Add", "--json").stdout)["usages"]
        add = {f"{row['file']}:{row['line']}" for row in answer.get("rows", [])} if "rows" in answer else None
        if add is None:   # the Python class is a candidate as well: ask for the C++ one
            targets = [candidate["target"] for candidate in answer["candidates"]]
            self.assertIn("native/include/crate.h::Crate.Add", targets)
            add = self.places("native/include/crate.h::Crate.Add")
        self.assertIn("native/tests/test_crate.cpp:9", add)
        self.assertFalse([place for place in add if place.startswith("tools/")], add)

    def test_more_shapes_of_real_code(self) -> None:
        """/* REQ-CPP-023: read as written in real C++: a class with a macro before its name and a base class after it; a method with an attribute macro between its parameters and its body, or after the parameters of its declaration; a prototype whose macro makes the grammar join the return type and the name; a function defined as a friend inside a class (a function of the file, called by its plain name); a method of an outer class named without a call inside a class nested in it; a method called through brackets, `(object.method)()`. A question that names one file of a C or C++ function answers for the function: its prototypes and its definition. */"""
        self.assertIn("native/include/gate.h:10", self.places("native/include/gate.h::Gate.Count"))
        self.assertIn("native/src/gate.cpp:4", self.places("native/include/gate.h::Gate.Trace"))
        self.assertIn("native/src/gate.cpp:5", self.places("native/include/gate.h::Gate.Open"))
        self.assertEqual({"native/src/gate.cpp:6"}, self.places("MakeBase"))
        self.assertEqual({"native/include/gate.h:24"}, self.places("lever_force"))
        self.assertIn("native/include/gate.h:22", self.places("native/include/gate.h::Lever.Describe"))
        self.assertEqual(self.places("native/include/depot.h::depot_free"), self.places("depot_free"))

    def test_methods_of_a_class_the_grammar_cuts_short(self) -> None:
        """/* REQ-CPP-024: in a file where the grammar meets an error, a class keeps the methods that stand between its braces in the text, also where the grammar ends the class early or takes it for the return type of what follows (a macro before a destructor or before `auto`): the methods are methods of the class, calls among them without an object are uses of them, and a class after it is still read. */"""
        self.assertIn("native/include/buffer.h:7", self.places("native/include/buffer.h::Buffer.release"))
        self.assertIn("native/include/buffer.h:8", self.places("native/include/buffer.h::Buffer.limit"))
        self.assertIn("native/include/buffer.h:15", self.places("native/include/buffer.h::Buffer.capacity"))
        self.assertIn("native/include/buffer.h:15", self.places("capacity"))


if __name__ == "__main__":
    unittest.main()
