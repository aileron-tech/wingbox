"""Defects found in the review of 0.19.1 in the adapters of Go, C#, Java, PHP, Ruby, Dart, C and C++ and Pascal,
and in the reading of the CMake files."""
from __future__ import annotations

import contextlib
import io
import os
import tempfile
import time
import unittest
from pathlib import Path

from scripts.build_code_graph import Ok, build_graph, discover_files, parse_file


def _write(root: Path, files: dict[str, str | bytes]) -> None:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(text, bytes):
            path.write_bytes(text)
        else:
            path.write_text(text, encoding="utf-8")


class _Built:
    """The graph of a folder of files: the import edges, their relations and what the build printed."""

    def __init__(self, files: dict[str, str | bytes]) -> None:
        self.log = io.StringIO()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(os.path.realpath(directory))
            _write(root, files)
            with contextlib.redirect_stderr(self.log), contextlib.redirect_stdout(self.log):
                results = [parse_file(item) for item in discover_files(str(root))]
                built = build_graph(results, str(root))
        if not isinstance(built, Ok):
            raise AssertionError(f"graph build failed: {built.error}")
        self.results = {os.path.relpath(result.path, root).replace(os.sep, "/"): result for result in results}
        self.graph = built.value
        self.paths = {node["id"]: node["path"] for node in self.graph.nodes}
        self.relations: dict[tuple[str, str], list[str]] = {}
        for edge in self.graph.edges:
            if edge["type"] == "IMPORTS":
                self.relations[(self.paths[edge["source"]], self.paths[edge["target"]])] = list(edge.get("relations", []))
        self.edges = set(self.relations)

    def dependents(self, path: str) -> set[str]:
        return {source for source, target in self.edges if target == path}

    def dependencies(self, path: str) -> set[str]:
        return {target for source, target in self.edges if source == path}


