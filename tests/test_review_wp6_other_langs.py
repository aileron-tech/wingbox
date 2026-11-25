"""Defects found in the review of 0.19.1 in the adapters of Go, C#, Java, PHP, Ruby, Dart, C and C++ and Pascal,
and in the reading of the CMake files."""
from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
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
        """/* REQ-GO-9603: A name a Go file binds locally (a parameter, a variable, a type parameter, also the one a method receiver names), a struct field key, a name of another package or directory and a name two files define give no edge. */"""
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
            "app/generic.go": "package app\n\ntype Box[T any] struct{ v T }\n\nfunc (b *Box[T]) Get() T { return b.v }\n",
            "app/t.go": "package app\n\ntype T struct{}\n",
            "tool/main.go": "package main\n\nfunc main() { _ = count }\n",
            "tool/gen.go": "//go:build ignore\n\npackage gen\n\nvar count = 2\n",
        })
        self.assertEqual({"lib/lib.go"}, built.dependencies("app/local.go"))
        self.assertEqual({"app/names.go"}, built.dependencies("app/literal.go"))
        self.assertEqual(set(), built.dependencies("app/caller.go"))
        self.assertEqual(set(), built.dependencies("app/generic.go"))
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


_DEPTH = 3_000   # operands of one expression, or branches of one conditional: three times what aborted a build


def _entities(built: _Built, path: str) -> dict[str, dict]:
    return {entity["name"]: entity for entity in built.results[path].entities}


class DeepTreeTest(unittest.TestCase):
    def test_a_php_expression_of_three_thousand_operands_is_read(self) -> None:
        """/* REQ-PHP-9640: A PHP file with a concatenation of 3,000 operands is parsed: its class, its methods and its dependencies are those of the file. */"""
        chain = " .\n        ".join("'a'" for _ in range(_DEPTH))
        built = _Built({
            **_PHP_APP,
            "app/Reports/Report.php": (
                "<?php\n\nnamespace App\\Reports;\n\nuse App\\Models\\Post;\n\nclass Report\n{\n"
                f"    const TEXT = {chain};\n\n"
                "    public function post(): Post { return new Post(); }\n}\n"
            ),
        })
        self.assertEqual({"app/Models/Post.php"}, built.dependencies("app/Reports/Report.php"))
        self.assertEqual(["post"], _entities(built, "app/Reports/Report.php")["App\\Reports\\Report"].get("methods"))

    def test_a_ruby_expression_of_three_thousand_operands_is_read(self) -> None:
        """/* REQ-RUBY-9641: A Ruby file with a sum of 3,000 operands is parsed: its class, its constant and its dependencies are those of the file. */"""
        chain = " +\n    ".join("'a'" for _ in range(_DEPTH))
        built = _Built({
            "lib/limits.rb": "module Limits\n  MAX = 3\nend\n",
            "lib/report.rb": (
                "require_relative 'limits'\n\nclass Report\n"
                f"  TEXT = {chain}\n\n"
                "  def size\n    Limits::MAX\n  end\nend\n"
            ),
        })
        self.assertEqual({"lib/limits.rb"}, built.dependencies("lib/report.rb"))
        entities = _entities(built, "lib/report.rb")
        self.assertEqual(["size"], entities["Report"].get("methods"))
        self.assertIn("Report::TEXT", entities)

    def test_a_cpp_conditional_of_three_thousand_branches_is_read(self) -> None:
        """/* REQ-CPP-9642: A C++ header with 3,000 #elif branches, at file level and in a class, is parsed: the functions of every branch and the include are those of the file. */"""
        branches = "#if MODE == 0\nint mode0();\n" + "".join(
            f"#elif MODE == {index}\nint mode{index}();\n" for index in range(1, _DEPTH)
        ) + "#endif\n"
        members = "#if MODE == 0\n    int member0();\n" + "".join(
            f"#elif MODE == {index}\n    int member{index}();\n" for index in range(1, _DEPTH)
        ) + "#endif\n"
        built = _Built({
            "src/dep.h": "int dep();\n",
            "src/modes.h": f'#include "dep.h"\n{branches}\nstruct Holder {{\n{members}}};\n\nint after();\n',
        })
        self.assertEqual({"src/dep.h"}, built.dependencies("src/modes.h"))
        entities = _entities(built, "src/modes.h")
        for name in ("mode0", f"mode{_DEPTH - 1}", "after", "Holder"):
            self.assertIn(name, entities)
        self.assertEqual(_DEPTH, len(entities["Holder"]["methods"]))
        self.assertEqual(["member0", f"member{_DEPTH - 1}"], [entities["Holder"]["methods"][0], entities["Holder"]["methods"][-1]])


def _cmake(files: dict[str, str]):
    """The targets and the tests `generation.cmake_targets` reads from a folder of files."""
    from scripts import generation

    with tempfile.TemporaryDirectory() as directory:
        root = Path(os.path.realpath(directory))
        _write(root, files)

        def read_text(relative: str) -> str | None:
            return (root / relative).read_text(encoding="utf-8")

        cmake_files = [name for name in files if name.endswith(("CMakeLists.txt", ".cmake"))]
        targets, tests = generation.cmake_targets(str(root), cmake_files, read_text)
    return {target.name: target for target in targets}, {test.name: test for test in tests}


_CMAKE_SOURCES = {f"lib/{name}": "int f();\n" for name in ("core.cpp", "detail.cpp", "extra.cpp", "src/deep.cpp", "one.cc")}


