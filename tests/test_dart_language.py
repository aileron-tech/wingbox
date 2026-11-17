from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.build_code_graph import Ok, assign_node_ids, build_graph, discover_files
from scripts.lang import language_for_file
from scripts.lang.dart import DART_LANGUAGE, parse_dart_treesitter
from scripts.lang.interface import FileResult


def _write(root: Path, relative_path: str, content: str) -> Path:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _pubspec(root: Path, name: str, extra: str = "") -> None:
    _write(
        root,
        "pubspec.yaml",
        f"name: {name}\ndescription: Shared application package.\n{extra}",
    )


class DartLanguageTests(unittest.TestCase):
    def _parse(self, root: Path, relative_path: str, content: str) -> FileResult:
        path = _write(root, relative_path, content)
        return parse_dart_treesitter(
            str(path),
            content,
            root.name,
            path.stat().st_mtime,
        )

    def test_parser_extracts_dart_types_members_and_heritage(self) -> None:
        """/* REQ-DART-001: Dart parsing keeps classes, mixins, enums, extensions, functions, members, and heritage. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = self._parse(
                root,
                "lib/features/users/repository.dart",
                """
import 'dart:async';
import 'package:flutter/material.dart';
import 'package:example_app/core/entities/entity.dart';

abstract class Repository<T extends Entity> {
  Future<T> find(String id);
  void save(T value) {}
  static Repository<T> build() => throw UnimplementedError();
}

class UserRepository extends Repository<User> with Retryable, Loggable implements Closeable {
  final int retries = 3;
  UserRepository(this.retries);
  @override
  Future<User> find(String id) async => User(id);
  @override
  void save(User value) {}
  int get size => retries;
}

mixin Retryable on Repository<User> {
  void retry() {}
}

mixin Loggable implements Retryable {}

enum Status { active, inactive, archived }

extension StringCase on String {
  String shout() => toUpperCase();
}

typedef Loader = Future<void> Function();