class GoPackageFilesTest(unittest.TestCase):
    def test_a_file_depends_on_the_file_of_its_package_whose_function_it_calls(self) -> None:
        """/* REQ-GO-9600: A Go file that calls a function another file of its package defines depends on that file. */"""
        built = _Built({
            "go.mod": "module example.com/app\n\ngo 1.22\n",
            "main.go": "package main\n\nfunc main() { helper() }\n",
            "helper.go": "package main\n\nfunc helper() {}\n",
            "other.go": "package main\n\nfunc unused() {}\n",
        })
        self.assertEqual({"main.go"}, built.dependents("helper.go"))
        self.assertEqual(set(), built.dependents("other.go"))

    def test_a_test_file_of_the_package_depends_on_the_file_it_tests(self) -> None:
        """/* REQ-GO-9601: A _test.go file of the same directory and package depends on the file whose names it uses. */"""
        built = _Built({
            "go.mod": "module example.com/app\n\ngo 1.22\n",
            "internal/store/store.go": "package store\n\nfunc Open() int { return newDB() }\n",
            "internal/store/db.go": "package store\n\nfunc newDB() int { return 1 }\n",
            "internal/store/store_test.go": (
                'package store\n\nimport "testing"\n\n'
                "func TestOpen(t *testing.T) { if Open() != 1 { t.Fail() } }\n"
            ),
            "internal/store/fixtures_test.go": "package store\n\nfunc fixture() int { return 2 }\n",
            "internal/store/more_test.go": (
                'package store\n\nimport "testing"\n\nfunc TestMore(t *testing.T) { _ = fixture() }\n'
            ),
        })
        self.assertEqual({"internal/store/store_test.go"}, built.dependents("internal/store/store.go"))
        self.assertEqual({"internal/store/store.go"}, built.dependents("internal/store/db.go"))
        self.assertEqual({"internal/store/more_test.go"}, built.dependents("internal/store/fixtures_test.go"))

    def test_types_variables_constants_and_receivers_link_the_files_of_a_package(self) -> None:
        """/* REQ-GO-9602: Types, variables, constants and the receiver of a method link the files of a Go package. */"""
        built = _Built({
            "go.mod": "module example.com/app\n\ngo 1.22\n",
            "store/types.go": "package store\n\ntype Store struct{ rows []Row }\n\ntype Row struct{}\n",
            "store/limits.go": "package store\n\nconst MaxRows = 10\n\nvar defaults = map[string]int{}\n",
            "store/close.go": "package store\n\nfunc (s *Store) Close() {}\n",
            "store/fill.go": (
                "package store\n\nfunc fill(rows []Row) int {\n"
                "\tif len(rows) > MaxRows { return defaults[\"rows\"] }\n\treturn 0\n}\n"
            ),
            "store/keys.go": "package store\n\nvar byLimit = map[int]string{MaxRows: \"max\"}\n",
        })
        self.assertEqual({"store/types.go"}, built.dependencies("store/close.go"))
        self.assertEqual({"store/types.go", "store/limits.go"}, built.dependencies("store/fill.go"))
        self.assertEqual({"store/limits.go"}, built.dependencies("store/keys.go"))

    def test_a_local_name_or_an_ambiguous_name_gives_no_edge(self) -> None:
        """/* REQ-GO-9603: A name a Go file binds locally, a struct field key, a name of another package or directory and a name two files define give no edge. */"""
        built = _Built({
            "go.mod": "module example.com/app\n\ngo 1.22\n",
            "app/names.go": "package app\n\nvar count = 1\n\nvar Name = \"n\"\n\ntype Config struct{ Name string }\n",
            "app/linux.go": "package app\n\nfunc open() int { return 1 }\n",
            "app/other.go": "package app\n\nfunc open() int { return 2 }\n",
            "app/local.go": (
                'package app\n\nimport "example.com/app/lib"\n\n'
                "func total(count int) int {\n"
                "\tfor _, Name := range []string{} { _ = Name }\n"
                "\treturn count + lib.Limit\n}\n\n"
                "func shadow() lib.Config { count := 2; _ = count; return lib.Config{} }\n"
            ),
            "app/literal.go": "package app\n\nfunc build() Config { return Config{Name: \"x\"} }\n",
            "app/caller.go": "package app\n\nfunc run() int { return open() }\n",
            "lib/lib.go": "package lib\n\nconst Limit = 3\n\ntype Config struct{}\n",
            "tool/main.go": "package main\n\nfunc main() { _ = count }\n",
            "tool/gen.go": "//go:build ignore\n\npackage gen\n\nvar count = 2\n",
        })
        self.assertEqual({"lib/lib.go"}, built.dependencies("app/local.go"))
        self.assertEqual({"app/names.go"}, built.dependencies("app/literal.go"))
        self.assertEqual(set(), built.dependencies("app/caller.go"))
        self.assertEqual(set(), built.dependencies("tool/main.go"))

    def test_a_name_is_local_only_from_its_declaration_on(self) -> None:
        """/* REQ-GO-9604: A Go name used before a local declaration of the same name, or in another block, is the name of the package. */"""
        built = _Built({
            "go.mod": "module example.com/app\n\ngo 1.22\n",
            "app/names.go": "package app\n\nvar limit = 1\n\nfunc scale(n int) int { return n }\n",
            "app/first.go": "package app\n\nfunc first() int { limit := limit + 1; return limit }\n",
            "app/block.go": (
                "package app\n\nfunc block(on bool) int {\n"
                "\tif on { scale := 2; return scale }\n\treturn scale(3)\n}\n"
            ),
            "app/test_only_test.go": "package app\n\nvar only = 1\n",
            "app/uses_only.go": "package app\n\nfunc uses() int { return only }\n",
        })
        self.assertEqual({"app/names.go"}, built.dependencies("app/first.go"))
        self.assertEqual({"app/names.go"}, built.dependencies("app/block.go"))
        self.assertEqual(set(), built.dependencies("app/uses_only.go"))


_GO_MAIN = 'package main\n\nimport "example.com/two/pkg/a"\n\nfunc main() { a.A() }\n'


