"""Acceptance harness for Dart and Flutter: graph accuracy without a private sample project.

Every test builds synthetic Dart projects in a temporary directory -- an
invented package name (``example_app``), no real client, employer or project --
and asserts on the graph produced through the public entry point
``scripts/build_code_graph.py``, invoked as a subprocess the way a user runs
it. ``benchmarks/dart/check_accuracy.py`` is exercised the same way, on a small
synthetic project, so that the accuracy check of a real project cannot rot
without a test failing.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parents[1]
BUILD_SCRIPT = REPO_ROOT / "scripts" / "build_code_graph.py"
SEARCH_SCRIPT = REPO_ROOT / "scripts" / "code_graph_search.py"
ACCURACY_SCRIPT = REPO_ROOT / "benchmarks" / "dart" / "check_accuracy.py"

# A build of a few hundred Dart files takes seconds; this bound only catches a
# build that stops finishing at all, so it stays generous on a slow machine.
PERFORMANCE_BOUND_SECONDS = 120.0
PERFORMANCE_FILES = 700

def _load_module(name: str, path: Path) -> ModuleType:
    """Load a repository script by path, as the tests and the skill do."""
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


ACCURACY = _load_module("dart_check_accuracy", ACCURACY_SCRIPT)
SEARCH = _load_module("dart_acceptance_search", SEARCH_SCRIPT)


def write_file(path: Path, content: str) -> None:
    """Write one source file, creating its directories."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def write_project(root: Path, files: dict[str, str], package: str | None = "example_app") -> None:
    """Write a synthetic project: an optional pubspec.yaml and the given files."""
    if package is not None:
        write_file(
            root / "pubspec.yaml",
            f"name: {package}\ndescription: A synthetic project for the test suite.\n"
            "environment:\n  sdk: '>=3.0.0 <4.0.0'\n",
        )
    for relative_path, content in files.items():
        write_file(root / relative_path, content)


def _short_id_of(graph_path: Path, module: str) -> str:
    """The short ID a rendered graph gives a module, as the graph's own reader decodes it."""
    graph = SEARCH.CodeGraph()
    loaded = graph.load(str(graph_path))
    if not isinstance(loaded, SEARCH.MapLoadOk):
        raise AssertionError(f"cannot load {graph_path}: {loaded.error}")
    for short_id, module_id in graph.pmap.items():
        if module_id == module:
            return short_id
    raise AssertionError(f"{module} has no short ID in {graph_path}")


def _remove_import_fact(lines: list[str], graph_path: Path, source: str, target: str) -> list[str]:
    """Drop the one ``(imports <source> <target>)`` fact of a rendered graph and count it out."""
    wanted = ("imports", _short_id_of(graph_path, source), _short_id_of(graph_path, target))
    dropped = any(line.strip().strip("()").split()[:3] == list(wanted) for line in lines)
    kept: list[str] = []
    for line in lines:
        if line.strip().strip("()").split()[:3] == list(wanted):
            continue  # the fact itself
        if dropped and line.startswith("; ") and " edges " in line and "---" not in line:
            # "; 7 files 8 nodes 8 edges 0 violations"
            fields = line.split()
            edges_at = fields.index("edges")
            fields[edges_at - 1] = str(int(fields[edges_at - 1]) - 1)
            line = " ".join(fields)
        elif dropped and line.startswith("(code-graph ") and ":edges" in line:
            head, _, rest = line.partition(":edges ")
            number, space, tail = rest.partition(" ")
            line = f"{head}:edges {int(number) - 1}{space}{tail}"
        kept.append(line)
    return kept