Future<void> loadUsers() async {}
int countOf(String name) => name.length;
""".lstrip(),
            )

        entities = {entity["name"]: entity for entity in result.entities}
        self.assertEqual("dart", result.language)
        self.assertEqual(
            [
                "dart:async",
                "package:flutter/material.dart",
                "package:example_app/core/entities/entity.dart",
            ],
            result.imports,
        )
        self.assertEqual("class", entities["Repository"]["kind"])
        self.assertEqual(
            ["find", "save", "build"],
            entities["Repository"]["methods"],
        )
        self.assertEqual("class", entities["UserRepository"]["kind"])
        self.assertEqual(
            ["find", "save", "size"],
            entities["UserRepository"]["methods"],
        )
        self.assertEqual(
            ["Repository", "Retryable", "Loggable", "Closeable"],
            entities["UserRepository"]["bases"],
        )
        self.assertEqual("mixin", entities["Retryable"]["kind"])
        self.assertEqual(["retry"], entities["Retryable"]["methods"])
        self.assertEqual(["Repository"], entities["Retryable"]["bases"])
        self.assertEqual("mixin", entities["Loggable"]["kind"])
        self.assertEqual(["Retryable"], entities["Loggable"]["bases"])
        self.assertEqual("enum", entities["Status"]["kind"])
        self.assertEqual("extension", entities["StringCase"]["kind"])
        self.assertEqual(["shout"], entities["StringCase"]["methods"])
        self.assertEqual(["String"], entities["StringCase"]["bases"])
        self.assertEqual("type", entities["Loader"]["kind"])
        self.assertEqual("function", entities["loadUsers"]["kind"])
        self.assertEqual("function", entities["countOf"]["kind"])
        self.assertNotIn("retries", entities)
        self.assertNotIn("size", entities)
        self.assertIn("Repository", result.exports_abstract)
        self.assertNotIn("UserRepository", result.exports_abstract)
        self.assertEqual("Repository", result.extends[0])
        # implements holds every implemented type of the file: the class
        # implements Closeable and the mixin implements Retryable.
        self.assertEqual({"Closeable", "Retryable"}, set(result.implements))
        for name in ("Repository", "UserRepository", "Retryable", "Status"):
            self.assertIn(name, result.exported_names)

    def test_mixin_application_class_names_the_mixins_after_its_base(self) -> None:
        """/* REQ-DART-002: A Dart mixin application class takes its superclass first and its mixins after it. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = self._parse(
                root,
                "lib/model/view_state.dart",
                """
abstract class ViewState {}
mixin Loaded on ViewState {}
mixin Retryable {}
class LoadedState = ViewState with Loaded;
class RetryState = Object with Loaded, Retryable;
""".lstrip(),
            )

        entities = {entity["name"]: entity for entity in result.entities}
        self.assertEqual("LoadedState", entities["LoadedState"]["name"])
        self.assertEqual("class", entities["LoadedState"]["kind"])
        self.assertEqual(
            ["ViewState", "Loaded"],
            entities["LoadedState"]["bases"],
        )
        self.assertEqual(
            ["Object", "Loaded", "Retryable"],
            entities["RetryState"]["bases"],
        )
        self.assertEqual("ViewState", result.extends[0])
        self.assertIn("ViewState", result.exports_abstract)
        self.assertNotIn("LoadedState", result.exports_abstract)

    def test_graph_resolves_package_and_relative_imports(self) -> None:
        """/* REQ-DART-003: package: imports of the project's own name resolve to lib files and relative imports to neighbouring files. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _pubspec(root, "example_app")
            model = self._parse(
                root,
                "lib/core/models/user.dart",
                "class User {\n  const User(this.id);\n  final String id;\n}\n",
            )
            helper = self._parse(
                root,
                "lib/core/utils/formats.dart",
                "String upperCase(String value) => value.toUpperCase();\n",
            )
            detail = self._parse(
                root,
                "lib/features/users/user_detail_page.dart",
                """
import 'package:example_app/core/models/user.dart';
import '../../core/utils/formats.dart';
import 'package:example_app/core/missing/missing.dart';
import 'dart:convert';
import 'package:intl/intl.dart';

class UserDetailPage {
  String describe(User user) => upperCase(user.id);
}
""".lstrip(),
            )
            graph_result = build_graph([model, helper, detail], str(root))
            if not isinstance(graph_result, Ok):
                self.fail(f"Dart graph build failed: {graph_result.error}")

        imports = {
            (edge["source"], edge["target"])
            for edge in graph_result.value.edges
            if edge["type"] == "IMPORTS"
        }
        self.assertEqual(
            {
                (
                    "lib/features/users/user_detail_page",
                    "lib/core/models/user",
                ),
                (
                    "lib/features/users/user_detail_page",
                    "lib/core/utils/formats",
                ),
            },
            imports,
        )

    def test_graph_prefers_the_nearest_pubspec_that_declares_the_name(self) -> None:
        """/* REQ-DART-004: package: names resolve through the pubspec that declares them, the nearest one to the importing file first. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _pubspec(root, "example_app")
            _pubspec(root / "packages/shared", "shared")
            shared = self._parse(
                root,
                "packages/shared/lib/format.dart",
                "String format(int value) => '$value';\n",
            )
            root_page = self._parse(
                root,
                "lib/pages/home_page.dart",
                "import 'package:shared/format.dart';\nclass HomePage {}\n",
            )
            package_page = self._parse(
                root,
                "packages/shared/example/example.dart",
                "import 'package:shared/format.dart';\nvoid main() {}\n",
            )
            graph_result = build_graph([shared, root_page, package_page], str(root))
            if not isinstance(graph_result, Ok):
                self.fail(f"Dart graph build failed: {graph_result.error}")

        imports = {
            (edge["source"], edge["target"])
            for edge in graph_result.value.edges
            if edge["type"] == "IMPORTS"
        }
        self.assertEqual(
            {
                ("lib/pages/home_page", "packages/shared/lib/format"),
                ("packages/shared/example/example", "packages/shared/lib/format"),
            },
            imports,
        )

    def test_part_directives_link_part_files_and_their_owner(self) -> None:
        """/* REQ-DART-005: part files are their own modules, linked to the library that parts them, and parts name their owner backwards. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _pubspec(root, "example_app")
            library = self._parse(
                root,
                "lib/src/store.dart",
                """
part 'store_state.dart';
part 'store_actions.dart';
part 'missing.g.dart';

class Store {}
""".lstrip(),
            )
            state = self._parse(
                root,
                "lib/src/store_state.dart",
                "part of 'store.dart';\n\nclass StoreState {}\n",
            )
            actions = self._parse(
                root,
                "lib/src/store_actions.dart",
                "part of 'store.dart';\n\nvoid reload() {}\n",
            )
            graph_result = build_graph([library, state, actions], str(root))
            if not isinstance(graph_result, Ok):
                self.fail(f"Dart graph build failed: {graph_result.error}")

        imports = {
            (edge["source"], edge["target"])
            for edge in graph_result.value.edges
            if edge["type"] == "IMPORTS"
        }
        self.assertEqual(
            {
                ("lib/src/store", "lib/src/store_state"),
                ("lib/src/store", "lib/src/store_actions"),
                ("lib/src/store_state", "lib/src/store"),
                ("lib/src/store_actions", "lib/src/store"),
            },
            imports,
        )

    def test_parts_named_with_another_library_uri_resolve_through_package_names(
        self,
    ) -> None:
        """/* REQ-DART-006: a part of directive with a package: or dart: URI resolves like an import and a missing part is never an error. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _pubspec(root, "example_app")
            owner = self._parse(
                root,
                "lib/core/clock.dart",
                "class Clock {}\n",
            )
            bare = self._parse(
                root,
                "lib/core/clock_io.dart",
                "part of 'package:example_app/core/clock.dart';\n\nvoid tick() {}\n",
            )
            builtin = self._parse(
                root,
                "lib/core/clock_builtin.dart",
                "part of 'dart:core';\n\nvoid other() {}\n",
            )
            graph_result = build_graph([owner, bare, builtin], str(root))
            if not isinstance(graph_result, Ok):
                self.fail(f"Dart graph build failed: {graph_result.error}")

        imports = {
            (edge["source"], edge["target"])
            for edge in graph_result.value.edges
            if edge["type"] == "IMPORTS"
        }
        self.assertEqual(
            {("lib/core/clock_io", "lib/core/clock")},
            imports,
        )

    def test_package_uri_parts_link_in_both_directions(self) -> None:
        """/* REQ-DART-014: a part named by a package: URI is an edge both ways, while dart: and third-party parts stay external. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _pubspec(root, "example_app")
            library = self._parse(
                root,
                "lib/a.dart",
                """
part 'package:example_app/src/b.dart';
part 'dart:core';
part 'package:some_third_party/generated/c.dart';

class A {}
""".lstrip(),
            )
            part = self._parse(
                root,
                "lib/src/b.dart",
                """
part of 'package:example_app/a.dart';

class B {}
""".lstrip(),
            )
            graph_result = build_graph([library, part], str(root))
            if not isinstance(graph_result, Ok):
                self.fail(f"Dart graph build failed: {graph_result.error}")

        imports = {
            (edge["source"], edge["target"])
            for edge in graph_result.value.edges
            if edge["type"] == "IMPORTS"
        }
        self.assertEqual(
            {
                ("lib/a", "lib/src/b"),
                ("lib/src/b", "lib/a"),
            },
            imports,
        )
        self.assertEqual(
            [
                "package:example_app/src/b.dart",
                "dart:core",
                "package:some_third_party/generated/c.dart",
            ],
            library.imports,
        )

    def test_generated_part_files_on_disk_are_indexed(self) -> None:
        """/* REQ-DART-007: a codegen part that exists on disk is discovered like any other part file, and the missing ones are skipped silently. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _pubspec(root, "example_app")
            model = _write(
                root,
                "lib/models/profile.dart",
                """
part 'profile.freezed.dart';
part 'profile.g.dart';

class Profile {
  const Profile(this.name);
  final String name;
}
""".lstrip(),
            )
            _write(
                root,
                "lib/models/profile.g.dart",
                "part of 'profile.dart';\n\nMap<String, dynamic> toJson(Profile p) => {};\n",
            )

            generated_path = model.parent / "profile.g.dart"
            self.assertTrue(generated_path.is_file())
            discovered = discover_files(str(root))
            parsed = {
                Path(path).name: parse_dart_treesitter(
                    path,
                    Path(path).read_text(encoding="utf-8"),
                    root.name,
                    Path(path).stat().st_mtime,
                )
                for path, _, _ in discovered
            }
            graph_result = build_graph(list(parsed.values()), str(root))
            if not isinstance(graph_result, Ok):
                self.fail(f"Dart graph build failed: {graph_result.error}")

        self.assertEqual(
            ["profile.dart", "profile.g.dart"],
            sorted(Path(path).name for path, _, _ in discovered),
        )
        self.assertEqual(
            ["profile.freezed.dart", "profile.g.dart"],
            parsed["profile.dart"].imports,
        )
        self.assertEqual(["profile.dart"], parsed["profile.g.dart"].imports)
        imports = {
            (edge["source"], edge["target"])
            for edge in graph_result.value.edges
            if edge["type"] == "IMPORTS"
        }
        self.assertEqual(
            {
                ("lib/models/profile", "lib/models/profile.g"),
                ("lib/models/profile.g", "lib/models/profile"),
            },
            imports,
        )

    def test_dart_language_server_command_is_exposed_for_lsp_mode(self) -> None:
        """/* REQ-DART-008: Dart exposes its language server command and label for LSP mode. */"""
        self.assertEqual(("dart", "language-server"), DART_LANGUAGE.lsp_command)
        self.assertEqual("Dart", DART_LANGUAGE.lsp_label)
        self.assertEqual(("dart",), DART_LANGUAGE.lsp_result_languages)
        self.assertEqual("dart", DART_LANGUAGE.lsp_language_id("lib/main.dart"))

    def test_dart_language_metadata_matches_the_registry_contract(self) -> None:
        """/* REQ-DART-009: Dart registration exposes canonical file, output, project, and dependency metadata. */"""
        self.assertIs(DART_LANGUAGE, language_for_file("main.dart"))
        self.assertIs(DART_LANGUAGE, language_for_file("lib/main.dart"))
        self.assertEqual("dart", DART_LANGUAGE.name)
        self.assertEqual(("dart",), DART_LANGUAGE.result_languages)
        self.assertEqual(("pubspec.yaml",), DART_LANGUAGE.project_config_names)
        self.assertEqual("Dart", DART_LANGUAGE.display_name)
        self.assertEqual("dart", DART_LANGUAGE.cli_label)
        self.assertEqual((".dart",), DART_LANGUAGE.source_extensions)
        self.assertEqual(
            [
                "tree-sitter==0.25.2",
                "tree-sitter-dart==0.1.0",
            ],
            [dependency.requirement for dependency in DART_LANGUAGE.dependencies],
        )
        self.assertTrue(DART_LANGUAGE.accepts_project_config("pubspec.yaml"))
        self.assertFalse(DART_LANGUAGE.accepts_project_config("pubspec.lock"))
        self.assertEqual(
            "lib/features/home_page",
            DART_LANGUAGE.normalize_module_id("lib/features/home_page.dart"),
        )
        self.assertEqual("dart", DART_LANGUAGE.output_language_code("dart"))
        with self.assertRaises(ValueError):
            DART_LANGUAGE.normalize_module_id("lib/features/home_page.ts")

    def test_dart_source_and_generated_files_share_one_module_id(self) -> None:
        """/* REQ-DART-010: a Dart file and its generated codegen part never collide: both keep their own module id. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "example_app"
            root.mkdir()
            paths = [
                str(root / "lib/models/profile.dart"),
                str(root / "lib/models/profile.g.dart"),
                str(root / "lib/models/profile.freezed.dart"),
            ]
            for path in paths:
                Path(path).parent.mkdir(parents=True, exist_ok=True)
                Path(path).write_text("class Profile {}\n", encoding="utf-8")

            assigned = assign_node_ids(paths, str(root))

        self.assertEqual(
            {
                "lib/models/profile": 1,
                "lib/models/profile.g": 1,
                "lib/models/profile.freezed": 1,
            },
            {node_id: list(assigned.values()).count(node_id) for node_id in assigned.values()},
        )
        self.assertEqual("lib/models/profile", assigned[paths[0]])
        self.assertEqual("lib/models/profile.g", assigned[paths[1]])

    def test_discovery_excludes_dart_and_flutter_build_output(self) -> None:
        """/* REQ-DART-011: discovery skips .dart_tool everywhere and build folders by name, and keeps other folders of the same name free of that rule. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _pubspec(root, "example_app")
            _write(root, "lib/main.dart", "void main() {}\n")
            _write(
                root,
                ".dart_tool/build/generated/kit_source.dart",
                "class Generated {}\n",
            )
            _write(
                root,
                "build/app/intermediates/generated_plugin.dart",
                "class GeneratedPlugin {}\n",
            )
            _write(
                root,
                "packages/shared/pubspec.yaml",
                "name: shared\n",
            )
            _write(
                root,
                "packages/shared/build/generated.dart",
                "class PackagedOutput {}\n",
            )
            _write(
                root,
                "packages/shared/lib/shared.dart",
                "class Shared {}\n",
            )
            _write(
                root,
                "tools/output/handwritten.dart",
                "class Handwritten {}\n",
            )
            _write(
                root,
                "tools/build/handwritten.dart",
                "class BuildGenerator {}\n",
            )

            discovered = discover_files(str(root))

        self.assertEqual(
            [
                "lib/main.dart",
                "packages/shared/lib/shared.dart",
                "tools/output/handwritten.dart",
            ],
            sorted(Path(path).relative_to(root).as_posix() for path, _, _ in discovered),
        )

    def test_dart_only_build_needs_no_parser_packages(self) -> None:
        """/* REQ-DART-012: Dart detection without its parser packages names the exact install command and publishes nothing. */"""
        repository_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            (target / "main.dart").write_text(
                "void main() {}\n",
                encoding="utf-8",
            )

            completed = subprocess.run(
                [
                    sys.executable,
                    "-S",
                    "-B",
                    str(repository_root / "scripts" / "build_code_graph.py"),
                    "--root",
                    str(target),
                ],
                cwd=repository_root,
                capture_output=True,
                check=False,
                env={
                    name: value
                    for name, value in os.environ.items()
                    if name != "PYTHONPATH"
                },
                text=True,
            )

            self.assertEqual(2, completed.returncode, completed.stderr)
            self.assertIn("Dart", completed.stderr)
            self.assertIn("tree-sitter-dart==0.1.0", completed.stderr)
            self.assertIn("requirements/dart.txt", completed.stderr)
            self.assertNotIn("tree-sitter-go", completed.stderr)
            self.assertNotIn("Traceback", completed.stderr)
            self.assertFalse((target / ".wingbox" / "index.sexp").exists())
            self.assertFalse((target / ".wingbox" / "cache.json").exists())

    def test_dart_project_produces_graph_nodes_with_dart_entities(self) -> None:
        """/* REQ-DART-013: a Dart project becomes a graph of dart nodes that carry their class and methods. */"""
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            _pubspec(base, "example_app")
            worker = _write(
                base,
                "lib/worker.dart",
                """