class GoModuleFileTest(unittest.TestCase):
    def test_a_comment_after_the_module_path_is_not_part_of_it(self) -> None:
        """/* REQ-GO-9605: The module line of go.mod is read with a trailing comment, with quotes and in the block form. */"""
        for module_line in (
            "module example.com/two // the second module\n",
            'module "example.com/two"\n',
            "// a module\nmodule (\n\texample.com/two // here\n)\n",
        ):
            with self.subTest(module_line=module_line):
                built = _Built({
                    "go.mod": module_line + "\ngo 1.22\n",
                    "main.go": _GO_MAIN,
                    "pkg/a/a.go": "package a\n\nfunc A() {}\n",
                })
                self.assertEqual({"main.go"}, built.dependents("pkg/a/a.go"))

    def test_a_module_file_that_is_not_utf8_is_reported_and_the_build_goes_on(self) -> None:
        """/* REQ-GO-9606: A go.mod with a byte that is not UTF-8 gives a warning; the other modules are read and the graph is built. */"""
        built = _Built({
            "go.mod": "module example.com/two\n\ngo 1.22\n",
            "main.go": _GO_MAIN,
            "pkg/a/a.go": "package a\n\nfunc A() {}\n",
            "legacy/go.mod": b"// caf\xe9\nmodule example.com/legacy\n",
            "legacy/main.go": "package main\n\nfunc main() {}\n",
        })
        self.assertEqual({"main.go"}, built.dependents("pkg/a/a.go"))
        self.assertIn("Cannot read Go module identity", built.log.getvalue())
        self.assertIn(os.path.join("legacy", "go.mod"), built.log.getvalue())

    def test_a_long_module_file_is_read_in_linear_time(self) -> None:
        """/* REQ-GO-9607: A go.mod of 100 KB of blank lines, or of one long line, is read within a second. */"""
        from scripts.lang.go.imports import _module_path

        for content, expected in (
            ("\n" * 100_000 + "module example.com/late\n", "example.com/late"),
            ("module" + " " * 100_000 + "example.com/far\n" + " " * 100_000, "example.com/far"),
        ):
            started = time.perf_counter()
            path = _module_path(content)
            self.assertLess(time.perf_counter() - started, 1.0)
            self.assertEqual(expected, path)