class CMakeTargetsTest(unittest.TestCase):
    def test_sources_given_through_a_variable_set_once_are_read(self) -> None:
        """/* REQ-CS-9650: A target whose sources are a variable that the same CMake file sets once, at its top level, with literal values has those sources; a variable with one value is also read inside a path. */"""
        targets, _tests = _cmake({
            **_CMAKE_SOURCES,
            "lib/CMakeLists.txt": (
                "# sources through a variable\n"
                "set(CORE_SOURCES core.cpp\n    detail.cpp)\n"
                "add_library(core STATIC ${CORE_SOURCES})\n"
                "set(SRC_DIR src)\n"
                'set(QUOTED "extra.cpp;${CMAKE_CURRENT_SOURCE_DIR}/core.cpp")\n'
                "add_library(deep STATIC ${SRC_DIR}/deep.cpp \"${QUOTED}\")\n"
                "add_library(plain STATIC extra.cpp)\n"
                "add_library(iface INTERFACE)\n"
            ),
        })
        self.assertEqual(("lib/core.cpp", "lib/detail.cpp"), targets["core"].sources)
        self.assertEqual(("lib/src/deep.cpp", "lib/extra.cpp", "lib/core.cpp"), targets["deep"].sources)
        self.assertEqual([False, False, False, False], [targets[name].unread for name in ("core", "deep", "plain", "iface")])

    def test_a_variable_that_is_not_set_once_literally_stays_unread_and_marks_the_target(self) -> None:
        """/* REQ-CS-9651: A variable set twice, appended to, set in a block, set after its use, filled by file(GLOB) or set in no place of the file is not substituted, and neither is a generator expression: the target is marked as one whose sources were not all read. */"""
        targets, _tests = _cmake({
            **_CMAKE_SOURCES,
            "lib/CMakeLists.txt": (
                "set(TWICE core.cpp)\nset(TWICE detail.cpp)\nadd_library(twice STATIC ${TWICE})\n"
                "set(GROWN core.cpp)\nlist(APPEND GROWN detail.cpp)\nadd_library(grown STATIC ${GROWN})\n"
                "if(WIN32)\n  set(GUARDED core.cpp)\nendif()\nadd_library(guarded STATIC ${GUARDED})\n"
                "add_library(early STATIC ${LATE})\nset(LATE core.cpp)\n"
                "file(GLOB GLOBBED *.cc)\nadd_library(globbed STATIC)\ntarget_sources(globbed PRIVATE ${GLOBBED})\n"
                "add_library(outside STATIC extra.cpp ${FROM_PARENT})\n"
                "add_library(expression OBJECT $<$<CONFIG:Debug>:core.cpp> detail.cpp)\n"
                "set(NESTED ${OTHER_LIST})\nadd_library(nested STATIC ${NESTED})\n"
                "add_library(made STATIC extra.cpp ${CMAKE_CURRENT_BINARY_DIR}/made.cpp)\n"
            ),
        })
        for name in ("twice", "grown", "guarded", "early", "globbed", "nested"):
            self.assertEqual(((), True), (targets[name].sources, targets[name].unread), name)
        self.assertEqual((("lib/extra.cpp",), True), (targets["outside"].sources, targets["outside"].unread))
        self.assertEqual((("lib/detail.cpp",), True), (targets["expression"].sources, targets["expression"].unread))
        self.assertEqual((("lib/extra.cpp",), False), (targets["made"].sources, targets["made"].unread))

    def test_a_bracket_comment_holds_no_target(self) -> None:
        """/* REQ-CS-9652: A call written in a bracket comment `#[[ ... ]]` or `#[==[ ... ]==]` of a CMake file creates no target and no test. */"""
        targets, tests = _cmake({
            **_CMAKE_SOURCES,
            "lib/CMakeLists.txt": (
                "add_library(before STATIC core.cpp)\n"
                "#[[ a bracket comment\nadd_executable(ghost detail.cpp)\n]]\n"
                "#[==[ another ]] one\nadd_test(NAME ghost_run COMMAND before)\n]==] add_library(after STATIC extra.cpp)\n"
                "add_test(NAME real COMMAND before) #[[ unclosed\nadd_library(lost STATIC core.cpp)\n"
            ),
        })
        self.assertEqual(["after", "before"], sorted(targets))
        self.assertEqual(["real"], sorted(tests))

    def test_a_hash_in_a_quoted_argument_is_no_comment(self) -> None:
        """/* REQ-CS-9653: A `#` inside a quoted argument of a CMake call does not start a comment: the call and the calls after it are read. */"""
        targets, tests = _cmake({
            **_CMAKE_SOURCES,
            "lib/CMakeLists.txt": (
                "add_executable(unit_tests core.cpp)   # a comment with a bracket )\n"
                'add_test(NAME hash COMMAND unit_tests "--tag=#1")\n'
                'add_test(NAME escaped COMMAND unit_tests "--name=\\"#2\\"") # "quoted" comment\n'
                "add_test(NAME after_hash COMMAND unit_tests)\n"
            ),
        })
        self.assertEqual(["unit_tests"], sorted(targets))
        self.assertEqual({"hash": "unit_tests", "escaped": "unit_tests", "after_hash": "unit_tests"},
                         {name: test.target for name, test in tests.items()})

    def test_a_quoted_test_name_is_kept_whole(self) -> None:
        """/* REQ-CS-9654: The name of `add_test(NAME "quoted name" ...)` is the text between the quotes. */"""
        _targets, tests = _cmake({
            **_CMAKE_SOURCES,
            "lib/CMakeLists.txt": (
                "add_executable(unit_tests core.cpp)\n"
                'add_test(NAME "quoted name" COMMAND unit_tests --x)\n'
                'add_test("short quoted" unit_tests)\n'
            ),
        })
        self.assertEqual(["quoted name", "short quoted"], sorted(tests))
        self.assertEqual("unit_tests", tests["quoted name"].target)