class DartAcceptanceCase(unittest.TestCase):
    """Run the builder as a user does and read the graph it wrote."""

    def build(self, root: Path, full: bool = True) -> subprocess.CompletedProcess:
        """Build the graph of one synthetic project in a subprocess."""
        command = [sys.executable, str(BUILD_SCRIPT), "--root", str(root)]
        if full:
            command.append("--full")
        started = time.monotonic()
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=PERFORMANCE_BOUND_SECONDS * 3,
        )
        self.last_build_seconds = time.monotonic() - started
        if completed.returncode != 0 and "Permission denied" in completed.stderr:
            # A host may refuse the process pool that the builder shares with every
            # language (no POSIX semaphores): the build then runs the same builder with
            # the serial pool of the accuracy check, unchanged in every other way.
            ACCURACY.run_builder(
                str(root), output=str(root / ".code_graph.sexp"), full=full, python=sys.executable
            )
            return completed
        self.assertEqual(
            0,
            completed.returncode,
            f"the build failed\nstdout:\n{completed.stdout[-4000:]}\nstderr:\n{completed.stderr[-4000:]}",
        )
        return completed

    def graph(self, root: Path, full: bool = True):
        """The loaded code graph of a project, built first."""
        self.build(root, full=full)
        graph = SEARCH.CodeGraph()
        loaded = graph.load(str(root / ".code_graph.sexp"))
        self.assertIsInstance(loaded, SEARCH.MapLoadOk, getattr(loaded, "error", loaded))
        return graph

    def graph_edges(self, root: Path) -> set[tuple[str, str, str]]:
        """Every fact of the graph as (kind, source, target) with module IDs."""
        return {(edge.etype, edge.source, edge.target) for edge in self.graph(root).all_edges}

    def import_targets(self, root: Path, node: str) -> set[str]:
        """The modules one module imports."""
        return {
            edge.target
            for edge in self.graph(root).all_edges
            if edge.etype == "imports" and edge.source == node
        }

    def base_tree(self) -> dict[str, str]:
        """The small package every test starts from: two models and one page."""
        return {
            "lib/core/models/user.dart": (
                "class User {\n"
                "  const User(this.id);\n"
                "  final String id;\n"
                "}\n"
            ),
            "lib/core/utils/formats.dart": (
                "String upperCase(String value) => value.toUpperCase();\n"
            ),
            "lib/features/users/user_detail_page.dart": (
                "import 'package:example_app/core/models/user.dart';\n"
                "import '../../core/utils/formats.dart';\n"
                "\n"
                "class UserDetailPage {\n"
                "  String describe(User user) => upperCase(user.id);\n"
                "}\n"
            ),
        }