class CSharpTypeBindingTest(unittest.TestCase):
    def test_a_partial_class_links_every_file_that_declares_it(self) -> None:
        """/* REQ-CSHARP-9610: A type declared partial in several files of one namespace and project links its user to each of them; other duplicates stay unlinked. */"""
        built = _Built({
            "Domain/Customer.Part1.cs": "namespace Acme.Domain;\n\npublic partial class Customer\n{\n    public string Name = \"\";\n}\n",
            "Domain/Customer.Part2.cs": "namespace Acme.Domain;\n\npublic partial class Customer\n{\n    public int Age;\n}\n",
            "Domain/Twice.A.cs": "namespace Acme.Domain;\n\npublic class Twice {}\n",
            "Domain/Twice.B.cs": "namespace Acme.Domain;\n\npublic class Twice {}\n",
            "Services/OrderService.cs": (
                "using Acme.Domain;\n\nnamespace Acme.Services;\n\n"
                "public class OrderService\n{\n    Customer c;\n    Twice t;\n}\n"
            ),
            "Services/Same.cs": "namespace Acme.Domain;\n\npublic class Same\n{\n    Customer c;\n}\n",
            "Services/Static.cs": (
                "using static Acme.Domain.Customer;\n\nnamespace Acme.Services;\n\npublic class Static {}\n"
            ),
        })
        parts = {"Domain/Customer.Part1.cs", "Domain/Customer.Part2.cs"}
        self.assertEqual(parts, built.dependencies("Services/OrderService.cs"))
        self.assertEqual(parts, built.dependencies("Services/Same.cs"))
        self.assertEqual(parts, built.dependencies("Services/Static.cs"))

    def test_partial_declarations_of_two_projects_are_two_types(self) -> None:
        """/* REQ-CSHARP-9611: Partial declarations of one name in two projects are not one type: a file binds the one of its own project, a file of a third project none. */"""
        built = _Built({
            "Api/Api.csproj": "<Project Sdk=\"Microsoft.NET.Sdk.Web\" />\n",
            "Api/Program.cs": "public partial class Program {}\n",
            "Api/Startup.cs": "namespace Api;\n\npublic class Startup\n{\n    Program p;\n}\n",
            "Jobs/Jobs.csproj": "<Project Sdk=\"Microsoft.NET.Sdk.Web\" />\n",
            "Jobs/Program.cs": "public partial class Program {}\n",
            "Tests/Tests.csproj": "<Project Sdk=\"Microsoft.NET.Sdk\" />\n",
            "Tests/ApiTests.cs": "namespace Tests;\n\npublic class ApiTests\n{\n    Program p;\n}\n",
        })
        self.assertEqual({"Api/Program.cs"}, built.dependencies("Api/Startup.cs"))
        self.assertEqual(set(), built.dependencies("Tests/ApiTests.cs"))

    def test_a_type_of_an_enclosing_namespace_is_found(self) -> None:
        """/* REQ-CSHARP-9612: A C# name binds to a type of an enclosing namespace and of the global namespace. */"""
        built = _Built({
            "Services/OrderService.cs": "namespace Acme.Services;\n\npublic class OrderService {}\n",
            "Root.cs": "public class Root {}\n",
            "Services/Inner/Deep.cs": (
                "namespace Acme.Services.Inner;\n\npublic class Deep\n{\n    OrderService s;\n    Root r;\n}\n"
            ),
            "Program.cs": "var root = new Root();\n",
        })
        self.assertEqual({"Services/OrderService.cs", "Root.cs"}, built.dependencies("Services/Inner/Deep.cs"))
        self.assertEqual({"Root.cs"}, built.dependencies("Program.cs"))

    def test_a_using_inside_a_namespace_is_read(self) -> None:
        """/* REQ-CSHARP-9613: A using directive written inside a namespace block, or after a file-scoped namespace, brings in the types of its namespace. */"""
        built = _Built({
            "Domain/Order.cs": "namespace Acme.Domain;\n\npublic class Order {}\n",
            "Services/UsingInside.cs": (
                "namespace Acme.Services2\n{\n    using Acme.Domain;\n\n"
                "    public class UsingInside\n    {\n        Order o;\n    }\n}\n"
            ),
            "Services/UsingAfter.cs": (
                "namespace Acme.Services3;\n\nusing Acme.Domain;\n\npublic class UsingAfter\n{\n    Order o;\n}\n"
            ),
        })
        self.assertEqual({"Services/UsingInside.cs", "Services/UsingAfter.cs"}, built.dependents("Domain/Order.cs"))
        self.assertIn("Acme.Domain", built.results["Services/UsingInside.cs"].imports)

    def test_a_global_using_holds_for_the_files_of_its_project(self) -> None:
        """/* REQ-CSHARP-9614: A global using of one file brings in its namespace for every file of the same project, and for no file of another project. */"""
        built = _Built({
            "App/App.csproj": "<Project Sdk=\"Microsoft.NET.Sdk\" />\n",
            "App/GlobalUsings.cs": "global using Acme.Domain;\n",
            "App/Domain/Money.cs": "namespace Acme.Domain;\n\npublic record Money(decimal V);\n",
            "App/Services/GlobalUser.cs": "namespace Acme.Other;\n\npublic class GlobalUser\n{\n    Money m;\n}\n",
            "App/Program.cs": "var m = new Money(1);\n",
            "Tool/Tool.csproj": "<Project Sdk=\"Microsoft.NET.Sdk\" />\n",
            "Tool/Runner.cs": "namespace Acme.Tool;\n\npublic class Runner\n{\n    Money m;\n}\n",
        })
        self.assertEqual({"App/Services/GlobalUser.cs", "App/Program.cs"}, built.dependents("App/Domain/Money.cs"))

    def test_a_type_of_the_own_namespace_hides_the_one_a_using_brings(self) -> None:
        """/* REQ-CSHARP-9615: A name binds to the type of the nearest namespace: a type a using directive brings in gives no edge when the own namespace declares the name, and a name two usings bring in gives none. */"""
        built = _Built({
            "Dup/A/Thing.cs": "namespace Acme.A;\n\npublic class Thing {}\n\npublic class OnlyA {}\n",
            "Dup/B/Thing.cs": "namespace Acme.B;\n\npublic class Thing {}\n",
            "Dup/C/Thing.cs": "namespace Acme.C;\n\npublic class Thing {}\n",
            "Dup/B/UsesThing.cs": (
                "using Acme.A;\n\nnamespace Acme.B;\n\npublic class UsesThing\n{\n    Thing t;\n    OnlyA a;\n}\n"
            ),
            "Dup/D/Both.cs": (
                "using Acme.A;\nusing Acme.C;\n\nnamespace Acme.D;\n\npublic class Both\n{\n    int Thing;\n}\n"
            ),
            "Dup/D/Named.cs": (
                "using Other = Acme.A.Thing;\n\nnamespace Acme.B;\n\npublic class Named\n{\n    Other o;\n    Thing t;\n}\n"
            ),
        })
        self.assertEqual({"Dup/B/Thing.cs", "Dup/A/Thing.cs"}, built.dependencies("Dup/B/UsesThing.cs"))
        self.assertEqual(set(), built.dependencies("Dup/D/Both.cs"))
        self.assertEqual({"Dup/A/Thing.cs", "Dup/B/Thing.cs"}, built.dependencies("Dup/D/Named.cs"))


