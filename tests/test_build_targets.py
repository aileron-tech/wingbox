"""Build targets and tests read from the CMake files, and which of them a change reaches."""
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
    "CMakeLists.txt": "cmake_minimum_required(VERSION 3.20)\nproject(depot)\nadd_subdirectory(native)\n",
    "native/CMakeLists.txt": (
        "add_library(stage STATIC src/scene/stage.cpp)\n"
        "target_include_directories(stage PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/src)\n"
        "add_executable(round_app\n    src/play/round.cpp\n    src/main.cpp)\n"
        "add_executable(tool_app)\n"
        "target_sources(tool_app PRIVATE src/tool.cpp)\n"
        "add_library(fmt::fmt ALIAS stage)\n"
        "add_executable(${GENERATED_NAME} src/gone.cpp)\n"
        "include(tests/cmake/stage.cmake)\n"
    ),
    "native/tests/cmake/stage.cmake": (
        "add_executable(stage_tests tests/test_stage.cpp\n"
        "    \"${CMAKE_CURRENT_LIST_DIR}/../../src/scene/stage.cpp\")\n"
        "add_test(NAME stage_run COMMAND stage_tests --all)\n"
        "add_test(NAME stage_python\n    COMMAND ${Python3_EXECUTABLE} ${PROJECT_SOURCE_DIR}/tools/check_stage.py --strict\n"
        "    WORKING_DIRECTORY ${PROJECT_SOURCE_DIR})\n"
        "add_test(round_smoke round_app)\n"
        "add_test(NAME tool_run COMMAND ${CMAKE_COMMAND} -E env MODE=1 $<TARGET_FILE:tool_app> --self-test)\n"
        "add_test(NAME ${GENERATED_NAME}_run COMMAND stage_tests)\n"
    ),
    "native/src/scene/stage.h": "struct Stage { int run(); };\n",
    "native/src/scene/stage.cpp": '#include "stage.h"\nint Stage::run() { return 0; }\n',
    "native/src/play/round.cpp": '#include "scene/stage.h"\nint play() { Stage stage; return stage.run(); }\n',
    "native/src/main.cpp": "int play();\nint main() { return play(); }\n",
    "native/src/tool.cpp": "int main() { return 0; }\n",
    "native/src/gone.cpp": "int gone() { return 0; }\n",
    "native/tests/test_stage.cpp": '#include "scene/stage.h"\nint main() { Stage stage; return stage.run(); }\n',
    "tools/check_stage.py": "def main():\n    return 0\n",
}


def _write(root: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text, encoding="utf-8")


def _run(root: Path, *args: str) -> subprocess.CompletedProcess:
    environment = dict(os.environ, PYTHONPATH=str(SOURCE) + os.pathsep + os.environ.get("PYTHONPATH", ""), WINGBOX_JOBS="1")
    return subprocess.run([sys.executable, "-m", "wingbox", *args], capture_output=True, env=environment, cwd=str(root), timeout=180, text=True)


class BuildTargetsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        folder = tempfile.TemporaryDirectory()
        cls.addClassCleanup(folder.cleanup)
        cls.root = Path(os.path.realpath(folder.name))
        subprocess.run(["git", "init", "-q", str(cls.root)], check=True)
        _write(cls.root, PROJECT)
        _run(cls.root, "build", ".")
        cls.graph = (cls.root / ".wingbox" / "index.sexp").read_text(encoding="utf-8")

    def test_the_cmake_files_give_targets_and_tests(self) -> None:
        """/* REQ-CS-164: a build reads the build targets and the tests the CMake files state literally: `add_executable` and `add_library` with their source files, `target_sources`, and `add_test` in both forms, with the target a test runs (its command, or a `$<TARGET_FILE:<target>>` among its words) and the files of the repository its command names. Source paths are taken from the folder of the list file that includes the CMake file, from `${CMAKE_CURRENT_LIST_DIR}`, `${CMAKE_CURRENT_SOURCE_DIR}`, `${PROJECT_SOURCE_DIR}` and `${CMAKE_SOURCE_DIR}`. The graph holds one `(target <name> :kind executable|library ...)` and one `(ctest <name> ...)` fact for each; an alias, an imported target and a name written with a variable are left out. */"""
        facts = [line for line in self.graph.splitlines() if line.startswith(("(target ", "(ctest "))]
        names = sorted(line.split()[1] for line in facts if line.startswith("(target "))
        self.assertEqual(["round_app", "stage", "stage_tests", "tool_app"], names)
        self.assertEqual(1, sum(1 for line in facts if line.startswith("(target stage :kind library ")))
        self.assertEqual(1, sum(1 for line in facts if line.startswith("(target stage_tests :kind executable ")))
        tests = sorted(line for line in facts if line.startswith("(ctest "))
        self.assertEqual(4, len(tests), tests)
        self.assertIn("(ctest round_smoke :target round_app", tests[0])
        self.assertIn("(ctest stage_python :runs", tests[1])
        self.assertIn("(ctest stage_run :target stage_tests", tests[2])
        self.assertIn("(ctest tool_run :target tool_app", tests[3])

    def test_only_tests_names_the_targets_and_tests_a_change_reaches(self) -> None:
        """/* REQ-CS-165: `wingbox impact <file> --only tests` in a repository with such facts also names the build targets that hold the file or one of its dependents among their sources, `(affected-targets <file> <target> ...)`, and the tests that run one of those targets or a file of the list, `(affected-tests <file> <test> ...)` (`targets` and `tests` in JSON, two lines in the text); a file no target holds names none, and without `--only tests` the answer is as before. */"""
        answer = json.loads(_run(self.root, "impact", "native/src/scene/stage.h", "--only", "tests", "--json").stdout)["impact"][0]
        self.assertEqual(["round_app", "stage", "stage_tests"], answer["targets"])
        self.assertEqual(["round_smoke", "stage_run"], answer["tests"])
        compact = _run(self.root, "impact", "native/src/scene/stage.cpp", "--only", "tests").stdout
        self.assertIn("(affected-targets native/src/scene/stage.cpp round_app stage stage_tests)", compact)
        self.assertIn("(affected-tests native/src/scene/stage.cpp round_smoke stage_run)", compact)
        script = json.loads(_run(self.root, "impact", "tools/check_stage.py", "--only", "tests", "--json").stdout)["impact"][0]
        self.assertEqual(([], ["stage_python"]), (script["targets"], script["tests"]))
        tool = json.loads(_run(self.root, "impact", "native/src/tool.cpp", "--only", "tests", "--json").stdout)["impact"][0]
        self.assertEqual((["tool_app"], ["tool_run"]), (tool["targets"], tool["tests"]))
        main = json.loads(_run(self.root, "impact", "native/src/main.cpp", "--only", "tests", "--json").stdout)["impact"][0]
        self.assertEqual((["round_app"], ["round_smoke"]), (main["targets"], main["tests"]))
        lone = json.loads(_run(self.root, "impact", "native/src/gone.cpp", "--only", "tests", "--json").stdout)["impact"][0]
        self.assertEqual(([], []), (lone["targets"], lone["tests"]))
        whole = json.loads(_run(self.root, "impact", "native/src/scene/stage.h", "--json").stdout)["impact"][0]
        self.assertNotIn("targets", whole)
        human = _run(self.root, "impact", "native/src/scene/stage.h", "--only", "tests", "--human").stdout
        self.assertIn("Build targets: round_app, stage, stage_tests", human)
        self.assertIn("Tests to run: round_smoke, stage_run", human)


if __name__ == "__main__":
    unittest.main()