import 'package:example_app/utils/format.dart';

class Worker {
  void run() => format(1);
}
""".lstrip(),
            )
            _write(
                base,
                "lib/utils/format.dart",
                "String format(int value) => '$value';\n",
            )
            results = [
                parse_dart_treesitter(
                    str(path),
                    path.read_text(encoding="utf-8"),
                    base.name,
                    path.stat().st_mtime,
                )
                for path in (worker, base / "lib/utils/format.dart")
            ]
            graph_result = build_graph(results, str(base))
            if not isinstance(graph_result, Ok):
                self.fail(f"Dart graph build failed: {graph_result.error}")

        nodes = {node["id"]: node for node in graph_result.value.nodes}
        self.assertEqual({"lib/worker", "lib/utils/format"}, set(nodes))
        self.assertEqual("dart", nodes["lib/worker"]["language"])
        self.assertEqual(
            [("Worker", "class", ["run"])],
            [
                (entity["name"], entity["kind"], entity.get("methods", []))
                for entity in nodes["lib/worker"]["entities"]
            ],
        )
        imports = {
            (edge["source"], edge["target"])
            for edge in graph_result.value.edges
            if edge["type"] == "IMPORTS"
        }
        self.assertEqual({("lib/worker", "lib/utils/format")}, imports)

if __name__ == "__main__":
    unittest.main()