_JAVA_CORE = {
    "core/src/main/java/com/acme/core/Limits.java": (
        "package com.acme.core;\n\npublic class Limits {\n    public static final int MAX = 5;\n"
        "    public static int clamp(int v) { return Math.min(v, MAX); }\n}\n"
    ),
    "core/src/main/java/com/acme/core/OrderStatus.java": "package com.acme.core;\n\npublic enum OrderStatus { NEW, PAID }\n",
    "core/src/main/java/com/acme/core/Annot.java": "package com.acme.core;\n\npublic @interface Annot {}\n",
    "core/src/main/java/com/acme/core/Jobs.java": "package com.acme.core;\n\npublic class Jobs {\n    public static void run() {}\n}\n",
}
_JAVA_FOLDER = "core/src/main/java/com/acme/core/"


class JavaStaticUseTest(unittest.TestCase):
    def test_a_class_used_through_static_members_is_a_dependency(self) -> None:
        """/* REQ-JAVA-9620: A Java class of the own package used only through a static method, a constant, an enum constant or a method reference is a dependency. */"""
        built = _Built({
            **_JAVA_CORE,
            _JAVA_FOLDER + "OrderService.java": (
                "package com.acme.core;\n\npublic class OrderService {\n"
                "    int limit() { return Limits.clamp(Limits.MAX); }\n"
                "    boolean isNew(Object s) { return s == OrderStatus.NEW; }\n"
                "    Runnable job = Jobs::run;\n}\n"
            ),
        })
        self.assertEqual(
            {_JAVA_FOLDER + "Limits.java", _JAVA_FOLDER + "OrderStatus.java", _JAVA_FOLDER + "Jobs.java"},
            built.dependencies(_JAVA_FOLDER + "OrderService.java"),
        )

    def test_a_wildcard_import_brings_in_a_class_used_through_static_members(self) -> None:
        """/* REQ-JAVA-9621: A class a wildcard import brings in is a dependency when only its static members are used. */"""
        built = _Built({
            **_JAVA_CORE,
            "app/src/main/java/com/acme/app/Wild.java": (
                "package com.acme.app;\n\nimport com.acme.core.*;\n\npublic class Wild {\n"
                "    int f() { return Limits.MAX; }\n    Object g() { return OrderStatus.NEW; }\n}\n"
            ),
        })
        self.assertEqual(
            {_JAVA_FOLDER + "Limits.java", _JAVA_FOLDER + "OrderStatus.java"},
            built.dependencies("app/src/main/java/com/acme/app/Wild.java"),
        )

    def test_an_annotation_of_the_own_package_is_a_dependency(self) -> None:
        """/* REQ-JAVA-9622: An annotation of the own package written on a declaration is a dependency. */"""
        built = _Built({
            **_JAVA_CORE,
            _JAVA_FOLDER + "Order.java": (
                "package com.acme.core;\n\n@Annot\npublic class Order {\n    @Annot(\"x\") int total;\n}\n"
            ),
        })
        self.assertEqual({_JAVA_FOLDER + "Annot.java"}, built.dependencies(_JAVA_FOLDER + "Order.java"))

    def test_a_variable_named_like_a_class_gives_no_edge(self) -> None:
        """/* REQ-JAVA-9623: A field, parameter or variable of the file whose name is that of a class of the package is not a use of the class. */"""
        built = _Built({
            **_JAVA_CORE,
            _JAVA_FOLDER + "Holder.java": (
                "package com.acme.core;\n\npublic class Holder {\n    static final java.util.List<String> Jobs = null;\n"
                "    int size() { return Jobs.size(); }\n"
                "    int low(String limits) { return limits.length(); }\n}\n"
            ),
        })
        self.assertEqual(set(), built.dependencies(_JAVA_FOLDER + "Holder.java"))

    def test_files_of_the_default_package_are_linked(self) -> None:
        """/* REQ-JAVA-9624: A file without a package declaration depends on the class of the default package it names: the one of its folder when two folders declare the name. */"""
        built = _Built({
            "one/A.java": "public class A {\n    B b;\n    int n = Tool.COUNT;\n}\n",
            "one/B.java": "public class B {}\n",
            "one/Tool.java": "public class Tool {\n    static int COUNT = 1;\n}\n",
            "two/Tool.java": "public class Tool {\n    static int COUNT = 2;\n}\n",
            "two/C.java": "public class C {\n    B b;\n}\n",
            "three/D.java": "public class D {\n    Tool t;\n}\n",
            "four/src/E.java": "package four;\n\npublic class E {\n    B b;\n}\n",
        })
        self.assertEqual({"one/B.java", "one/Tool.java"}, built.dependencies("one/A.java"))
        self.assertEqual({"one/B.java"}, built.dependencies("two/C.java"))
        self.assertEqual(set(), built.dependencies("three/D.java"))
        self.assertEqual(set(), built.dependencies("four/src/E.java"))