class DartAcceptanceDependencyTests(DartAcceptanceCase):
    def test_own_package_import_resolves_to_the_lib_file(self) -> None:
        """/* REQ-DARTACC-001: A package: import of the project's own pubspec name resolves to the lib file it names, with and without `.dart`. */"""
        files = self.base_tree()
        files["lib/features/users/user_list_page.dart"] = (
            "import 'package:example_app/core/models/user.dart';\n"
            "import 'package:example_app/core/utils/formats.dart';\n"
            "\n"
            "class UserListPage {}\n"
        )
        files["lib/features/without_extension.dart"] = (
            "import 'package:example_app/core/models/user';\n"
            "\n"
            "class WithoutExtension {}\n"
        )
        files["lib/core/models/account.dart"] = (
            "import 'user.dart';\n"
            "\n"
            "class Account {}\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_project(root, files)

            without_extension = self.import_targets(root, "lib/features/without_extension")
            list_page = self.import_targets(root, "lib/features/users/user_list_page")
            detail_page = self.import_targets(root, "lib/features/users/user_detail_page")

        self.assertIn("lib/core/models/user", without_extension, "a package: URI without `.dart`")
        self.assertEqual(
            {"lib/core/models/user", "lib/core/utils/formats"},
            list_page,
            "a package: URI of the own package",
        )
        self.assertEqual(
            {"lib/core/models/user", "lib/core/utils/formats"},
            detail_page,
            "a package: URI and a relative one in one file",
        )

    def test_relative_import_resolves_against_the_importing_file(self) -> None:
        """/* REQ-DARTACC-002: A relative import resolves from the importing file's directory, for `.` and `..` paths alike. */"""
        files = self.base_tree()
        files["lib/features/users/detail/header.dart"] = (
            "String initial(User user) => user.id.substring(0, 1);\n"
        )
        files["lib/features/users/detail/body.dart"] = (
            "import '../../../core/models/user.dart';\n"
            "import './header.dart';\n"
            "import 'body.dart';\n"
            "\n"
            "class Body {}\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_project(root, files)

            targets = self.import_targets(root, "lib/features/users/detail/body")

        self.assertEqual(
            {"lib/core/models/user", "lib/features/users/detail/header"},
            targets,
        )

    def test_dart_and_third_party_imports_create_no_edge(self) -> None:
        """/* REQ-DARTACC-003: `dart:` imports and packages that no pubspec of the project declares resolve to no module and create no edge. */"""
        files = self.base_tree()
        files["lib/core/utils/json.dart"] = (
            "import 'dart:async';\n"
            "import 'dart:convert';\n"
            "import 'package:intl/intl.dart';\n"
            "import 'package:firebase_core/firebase_core.dart' as firebase;\n"
            "import 'package:flutter/material.dart';\n"
            "\n"
            "String encode(Object value) => jsonEncode(value);\n"
        )
        # A package named like a third-party one is only local when a pubspec declares it.
        files["lib/features/external.dart"] = (
            "import 'package:intl/intl.dart';\n"
            "\n"
            "class External {}\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_project(root, files)

            edges = self.graph_edges(root)

        sources = {source for kind, source, _ in edges if kind == "imports"}
        self.assertNotIn("lib/core/utils/json", sources)
        self.assertNotIn("lib/features/external", sources)
        targets = {target for kind, _, target in edges if kind == "imports"}
        self.assertNotIn("dart:convert", targets)
        self.assertFalse(
            [edge for edge in edges if "dart:" in edge[2] or "package:" in edge[2]],
            f"a raw specifier became a module: {sorted(edges)}",
        )

    def test_a_second_package_in_the_project_resolves_on_its_own_name(self) -> None:
        """/* REQ-DARTACC-004: A package:<name> import resolves to the lib folder of the pubspec.yaml that declares that name, not only to the root package. */"""
        files = self.base_tree()
        files["packages/shared/pubspec.yaml"] = (
            "name: shared_kit\ndescription: A shared library of the project.\n"
        )
        files["packages/shared/lib/models/item.dart"] = "class Item {}\n"
        files["lib/features/item_page.dart"] = (
            "import 'package:shared_kit/models/item.dart';\n"
            "\n"
            "class ItemPage {}\n"
        )
        files["lib/features/wrong_name.dart"] = (
            "import 'package:shared/models/item.dart';\n"
            "\n"
            "class WrongName {}\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_project(root, files)

            edges = self.graph_edges(root)

        self.assertIn(
            ("imports", "lib/features/item_page", "packages/shared/lib/models/item"),
            edges,
        )
        self.assertNotIn(
            "wrong_name",
            {source.rsplit("/", 1)[-1] for _, source, _ in edges},
            "a specifier of a package name no pubspec declares made an edge",
        )


class DartAcceptancePartTests(DartAcceptanceCase):
    def test_part_and_part_of_link_the_library_and_its_parts(self) -> None:
        """/* REQ-DARTACC-005: A library's part directive and a part file's part of directive link the two files of one Dart library. */"""
        files = self.base_tree()
        files["lib/features/account/account.dart"] = (
            "part 'account_state.dart';\n"
            "\n"
            "class Account {\n"
            "  const Account(this.id);\n"
            "  final String id;\n"
            "}\n"
        )
        files["lib/features/account/account_state.dart"] = (
            "part of 'account.dart';\n"
            "\n"
            "extension AccountLabel on Account {\n"
            "  String get label => 'Account ${id}';\n"
            "}\n"
        )
        files["lib/features/account/account_view.dart"] = (
            "import 'account.dart';\n"
            "\n"
            "class AccountView {\n"
            "  final Account account;\n"
            "  const AccountView(this.account);\n"
            "}\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_project(root, files)

            edges = self.graph_edges(root)

        imports = {(source, target) for kind, source, target in edges if kind == "imports"}
        self.assertEqual(
            {
                ("lib/features/account/account", "lib/features/account/account_state"),
                ("lib/features/account/account_state", "lib/features/account/account"),
                ("lib/features/account/account_view", "lib/features/account/account"),
            },
            {pair for pair in imports if "account/" in pair[0]},
        )

    def test_a_package_uri_names_a_part_file_because_it_shares_the_library(self) -> None:
        """/* REQ-DARTACC-006: a part imported by a package: URI is the dependent of its importer and the library names the part in the other direction. */"""
        files = self.base_tree()
        files["lib/features/cart/cart.dart"] = "part 'cart_total.dart';\n\nclass Cart {}\n"
        files["lib/features/cart/cart_total.dart"] = (
            "part of 'cart.dart';\n\nint totalOf(Cart cart) => 0;\n"
        )
        files["lib/features/cart/cart_page.dart"] = (
            "import 'package:example_app/features/cart/cart_total.dart';\n"
            "\n"
            "class CartPage {}\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_project(root, files)

            edges = self.graph_edges(root)

        imports = {(source, target) for kind, source, target in edges if kind == "imports"}
        self.assertIn(("lib/features/cart/cart_page", "lib/features/cart/cart_total"), imports)
        self.assertIn(("lib/features/cart/cart", "lib/features/cart/cart_total"), imports)
        # The page names the part, not the library: no edge from the page to cart.dart.
        self.assertNotIn(("lib/features/cart/cart_page", "lib/features/cart/cart"), imports)

    def test_a_missing_part_is_silent(self) -> None:
        """/* REQ-DARTACC-007: A part directive that names no file of the project does not fail the build. */"""
        files = self.base_tree()
        files["lib/features/generated/generated.dart"] = (
            "part 'generated.g.dart';\n"
            "\n"
            "class Generated {}\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_project(root, files)

            graph = self.graph(root)

        self.assertIn("lib/features/generated/generated", graph.nodes)
        self.assertFalse(
            [
                node
                for node in graph.nodes
                if node.startswith("lib/features/generated/")
                and node != "lib/features/generated/generated"
            ],
            f"a part that does not exist became a module: {sorted(graph.nodes)}",
        )


class DartAcceptanceDiscoveryTests(DartAcceptanceCase):
    def test_generated_output_next_to_a_pubspec_is_not_a_module(self) -> None:
        """/* REQ-DARTACC-008: .dart_tool/ and Flutter's build/ next to the pubspec.yaml hold generated code and are not modules of the graph. */"""
        files = self.base_tree()
        files[".dart_tool/flutter_build/dart_plugin_registrant.dart"] = "class Registrant {}\n"
        files[".dart_tool/package_config.dart"] = "class PackageConfig {}\n"
        files["build/generated/source_gen.dart"] = "class GeneratedSource {}\n"
        files["build/app/intermediates/main.dart"] = "class Main {}\n"
        files["lib/generated_keep.dart"] = "class Kept {}\n"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_project(root, files)

            graph = self.graph(root)

        modules = set(graph.nodes)
        for generated in (
            ".dart_tool/flutter_build/dart_plugin_registrant",
            ".dart_tool/package_config",
            "build/generated/source_gen",
            "build/app/intermediates/main",
        ):
            self.assertNotIn(generated, modules, "generated output became a module of the graph")
        self.assertIn("lib/generated_keep", modules)
        self.assertIn("lib/core/models/user", modules)

    def test_a_file_under_test_is_a_test_file(self) -> None:
        """/* REQ-DARTACC-009: a file under test/ is a test module and imports its package's lib code like any other file. */"""
        files = self.base_tree()
        files["test/user_detail_page_test.dart"] = (
            "import 'package:example_app/features/users/user_detail_page.dart';\n"
            "import 'package:flutter_test/flutter_test.dart';\n"
            "\n"
            "void main() {}\n"
        )
        files["test/helper/fixtures.dart"] = "const fixtureIds = <String>['a'];\n"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_project(root, files)

            graph = self.graph(root)

        self.assertIn("lib/features/users/user_detail_page", graph.nodes)
        node = graph.nodes["test/user_detail_page_test"]
        self.assertEqual("test", node.purpose)
        self.assertEqual("dart", node.lang)
        self.assertIn(
            "lib/features/users/user_detail_page",
            {edge.target for edge in graph.all_edges if edge.etype == "imports" and edge.source == "test/user_detail_page_test"},
        )

    def test_a_nested_pubspec_does_not_duplicate_its_project(self) -> None:
        """/* REQ-DARTACC-010: a nested package's lib file is one module, named by its path from the project root. */"""
        files = self.base_tree()
        files["example/lib/example.dart"] = "class Example {}\n"
        files["example/pubspec.yaml"] = "name: example_runner\ndescription: The example app.\n"
        files["lib/features/example_page.dart"] = (
            "import 'package:example_runner/example.dart';\n"
            "\n"
            "class ExamplePage {}\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_project(root, files)

            graph = self.graph(root)

        self.assertIn("example/lib/example", graph.nodes)
        self.assertNotIn("example", graph.nodes, "the packaging folder became a module")
        self.assertIn(
            ("lib/features/example_page", "example/lib/example"),
            {(edge.source, edge.target) for edge in graph.all_edges if edge.etype == "imports"},
        )


class DartAcceptanceAccuracyTests(DartAcceptanceCase):
    def _accuracy_project(self) -> dict[str, str]:
        """A project with every shape of specifier the check must score."""
        return {
            "lib/core/models/user.dart": "class User {\n  const User(this.id);\n  final String id;\n}\n",
            "lib/core/models/animal.dart": "abstract class Animal {\n  const Animal();\n}\n",
            "lib/core/utils/formats.dart": "String upperCase(String value) => value.toUpperCase();\n",
            "lib/features/users/user_page.dart": (
                "import 'package:example_app/core/models/user.dart';\n"
                "import '../../core/utils/formats.dart';\n"
                "\n"
                "class UserPage {}\n"
            ),
            "lib/features/users/user_detail_page.dart": (
                "import './user_page.dart';\n"
                "\n"
                "class UserDetailPage {}\n"
            ),
            "lib/features/animals/dog.dart": (
                "import 'package:example_app/core/models/animal.dart';\n"
                "\n"
                "class Dog extends Animal {\n  const Dog();\n}\n"
            ),
            "lib/features/cart/cart.dart": "part 'cart_total.dart';\n\nclass Cart {}\n",
            "lib/features/cart/cart_total.dart": "part of 'cart.dart';\n\nint totalOf(Cart cart) => 0;\n",
            "lib/features/cart/cart_page.dart": (
                "import 'package:example_app/features/cart/cart.dart';\n"
                "import 'package:example_app/features/cart/cart_total.dart';\n"
                "\n"
                "class CartPage {}\n"
            ),
            "lib/features/theme/theme.dart": (
                "import 'dart:ui';\nimport 'package:flutter/material.dart';\n\nclass Theme {}\n"
            ),
            "test/user_page_test.dart": (
                "import 'package:example_app/features/users/user_page.dart';\n\nvoid main() {}\n"
            ),
        }

    def test_the_accuracy_check_passes_on_a_small_synthetic_project(self) -> None:
        """/* REQ-DARTACC-011: benchmarks/dart/check_accuracy.py runs on a project path and exits 0 when the graph agrees with the import statements on disk. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_project(root, self._accuracy_project())

            completed = subprocess.run(
                [
                    sys.executable,
                    str(ACCURACY_SCRIPT),
                    str(root),
                    "--sample",
                    "40",
                    "--seed",
                    "7",
                    "--json",
                    str(root / "accuracy.json"),
                ],
                capture_output=True,
                text=True,
                timeout=PERFORMANCE_BOUND_SECONDS * 3,
            )
            result = json.loads((root / "accuracy.json").read_text(encoding="utf-8"))

        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
        aggregate = result["aggregate"]
        self.assertEqual(10, aggregate["sampled"], "every lib/ file is sampled")
        self.assertEqual(0, aggregate["missing_pairs"], result["files"])
        self.assertEqual([], aggregate["absent_nodes"])
        self.assertGreater(aggregate["expected_pairs"], 0, "the sample found no importer pair at all")
        self.assertEqual(1.0, aggregate["recall"])
        # A library and its part name each other: a legitimate edge, never an extra dependent.
        self.assertEqual(0, aggregate["extra_pairs"], result["files"])
        cart = next(entry for entry in result["files"] if entry["file"] == "lib/features/cart/cart.dart")
        self.assertEqual(2, cart["expected_pairs"], "the part and the page name the library")
        self.assertEqual([], cart["missing"], f"the graph lost a dependent: {cart}")
        self.assertIn("aggregate over", completed.stdout)
        self.assertIn("precision", completed.stdout)

    def test_the_accuracy_check_exits_1_on_a_graph_lacking_an_import(self) -> None:
        """/* REQ-DARTACC-012: check_accuracy.py exits 1 when the graph misses an importer that the files on disk name. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_project(root, self._accuracy_project())
            graph_path, _seconds = ACCURACY.run_builder(str(root))
            # A graph is a text file: remove one fact, the import of user by user_page.
            lines = Path(graph_path).read_text(encoding="utf-8").splitlines()
            removed = _remove_import_fact(lines, graph_path, "lib/features/users/user_page", "lib/core/models/user")
            self.assertEqual(len(lines) - 1, len(removed), "expected exactly one matching imports fact")
            Path(graph_path).write_text("\n".join(removed) + "\n", encoding="utf-8")

            completed = subprocess.run(
                [sys.executable, str(ACCURACY_SCRIPT), str(root), "--graph", graph_path, "--sample", "40"],
                capture_output=True,
                text=True,
                timeout=PERFORMANCE_BOUND_SECONDS * 3,
            )

        self.assertEqual(1, completed.returncode, completed.stdout)
        self.assertIn("missing importer: lib/features/users/user_page.dart", completed.stdout)
        self.assertIn("lib/core/models/user.dart", completed.stdout)
        aggregate = completed.stdout.rsplit("recall ", 1)[1]
        self.assertLess(float(aggregate.split()[0]), 0.99, "the run must report the miss in its recall")

    def test_the_accuracy_check_counts_only_found_pairs_it_knows(self) -> None:
        """/* REQ-DARTACC-013: precision counts a dependent that no import statement backs as extra, while recall counts only the importer pairs of the files. */"""
        report = ACCURACY.Report(
            root="/project",
            graph="/project/.code_graph.sexp",
            seed=1,
            packages=["example_app"],
            lib_files=3,
            modules=3,
            build_seconds=0.1,
        )
        importer = ACCURACY.FileScore(file="lib/a.dart", node="lib/a", present=True)
        importer.expected = {"lib/b.dart", "lib/c.dart"}
        importer.found = {"lib/b.dart"}
        importer.missing = {"lib/c.dart"}
        heir = ACCURACY.FileScore(file="lib/d.dart", node="lib/d", present=True)
        heir.found = {"lib/e.dart"}
        heir.extra = {"lib/e.dart"}
        heir.extra_reasons = {"lib/e.dart": "inheritance-only"}
        report.files = [importer, heir]

        self.assertEqual(2, report.expected_pairs)
        self.assertEqual(1, report.missing_pairs)
        self.assertEqual(0.5, report.recall)
        self.assertEqual(1, report.extra_pairs)
        self.assertEqual(0.5, report.precision)
        text = ACCURACY.format_text(report, 0.99)
        self.assertIn("missing importer: lib/c.dart", text)
        self.assertIn("inheritance-only", text)
        self.assertIn("aggregate over 2 sampled files", text)
        self.assertIn("dart-accuracy", ACCURACY.format_sexp(report))
        self.assertEqual({"lib/c.dart"}, set(report.to_dict()["files"][0]["missing"]))

    def test_a_library_and_its_part_are_linked_either_way(self) -> None:
        """/* REQ-DARTACC-017: a part directive links two files in both directions and the check counts that link once, as a legitimate edge and not as an extra dependent. */"""
        belongs = {
            "/app/lib/cart/cart_total.dart": "/app/lib/cart/cart.dart",
            "/app/lib/cart/cart_item.dart": "/app/lib/cart/cart.dart",
        }

        self.assertTrue(ACCURACY.part_link("/app/lib/cart/cart.dart", "/app/lib/cart/cart_total.dart", belongs))
        self.assertTrue(ACCURACY.part_link("/app/lib/cart/cart_total.dart", "/app/lib/cart/cart.dart", belongs))
        self.assertFalse(
            ACCURACY.part_link("/app/lib/cart/cart_total.dart", "/app/lib/cart/cart_item.dart", belongs),
            "two parts of one library are not linked to each other",
        )

        report = ACCURACY.Report(
            root="/app",
            graph="/app/.code_graph.sexp",
            seed=1,
            packages=["example_app"],
            lib_files=1,
            modules=1,
            build_seconds=0.1,
        )
        facts = ACCURACY.GraphFacts(
            modules={
                "/app/lib/cart/cart.dart": "lib/cart/cart",
                "/app/lib/cart/cart_total.dart": "lib/cart/cart_total",
            },
            # The adapter records the part -> library direction.
            dependents={"/app/lib/cart/cart.dart": {"/app/lib/cart/cart_total.dart": "imports"}},
            graph_path="/app/.code_graph.sexp",
        )
        specifiers = {
            "/app/lib/cart/cart.dart": [ACCURACY.Specifier("part", "cart_total.dart", "/app/lib/cart/cart_total.dart")],
            "/app/lib/cart/cart_total.dart": [
                ACCURACY.Specifier("part", "cart.dart", "/app/lib/cart/cart.dart")
            ],
        }
        score = ACCURACY.score_file(
            "/app/lib/cart/cart.dart", "/app", facts, specifiers, belongs
        )
        report.files = [score]
        self.assertEqual({"lib/cart/cart_total.dart"}, score.expected)
        self.assertEqual({"lib/cart/cart_total.dart"}, score.found)
        self.assertEqual(set(), score.extra, "a part link must never be an extra dependent")
        self.assertEqual(0, report.extra_pairs)

    def test_the_sampled_files_are_the_same_for_the_same_seed(self) -> None:
        """/* REQ-DARTACC-014: the sample of a run is reproducible: one seed gives one set of files, a different seed another. */"""
        files = [f"lib/module_{index:02d}.dart" for index in range(50)]

        first = ACCURACY.sample_files(files, 10, 20261117)
        again = ACCURACY.sample_files(list(reversed(files)), 10, 20261117)
        other = ACCURACY.sample_files(files, 10, 20261118)

        self.assertEqual(first, again)
        self.assertEqual(10, len(first))
        self.assertNotEqual(first, other)
        self.assertEqual(sorted(files), ACCURACY.sample_files(files, 500, 3))


class DartAcceptancePerformanceTests(DartAcceptanceCase):
    def _synthetic_project(self, root: Path) -> None:
        """Generate a project of PERFORMANCE_FILES files, by the test itself, never committed."""
        write_project(root, {}, package="example_app")
        write_file(
            root / "lib/core/base.dart",
            "abstract class Entity {\n  const Entity(this.id);\n  final String id;\n}\n",
        )
        write_file(
            root / "lib/core/format.dart",
            "String labelFor(String value) => value.trim().toUpperCase();\n",
        )
        remaining = PERFORMANCE_FILES - 2
        for index in range(remaining):
            feature = f"feature_{index // 20:02d}"
            page = f"page_{index:03d}"
            imports = ["import '../core/base.dart';"]
            base = ""
            if index > 0:
                previous = f"page_{index - 1:03d}"
                previous_dir = f"feature_{(index - 1) // 20:02d}"
                if previous_dir == feature:
                    imports.append(f"import './{previous}.dart';")
                else:
                    imports.append(f"import '../{previous_dir}/{previous}.dart';")
                base = "(Entity)"
            write_file(
                root / "lib" / feature / f"{page}.dart",
                "\n".join(imports)
                + "\n\n"
                + f"class Widget{index}{base} {{\n"
                + f"  Widget{index}(String id) : super(id);\n"
                + "  String render() => labelFor(id);\n"
                + "}\n",
            )
        write_file(
            root / "lib/main.dart",
            "import 'feature_00/page_000.dart';\nimport 'core/format.dart';\n\n"
            "void main() {\n  print(labelFor(Widget0('root').id));\n}\n",
        )

    def test_a_large_project_builds_within_the_bound_and_rebuilds_byte_for_byte(self) -> None:
        """/* REQ-DARTACC-015: a build of ~700 Dart files finishes under a generous bound, and a second build of the unchanged tree writes a byte-identical graph. */"""
        with tempfile.TemporaryDirectory() as directory:
            # The generator calls the temp directory "example_app": the root name
            # becomes the repository name of the graph, so name both trees alike.
            root = Path(directory) / "example_app"
            self._synthetic_project(root)
            generated = [path for path in root.rglob("*.dart")]
            self.assertEqual(PERFORMANCE_FILES + 1, len(generated))

            first = self.build(root)
            first_seconds = self.last_build_seconds
            graph_path = root / ".code_graph.sexp"
            self.assertTrue(graph_path.is_file(), first.stdout[-2000:])
            first_hash = hashlib.sha256(graph_path.read_bytes()).hexdigest()

            second = self.build(root, full=False)
            second_hash = hashlib.sha256(graph_path.read_bytes()).hexdigest()

        self.assertLess(
            first_seconds,
            PERFORMANCE_BOUND_SECONDS,
            f"the build took {first_seconds:.1f} s for {PERFORMANCE_FILES + 1} files",
        )
        self.assertEqual(first_hash, second_hash, "a rebuild of an unchanged tree changed the graph")
        self.assertIn(
            f"Found {PERFORMANCE_FILES + 1} source files",
            first.stdout,
            "the build did not see every generated file",
        )
        self.assertIn("Parsing 0 changed files", second.stdout)

    def test_a_full_rebuild_of_the_unchanged_tree_is_byte_identical_too(self) -> None:
        """/* REQ-DARTACC-016: two full builds of the same tree -- in another directory, with another mtime -- write the same graph, so nothing in it depends on the run. */"""
        contents = self._synthetic_project_contents()
        hashes = []
        for attempt in range(2):
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory) / "example_app"
                write_project(root, {}, package="example_app")
                for relative_path, content in contents.items():
                    write_file(root / relative_path, content)
                    # A second copy written a moment later has other mtimes.
                    os.utime(root / relative_path, (time.time() + attempt, time.time() + attempt))
                self.build(root, full=True)
                hashes.append(hashlib.sha256((root / ".code_graph.sexp").read_bytes()).hexdigest())

        self.assertEqual(hashes[0], hashes[1], "two full builds of one tree wrote different graphs")

    def _synthetic_project_contents(self) -> dict[str, str]:
        """The generated files of the performance project as a mapping, without writing them."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._synthetic_project(root)
            return {
                str(path.relative_to(root)): path.read_text(encoding="utf-8")
                for path in sorted(root.rglob("*.dart"))
            }


if __name__ == "__main__":
    unittest.main()