class CMakeAnswerTest(unittest.TestCase):
    """The same through the command: the graph facts and what `impact --only tests` answers."""

    FILES = {
        "CMakeLists.txt": "cmake_minimum_required(VERSION 3.20)\nproject(demo)\nadd_subdirectory(lib)\n",
        "lib/CMakeLists.txt": (
            "set(CORE_SOURCES core.cpp)\n"
            "add_library(core STATIC ${CORE_SOURCES})\n"
            "file(GLOB GLOB_SRC *.cc)\n"
            "add_library(glob_lib STATIC ${GLOB_SRC})\n"
            "add_executable(unit_tests test_core.cpp)\n"
            'add_test(NAME "quoted name" COMMAND unit_tests "--tag=#1")\n'
        ),
        "lib/core.h": "int core();\n",
        "lib/core.cpp": '#include "core.h"\nint core() { return 1; }\n',
        "lib/globbed.cc": "int globbed() { return 2; }\n",
        "lib/test_core.cpp": '#include "core.h"\nint main() { return core(); }\n',
    }

    @classmethod
    def setUpClass(cls) -> None:
        folder = tempfile.TemporaryDirectory()
        cls.addClassCleanup(folder.cleanup)
        cls.root = Path(os.path.realpath(folder.name))
        _write(cls.root, cls.FILES)
        cls.built = cls._run("build", ".")
        cls.graph = (cls.root / ".wingbox" / "index.sexp").read_text(encoding="utf-8")

    @classmethod
    def _run(cls, *args: str) -> subprocess.CompletedProcess:
        source = Path(__file__).resolve().parents[1] / "src"
        environment = dict(os.environ, PYTHONPATH=str(source) + os.pathsep + os.environ.get("PYTHONPATH", ""), WINGBOX_JOBS="1")
        return subprocess.run([sys.executable, "-m", "wingbox", *args], capture_output=True, env=environment,
                              cwd=str(cls.root), timeout=180, text=True)

    def test_the_graph_marks_a_target_whose_sources_were_not_read(self) -> None:
        """/* REQ-CS-9655: The `target` fact of a target whose source list was not all read carries `:sources unread`; a target read through a variable set once lists its sources, and a test named with a space is written quoted. */"""
        facts = [line for line in self.graph.splitlines() if line.startswith(("(target ", "(ctest "))]
        self.assertEqual(4, len(facts), self.built.stderr)
        self.assertRegex(self.graph, r'\(target core :kind library :in "lib/CMakeLists.txt" \w+\)')
        self.assertIn('(target glob_lib :kind library :sources unread :in "lib/CMakeLists.txt")', self.graph)
        self.assertIn('(ctest "quoted name" :target unit_tests :in "lib/CMakeLists.txt")', self.graph)

    def test_impact_names_the_target_of_the_variable_and_the_unread_targets(self) -> None:
        """/* REQ-CS-9656: `wingbox impact <file> --only tests` names a target that holds the file through a variable, and after it the targets whose sources were not all read: `(unread-targets <file> <target> ...)`, `unread_targets` in JSON, a line in the text. */"""
        compact = self._run("impact", "lib/core.cpp", "--only", "tests").stdout
        self.assertIn("(affected-targets lib/core.cpp core unit_tests)", compact)
        self.assertIn('(affected-tests lib/core.cpp "quoted name")', compact)
        self.assertIn("(unread-targets lib/core.cpp glob_lib)", compact)
        document = json.loads(self._run("impact", "lib/core.cpp", "--only", "tests", "--json").stdout)
        answer = document["impact"][0] if "impact" in document else document
        self.assertEqual((["core", "unit_tests"], ["quoted name"], ["glob_lib"]),
                         (answer["targets"], answer["tests"], answer["unread_targets"]))
        human = self._run("impact", "lib/core.cpp", "--only", "tests", "--human").stdout
        self.assertIn("Build targets whose sources were not all read: glob_lib", human)


_PHP_HELPERS = {
    "composer.json": '{"autoload": {"psr-4": {"App\\\\": "app/"}, "files": ["app/Support/helpers.php"]}}\n',
    "app/Support/helpers.php": (
        "<?php\n\nif (! function_exists('format_money')) {\n"
        "    function format_money(int $v): string { return (string) $v; }\n}\n"
    ),
    "app/Support/functions.php": (
        "<?php\n\nnamespace App\\Support;\n\nfunction ns_helper(): int { return 1; }\n\n"
        "function unused_helper(): int { return strlen('x'); }\n\nconst LIMIT = 5;\n"
    ),
}