_PHP_APP = {
    "composer.json": '{"autoload": {"psr-4": {"App\\\\": "app/"}}}\n',
    "app/Models/Post.php": "<?php\n\nnamespace App\\Models;\n\nclass Post {}\n",
    "app/Http/PostController.php": (
        "<?php\n\nnamespace App\\Http;\n\nuse App\\Models\\Post;\n\n"
        "class PostController\n{\n    public function show(): Post { return new Post(); }\n}\n"
    ),
}
_DEEP_JSON = "[" * 100_000


class ManifestReadingTest(unittest.TestCase):
    def test_a_composer_file_that_is_not_utf8_is_reported_and_the_build_goes_on(self) -> None:
        """/* REQ-PHP-9630: A composer.json with a byte that is not UTF-8 gives a warning; the other manifests are read and the graph is built. */"""
        built = _Built({**_PHP_APP, "packages/legacy/composer.json": b'{"name": "acme/caf\xe9"}\n'})
        self.assertEqual({"app/Http/PostController.php"}, built.dependents("app/Models/Post.php"))
        self.assertIn("Cannot read composer autoload", built.log.getvalue())
        self.assertIn(os.path.join("packages", "legacy", "composer.json"), built.log.getvalue())

    def test_a_composer_file_nested_too_deep_is_reported_and_the_build_goes_on(self) -> None:
        """/* REQ-PHP-9631: A composer.json nested deeper than the JSON reader goes gives a warning, not an aborted build. */"""
        built = _Built({**_PHP_APP, "packages/deep/composer.json": _DEEP_JSON})
        self.assertEqual({"app/Http/PostController.php"}, built.dependents("app/Models/Post.php"))
        self.assertIn(os.path.join("packages", "deep", "composer.json"), built.log.getvalue())

    def test_a_pubspec_that_is_not_utf8_is_reported_and_the_build_goes_on(self) -> None:
        """/* REQ-DART-9632: A pubspec.yaml with a byte that is not UTF-8 gives a warning; the other packages are read and the graph is built. */"""
        built = _Built({
            "pubspec.yaml": "name: app\n",
            "lib/main.dart": "import 'package:app/model.dart';\n\nvoid main() { Model(); }\n",
            "lib/model.dart": "class Model {}\n",
            "legacy/pubspec.yaml": b"name: caf\xe9\n",
            "legacy/lib/old.dart": "class Old {}\n",
        })
        self.assertEqual({"lib/main.dart"}, built.dependents("lib/model.dart"))
        self.assertIn("Cannot read Dart package identity", built.log.getvalue())
        self.assertIn(os.path.join("legacy", "pubspec.yaml"), built.log.getvalue())

    def test_a_compilation_database_nested_too_deep_is_reported_and_the_build_goes_on(self) -> None:
        """/* REQ-CPP-9633: A compile_commands.json nested deeper than the JSON reader goes, or not UTF-8, gives a warning, not an aborted build. */"""
        for database in (_DEEP_JSON, b'[{"file": "caf\xe9.cpp"}]'):
            with self.subTest(database=database[:12]):
                built = _Built({
                    "compile_commands.json": database,
                    "src/core.h": "int core();\n",
                    "src/core.cpp": '#include "core.h"\nint core() { return 1; }\n',
                })
                self.assertEqual({"src/core.cpp"}, built.dependents("src/core.h"))
                self.assertIn("Cannot read the compilation database", built.log.getvalue())


if __name__ == "__main__":
    unittest.main()