class PhpFunctionsTest(unittest.TestCase):
    def test_functions_and_constants_of_a_file_are_entities(self) -> None:
        """/* REQ-PHP-9660: A PHP function and a constant declared outside a class are entities of their file, a function inside `if (! function_exists())` too; a method and a class constant are not listed twice. */"""
        built = _Built({
            **_PHP_HELPERS,
            "app/Models/Post.php": "<?php\n\nnamespace App\\Models;\n\nclass Post\n{\n    const KIND = 'p';\n\n    public function title() { return ''; }\n}\n",
        })
        self.assertEqual("function", _entities(built, "app/Support/helpers.php")["format_money"]["kind"])
        functions = _entities(built, "app/Support/functions.php")
        self.assertEqual("function", functions["App\\Support\\ns_helper"]["kind"])
        self.assertEqual(["constant"], functions["App\\Support\\LIMIT"].get("decorators"))
        self.assertEqual(["App\\Models\\Post", "App\\Models\\Post::title"], list(_entities(built, "app/Models/Post.php")))

    def test_use_function_and_use_const_are_dependencies(self) -> None:
        """/* REQ-PHP-9661: `use function` and `use const`, also in a group, make the file depend on the file that declares the function or the constant, with the line of the statement. */"""
        built = _Built({
            **_PHP_HELPERS,
            "app/Support/more.php": "<?php\n\nnamespace App\\More;\n\nfunction first() {}\n\nfunction second() {}\n",
            "app/Http/UserController.php": (
                "<?php\n\nnamespace App\\Http;\n\nuse function App\\Support\\ns_helper;\nuse const App\\Support\\LIMIT;\n\n"
                "class UserController\n{\n    public function index() { return 1; }\n}\n"
            ),
            "app/Http/Grouped.php": (
                "<?php\n\nnamespace App\\Http;\n\nuse function App\\More\\{first, second as other};\n\nclass Grouped {}\n"
            ),
        })
        self.assertEqual({"app/Support/functions.php"}, built.dependencies("app/Http/UserController.php"))
        self.assertEqual({"app/Support/more.php"}, built.dependencies("app/Http/Grouped.php"))
        self.assertEqual(
            {"php-function:\\App\\Support\\ns_helper": [5], "php-const:\\App\\Support\\LIMIT": [6]},
            built.results["app/Http/UserController.php"].import_lines,
        )

    def test_a_call_links_the_one_file_that_declares_the_function(self) -> None:
        """/* REQ-PHP-9662: A call of a function links the file that declares it: the function of the own namespace, else the global one; a qualified name by its namespace. A function two files declare, a method and a function of PHP itself give no edge. */"""
        built = _Built({
            **_PHP_HELPERS,
            "app/Support/twice_a.php": "<?php\n\nfunction twice() {}\n",
            "app/Support/twice_b.php": "<?php\n\nfunction twice() {}\n",
            "app/Http/LegacyController.php": (
                "<?php\n\nnamespace App\\Http;\n\nclass LegacyController\n{\n"
                "    public function show() { return format_money(3) . twice() . strlen('x') . $this->ns_helper(); }\n}\n"
            ),
            "app/Support/Caller.php": (
                "<?php\n\nnamespace App\\Support;\n\nclass Caller\n{\n    public function run() { return ns_helper(); }\n}\n"
            ),
            "app/Http/Qualified.php": (
                "<?php\n\nnamespace App\\Http;\n\nuse App\\Support;\n\nclass Qualified\n{\n"
                "    public function run() { return \\App\\Support\\ns_helper() + Support\\unused_helper(); }\n}\n"
            ),
        })
        self.assertEqual({"app/Support/helpers.php"}, built.dependencies("app/Http/LegacyController.php"))
        self.assertEqual({"app/Support/functions.php"}, built.dependencies("app/Support/Caller.php"))
        self.assertEqual({"app/Support/functions.php"}, built.dependencies("app/Http/Qualified.php"))

    def test_a_file_required_by_its_folder_is_a_dependency(self) -> None:
        """/* REQ-PHP-9663: `require_once __DIR__ . '/path'`, `dirname(__FILE__)` and `dirname(__DIR__)` name a file by the folder of the requiring file: it is a dependency, with the line; a path of another form gives no edge. */"""
        built = _Built({
            **_PHP_HELPERS,
            "app/Http/Controllers/LegacyController.php": (
                "<?php\n\nnamespace App\\Http\\Controllers;\n\n"
                "require_once __DIR__ . '/../../Support/helpers.php';\n"
                "include(dirname(__FILE__) . '/local.php');\n"
                "require dirname(__DIR__, 2) . '/Support/functions.php';\n"
                "require $base . '/local.php';\n"
                "include 'other.php';\n\n"
                "class LegacyController {}\n"
            ),
            "app/Http/Controllers/local.php": "<?php\n\nreturn [];\n",
            "app/Http/Controllers/other.php": "<?php\n\nreturn [];\n",
        })
        self.assertEqual(
            {"app/Support/helpers.php", "app/Http/Controllers/local.php", "app/Support/functions.php"},
            built.dependencies("app/Http/Controllers/LegacyController.php"),
        )
        lines = built.results["app/Http/Controllers/LegacyController.php"].import_lines
        self.assertEqual([[5], [6], [7]], [lines[name] for name in sorted(lines, key=lambda name: lines[name])])


_BLADE_APP = {
    "composer.json": '{"autoload": {"psr-4": {"App\\\\": "app/"}}}\n',
    "app/Support/Fmt.php": "<?php\n\nnamespace App\\Support;\n\nclass Fmt {}\n",
    "app/Models/Post.php": "<?php\n\nnamespace App\\Models;\n\nclass Post {}\n",
    "app/Models/Profile.php": "<?php\n\nnamespace App\\Models;\n\nclass Profile {}\n",
    "app/Enums/Status.php": "<?php\n\nnamespace App\\Enums;\n\nenum Status: string { case Active = 'a'; }\n",
    "app/View/Components/Alert.php": (
        "<?php\n\nnamespace App\\View\\Components;\n\nclass Alert\n{\n"
        "    public function render() { return view('components.alert'); }\n}\n"
    ),
    "app/View/Components/Forms/DateInput.php": "<?php\n\nnamespace App\\View\\Components\\Forms;\n\nclass DateInput {}\n",
    "resources/views/components/alert.blade.php": "<div>{{ $slot }}</div>\n",
    "resources/views/components/panel.blade.php": "<div>panel</div>\n",
}


class PhpBladeClassesTest(unittest.TestCase):
    def test_a_template_depends_on_the_classes_it_names(self) -> None:
        """/* REQ-PHP-9664: A Blade template depends on the class of `@inject('name', 'Class')`, of `@use('Class')` and of a qualified `\\Name\\Space\\Class::` written in it; a name in a comment, in `@verbatim` or without `::` gives no edge. */"""
        built = _Built({
            **_BLADE_APP,
            "resources/views/users/index.blade.php": (
                "@inject('fmt', 'App\\Support\\Fmt')\n@use('App\\Models\\Post')\n"
                "<p>{{ \\App\\Enums\\Status::Active->value }} {{ App\\Models\\Profile::count() }}</p>\n"
            ),
            "resources/views/users/quiet.blade.php": (
                "{{-- \\App\\Models\\Profile::count() --}}\n@verbatim {{ \\App\\Models\\Post::all() }} @endverbatim\n"
                "<p>App\\Models\\Post and $x\\App\\Models\\Post::y and @@use('App\\Support\\Fmt')</p>\n"
            ),
        })
        self.assertEqual(
            {"app/Support/Fmt.php", "app/Models/Post.php", "app/Enums/Status.php", "app/Models/Profile.php"},
            built.dependencies("resources/views/users/index.blade.php"),
        )
        self.assertEqual(set(), built.dependencies("resources/views/users/quiet.blade.php"))

    def test_a_component_tag_links_the_component_class(self) -> None:
        """/* REQ-PHP-9665: `<x-alert>` depends on the class `View\\Components\\Alert` of the application as well as on the anonymous view; a tag with only a view depends on the view, and `<x-forms.date-input>` on `Forms\\DateInput`. */"""
        built = _Built({
            **_BLADE_APP,
            "resources/views/page.blade.php": "<x-alert type=\"info\" />\n<x-panel />\n<x-forms.date-input />\n<x-missing />\n",
        })
        self.assertEqual(
            {"app/View/Components/Alert.php", "resources/views/components/alert.blade.php",
             "resources/views/components/panel.blade.php", "app/View/Components/Forms/DateInput.php"},
            built.dependencies("resources/views/page.blade.php"),
        )

    def test_a_mailable_view_and_a_route_action_string_are_dependencies(self) -> None:
        """/* REQ-PHP-9666: `$this->view('emails.welcome')` depends on the view, and a route action written `'Controller@method'` on the controller: by its full name, or as `Http\\Controllers\\<name>` of the application when one class has it. */"""
        built = _Built({
            **_BLADE_APP,
            "app/Mail/Welcome.php": (
                "<?php\n\nnamespace App\\Mail;\n\nclass Welcome\n{\n"
                "    public function build() { return $this->view('emails.welcome'); }\n}\n"
            ),
            "resources/views/emails/welcome.blade.php": "<p>hi</p>\n",
            "app/Http/Controllers/LegacyController.php": "<?php\n\nnamespace App\\Http\\Controllers;\n\nclass LegacyController\n{\n    public function show() {}\n}\n",
            "app/Http/Controllers/Admin/PanelController.php": "<?php\n\nnamespace App\\Http\\Controllers\\Admin;\n\nclass PanelController\n{\n    public function show() {}\n}\n",
            "routes/web.php": "<?php\n\nuse Illuminate\\Support\\Facades\\Route;\n\nRoute::get('/l', 'LegacyController@show');\nRoute::get('/n', 'NoSuchController@show');\nRoute::get('/m', 'mailto@example');\n",
            "routes/api.php": "<?php\n\nuse Illuminate\\Support\\Facades\\Route;\n\nRoute::post('/p', 'App\\Http\\Controllers\\Admin\\PanelController@show');\n",
        })
        self.assertEqual({"resources/views/emails/welcome.blade.php"}, built.dependencies("app/Mail/Welcome.php"))
        self.assertEqual({"app/Http/Controllers/LegacyController.php"}, built.dependencies("routes/web.php"))
        self.assertEqual({"app/Http/Controllers/Admin/PanelController.php"}, built.dependencies("routes/api.php"))


_RAILS_MODELS = {
    f"app/models/{stem}.rb": f"class {name} < ApplicationRecord\nend\n"
    for stem, name in (
        ("batch", "Batch"), ("status", "Status"), ("person", "Person"), ("box", "Box"), ("address", "Address"),
        ("dish", "Dish"), ("company", "Company"), ("child", "Child"), ("analysis", "Analysis"), ("house", "House"),
        ("post", "Post"),
    )
}


class RubyRailsNamesTest(unittest.TestCase):
    def test_has_many_names_are_singularized_as_rails_does(self) -> None:
        """/* REQ-RUBY-9670: `has_many` of a plural in -ches, -shes, -xes, -sses, -uses, -ies and of an irregular plural (people, children, analyses) is an association with the model of the singular name. */"""
        plurals = ("batches", "statuses", "people", "boxes", "addresses", "dishes", "companies", "children",
                   "analyses", "houses", "posts")
        built = _Built({
            **_RAILS_MODELS,
            "app/models/application_record.rb": "class ApplicationRecord\nend\n",
            "app/models/user.rb": (
                "class User < ApplicationRecord\n" + "".join(f"  has_many :{plural}\n" for plural in plurals) + "end\n"
            ),
        })
        associated = {
            target for (source, target), relations in built.relations.items()
            if source == "app/models/user.rb" and "association" in relations
        }
        self.assertEqual(set(_RAILS_MODELS), associated)

    def test_the_singular_rules(self) -> None:
        """/* REQ-RUBY-9671: The singular of a Rails association name follows the rules of Rails; a word that is not a plural is left as it is. */"""
        from scripts.lang.ruby.imports import _singularize

        for plural, singular in (
            ("batches", "batch"), ("statuses", "status"), ("people", "person"), ("boxes", "box"),
            ("addresses", "address"), ("buses", "bus"), ("aliases", "alias"), ("wishes", "wish"),
            ("categories", "category"), ("men", "man"), ("women", "woman"), ("children", "child"),
            ("mice", "mouse"), ("wives", "wife"), ("wolves", "wolf"), ("heroes", "hero"), ("quizzes", "quiz"),
            ("matrices", "matrix"), ("indices", "index"), ("crises", "crisis"), ("movies", "movie"),
            ("news", "news"), ("series", "series"), ("sheep", "sheep"), ("glass", "glass"), ("cases", "case"),
            ("responses", "response"), ("line_items", "line_item"), ("shoes", "shoe"), ("data", "datum"),
        ):
            self.assertEqual(singular, _singularize(plural), plural)

    def test_a_scoped_constant_names_at_least_its_scope(self) -> None:
        """/* REQ-RUBY-9672: `User::LIMIT`, with LIMIT defined in a concern that User includes, is a dependency on the file of `User`: a scoped name that resolves nowhere is resolved by its longest leading part; a name two files define stays unlinked. */"""
        built = _Built({
            "app/models/concerns/trackable.rb": "module Trackable\n  LIMIT = 5\nend\n",
            "app/models/user.rb": "class User\n  include Trackable\nend\n",
            "app/models/twin_a.rb": "module Twin\n  class Part\n  end\nend\n",
            "app/models/twin_b.rb": "module Twin\n  class Part\n  end\nend\n",
            "app/services/limits.rb": (
                "class Limits\n  def top\n    User::LIMIT + Missing::Thing::VALUE\n  end\n\n"
                "  def twin\n    Twin::Part::SIZE\n  end\nend\n"
            ),
        })
        self.assertEqual({"app/models/user.rb"}, built.dependencies("app/services/limits.rb"))

    def test_the_autoload_path_scan_is_linear(self) -> None:
        """/* REQ-RUBY-9673: The autoload paths of config/application.rb are read in linear time: 100 KB of one line of `autoload_paths` without a path, or of unclosed `Rails.root.join(`, within a second; the paths of a real line are still found. */"""
        from scripts.lang.ruby.imports import _autoload_join_arguments

        self.assertEqual(
            ["'lib'", "'app', 'extras'", '"vendor/x"'],
            list(_autoload_join_arguments(
                "config.autoload_paths << Rails.root.join('lib')\n"
                "config.autoload_once_paths += [config.root.join('app', 'extras')]; config.autoload_paths << Rails.root.join(\"vendor/x\")\n"
                "config.eager_load_paths << Rails.root.join('skipped')\n"
            )),
        )
        for content in ("autoload_paths " * 7_000, "config.autoload_paths << " + "Rails.root.join(" * 6_500):
            started = time.perf_counter()
            self.assertEqual([], list(_autoload_join_arguments(content)))
            self.assertLess(time.perf_counter() - started, 1.0)


class CppIncludeFoldersTest(unittest.TestCase):
    def test_a_file_the_database_does_not_cover_takes_the_cmake_folders(self) -> None:
        """/* REQ-CPP-9680: With a compilation database for part of the tree, a source file without an entry and without a translation unit at or above its folder finds its includes in the folders the CMake files name, then in every folder of the database: both edges are marked `inferred`; a file with an entry keeps its stated edges. */"""
        built = _Built({
            "build/compile_commands.json": json.dumps([
                {"directory": ".", "file": "../src/a.cpp", "arguments": ["c++", "-I", "../inc1", "-c", "../src/a.cpp"]},
            ]),
            "inc1/h1.h": "int h1();\n",
            "src/a.cpp": "#include <h1.h>\nint a() { return h1(); }\n",
            "src/near.h": "#include <h1.h>\n",
            "other/CMakeLists.txt": "add_library(other sub/c.cpp)\ntarget_include_directories(other PUBLIC inc)\n",
            "other/inc/oh.h": "int oh();\n",
            "other/sub/c.cpp": "#include <oh.h>\n#include <h1.h>\nint c() { return oh() + h1(); }\n",
        })
        self.assertEqual(["inferred"], built.relations[("other/sub/c.cpp", "other/inc/oh.h")])
        self.assertEqual(["inferred"], built.relations[("other/sub/c.cpp", "inc1/h1.h")])
        self.assertEqual([], built.relations[("src/a.cpp", "inc1/h1.h")])
        self.assertEqual([], built.relations[("src/near.h", "inc1/h1.h")])

    def test_an_include_written_in_another_case_is_found_when_one_file_matches(self) -> None:
        """/* REQ-CPP-9681: An include whose name differs from the file only in the case of its letters is an edge marked `inferred` when exactly one file matches; with two such files there is no edge. */"""
        built = _Built({
            "case/core.h": "int core();\n",
            "case/sub/deep.h": "int deep();\n",
            "case/main.cpp": '#include "Core.h"\n#include "SUB/Deep.H"\n#include "Util.h"\nint main() { return core(); }\n',
            "one/util.h": "int one();\n",
            "two/UTIL.h": "int two();\n",
            "exact/core.h": "int other_core();\n",
        })
        self.assertEqual({"case/core.h", "case/sub/deep.h"}, built.dependencies("case/main.cpp"))
        self.assertEqual(["inferred"], built.relations[("case/main.cpp", "case/core.h")])
        self.assertEqual(["inferred"], built.relations[("case/main.cpp", "case/sub/deep.h")])


class CppOperatorNameTest(unittest.TestCase):
    def test_the_call_operator_reads_back_whole(self) -> None:
        """/* REQ-CPP-9682: A method named `operator()` is written to the graph quoted, as a function and in the methods of its class, so that it reads back whole: `find_function` names `operator()`, not `operator(`. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(os.path.realpath(directory))
            _write(root, {
                "ops.h": (
                    "#pragma once\nstruct Cmp {\n  bool operator()(int a, int b) const { return a < b; }\n"
                    "  bool operator==(const Cmp& o) const;\n};\n"
                ),
                "use.cpp": '#include "ops.h"\nint use() { Cmp c; return c(1, 2) ? 1 : 0; }\n',
            })
            source = Path(__file__).resolve().parents[1] / "src"
            environment = dict(os.environ, PYTHONPATH=str(source) + os.pathsep + os.environ.get("PYTHONPATH", ""), WINGBOX_JOBS="1")

            def run(*args: str) -> str:
                return subprocess.run([sys.executable, "-m", "wingbox", *args], capture_output=True, env=environment,
                                      cwd=str(root), timeout=180, text=True).stdout

            run("build", ".")
            graph = (root / ".wingbox" / "index.sexp").read_text(encoding="utf-8")
            found = run("find_function", "operator")
        self.assertIn('"Cmp.operator()"', graph)
        self.assertIn('(:methods "operator()" operator==)', graph)
        self.assertIn("ops.h::Cmp.operator()", found)
        self.assertNotIn("Cmp.operator( ", found)


class DartPartOfNameTest(unittest.TestCase):
    def test_part_of_a_library_name_links_the_part_to_its_library(self) -> None:
        """/* REQ-DART-9690: `part of app.main;`, the form that names the library, makes the part depend on the file that declares `library app.main;`, with the line of the directive; a name two libraries declare is settled by the package of the part, and stays unlinked without one. */"""
        built = _Built({
            "pubspec.yaml": "name: app\n",
            "lib/main.dart": "library app.main;\n\npart 'src/part_old.dart';\n\nvoid main() {}\n",
            "lib/src/part_old.dart": "part of app.main;\n\nclass Old {}\n",
            "lib/src/lost.dart": "part of app.nowhere;\n\nclass Lost {}\n",
            "packages/shared/pubspec.yaml": "name: shared\n",
            "packages/shared/lib/shared.dart": "library shared.core;\n\npart 'src/impl.dart';\n",
            "packages/shared/lib/src/impl.dart": "part of shared.core;\n\nclass Impl {}\n",
            "packages/other/pubspec.yaml": "name: other\n",
            "packages/other/lib/other.dart": "// library app.main; in a comment\nlibrary shared.core;\n",
            "tool/twice_a.dart": "library twice;\n",
            "tool/twice_b.dart": "library twice;\n",
            "tool/part.dart": "part of twice;\n",
        })
        self.assertEqual({"lib/main.dart"}, built.dependencies("lib/src/part_old.dart"))
        self.assertEqual({"packages/shared/lib/shared.dart"}, built.dependencies("packages/shared/lib/src/impl.dart"))
        self.assertEqual(set(), built.dependencies("lib/src/lost.dart"))
        self.assertEqual(set(), built.dependencies("tool/part.dart"))
        self.assertEqual({"dart-library:app.main": [1]}, built.results["lib/src/part_old.dart"].import_lines)


_KB100 = 100_000   # the size of the adversarial inputs
_TIME_BOUND = 5.0   # seconds; before, each input below took minutes (a fifth of it took 5 to 60 seconds)


class LinearScansTest(unittest.TestCase):
    """Text scans that were quadratic in the size of one file: each reads 100 KB within the time bound."""

    def _timed(self, call):
        started = time.perf_counter()
        value = call()
        self.assertLess(time.perf_counter() - started, _TIME_BOUND)
        return value

    def test_the_pubspec_name_is_read_in_linear_time(self) -> None:
        """/* REQ-DART-9691: The name of a pubspec.yaml is read in linear time (one line of 100 KB within the bound), and as before: the first `name:` at column 0, without its comment and quotes. */"""
        from scripts.lang.dart.imports import _pubspec_name

        self.assertEqual("x" + " " * _KB100 + "y", self._timed(lambda: _pubspec_name("name: x" + " " * _KB100 + "y")))
        for content, name in (
            ("name: app\n", "app"), ("# c\nversion: 1\nname:   'quoted'   # the name\n", "quoted"),
            ("name : spaced\nname: second\n", "spaced"), (" name: indented\n", ""), ("name:\nname: late\n", "late"),
            ("names: no\n", ""), ('name: "d"\n', "d"),
        ):
            self.assertEqual(name, _pubspec_name(content), content)

    def test_a_pascal_include_directive_is_read_in_linear_time(self) -> None:
        """/* REQ-PAS-9692: A Pascal include directive is read in linear time (100 KB of spaces in one directive within the bound), and as before. */"""
        from scripts.lang.pascal.parser import extract_pascal_imports

        found = self._timed(lambda: extract_pascal_imports("unit u;\n{$I a" + " " * _KB100 + "b}\n"))
        self.assertEqual(1, len(found))
        paths = [item.explicit_path for item in extract_pascal_imports("unit u;\n{$I defs.inc}\n{$INCLUDE 'more defs.inc' }\n{$IFDEF X}\n")]
        self.assertEqual(["defs.inc", "more defs.inc"], paths)

    def test_php_registrations_are_scanned_in_linear_time(self) -> None:
        """/* REQ-PHP-9693: The registrations of a PHP file are scanned in linear time: 100 KB of one name as the class of `Livewire::component`, and 100 KB of `loadViewsFrom(` that never closes, within the bound; a real registration is still read. */"""
        from scripts.lang.php.imports import _scan_registrations

        def scan(content: str) -> tuple[list, list, list]:
            views: list = []
            locations: list = []
            livewire: list = []
            _scan_registrations(content, "a.php", "/a.php", views, locations, livewire)
            return views, locations, livewire

        self._timed(lambda: scan("<?php Livewire::component('x', " + "a" * _KB100 + ");"))
        self._timed(lambda: scan("<?php " + "loadViewsFrom(" * (_KB100 // 14)))
        views, _locations, livewire = scan(
            "<?php\n$this->loadViewsFrom(__DIR__ . '/../resources/views', 'crm');\n"
            "Livewire::component('counter', \\App\\Livewire\\Counter::class);\n"
        )
        self.assertEqual([("crm", "__DIR__ . '/../resources/views'", "a.php")], views)
        self.assertEqual([("counter", "\\App\\Livewire\\Counter", "/a.php")], livewire)

    def test_cpp_text_scans_are_linear(self) -> None:
        """/* REQ-CPP-9694: A C++ file of 100 KB is parsed within the bound when it holds one long declaration with `= 0`, a long MOCK_METHOD name or tail, or block comments that never close; and the scans of a text of 100 KB stay within it: the template arguments of a name nested thousands deep, the braces of a text of comments that never close, class headings that never reach a brace. */"""
        from scripts.lang.cpp import CPP_LANGUAGE
        from scripts.lang.cpp import parser as cpp_parser
        from scripts.lang.cpp import symbols as cpp_symbols

        cases = {
            "pure_virtual.h": "struct A {\n  void f(int a = 0" + " " * _KB100 + ", int b);\n};\n",
            "mock_method.h": "struct A {\n  MOCK_METHOD" + "1" * _KB100 + " x;\n};\n",
            "mock_tail.h": "struct A {\n  MOCK_METHOD1(run, void(int))" + " " * _KB100 + "x;\n};\n",
            "open_comments.cpp": "int f() {\n" + "/* " * (_KB100 // 3) + "\n",
        }
        with tempfile.TemporaryDirectory() as directory:
            for name, content in cases.items():
                path = Path(directory) / name
                path.write_text(content, encoding="utf-8")
                with self.subTest(name=name):
                    self._timed(lambda: CPP_LANGUAGE.parse(str(path), content, "fixture", 0.0))
        nested = "A<" * (_KB100 // 2) + "int" + ">" * (_KB100 // 2)
        self.assertEqual("A", self._timed(lambda: cpp_parser._plain(nested)))
        comments = ("int f() {\n" + "/* " * (_KB100 // 3) + "\n").encode()
        self.assertEqual({8: len(comments)}, self._timed(lambda: cpp_symbols.brace_pairs(comments)))
        for heading in ("struct X<\n", "struct X :\n"):
            source = (heading * (_KB100 // len(heading)) + "int f( {\n").encode()
            pairs = cpp_symbols.brace_pairs(source)
            with self.subTest(heading=heading):
                self.assertEqual([], self._timed(lambda: cpp_symbols.text_classes(source, pairs)))

    def test_a_block_comment_that_never_closes_ends_the_text_that_is_parsed(self) -> None:
        """/* REQ-CPP-9696: A C++ file is read up to a block comment that nothing closes: the include and the declarations before it are those of the file. A `/*` in a string, a raw string, a character literal or a line comment opens no comment, and the declarations after it are read. */"""
        built = _Built({
            "src/dep.h": "int dep();\n",
            "src/open.h": '#include "dep.h"\nint before();\n/* never closed\nint inside();\n',
            "src/globs.cpp": (
                '#include "dep.h"\nconst char* a = "src/*.cpp";   // all of src/*\n'
                'const char* b = R"x(\n  docs/*\n)x";\nconst char c = \'"\';\nint after() { return dep(); }\n'
            ),
        })
        self.assertEqual({"src/dep.h"}, built.dependencies("src/open.h"))
        self.assertEqual(["before"], list(_entities(built, "src/open.h")))
        self.assertEqual({"src/dep.h"}, built.dependencies("src/globs.cpp"))
        self.assertIn("after", _entities(built, "src/globs.cpp"))

    def test_the_rewritten_cpp_scans_answer_as_before(self) -> None:
        """/* REQ-CPP-9695: The C++ text scans keep their answers: a name without its template arguments, a pure virtual method, the method of a googlemock macro, braces outside comments and strings, the classes of a text. */"""
        from scripts.lang.cpp import parser as cpp_parser
        from scripts.lang.cpp import symbols as cpp_symbols

        for text, plain in (
            ("std :: map<K, std::vector<V>>::iterator", "std::map::iterator"), ("a<b>c<d", "ac<d"), ("a>b<c>", "a>b"),
            ("A<B<C>", "A<B"), ("plain", "plain"),
        ):
            self.assertEqual(plain, cpp_parser._plain(text), text)
        for text, pure in (
            ("virtual void f() = 0;", True), ("virtual void f() =0 ; ", True), ("void f() = 0", True),
            ("int a = 10;", False), ("void f(int a = 0, int b);", False), ("void f();", False),
        ):
            self.assertEqual(pure, cpp_parser._is_pure_virtual(text), text)
        for text, method in (
            ("MOCK_METHOD(void, run, (int), (override));", "run"), ("MOCK_METHOD1(stop, void(int));", "stop"),
            ("MOCK_CONST_METHOD0_T(size, int())  ;  ", "size"), ("MOCK_METHOD x;", ""), ("int run();", ""),
        ):
            self.assertEqual(method, cpp_parser._mock_method(text), text)
        source = b'int f() { /* } */ g("}"); // {\n if (x) { y(); } }\nstruct S : B<int> { int m; };\n/* open { \n'
        pairs = cpp_symbols.brace_pairs(source)
        self.assertEqual([source.index(b"{"), source.index(b"{ y"), source.index(b"{ int m")], sorted(pairs))
        self.assertEqual([("S", source.index(b"{ int m"), pairs[source.index(b"{ int m")])], cpp_symbols.text_classes(source, pairs))


if __name__ == "__main__":
    unittest.main()
