"""The validation mark of a data-flow fact: found in the syntax tree, written with its evidence."""
from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from scripts import build_code_graph, code_graph_search
from scripts.build_code_graph import Ok, build_graph
from scripts.lang.interface import FileResult


def _parse(language: str, name: str, content: str) -> FileResult:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory).resolve() / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return build_code_graph.parse_file((str(path), language, "fixture"))


def _evidence(language: str, name: str, content: str) -> str:
    result = _parse(language, name, content)
    if bool(result.validation_evidence) != result.has_validation:
        raise AssertionError(f"has_validation={result.has_validation} with evidence {result.validation_evidence!r}")
    return result.validation_evidence


IMPORTER_PY = "from app.b import VALUE\n\n\ndef use() -> int:\n    return VALUE\n"
IMPORTER_TS = 'import { total } from "./b";\n\nexport const shown = total;\n'


def _index(files: dict[str, str], language: str) -> str:
    """The code graph of a small project, as `wingbox build` writes it."""
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory).resolve()
        results = []
        for relative_path, content in files.items():
            path = root / relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            results.append(build_code_graph.parse_file((str(path), language, "fixture")))
        graph = build_graph(results, str(root))
        if not isinstance(graph, Ok):
            raise AssertionError(f"graph build failed: {graph.error}")
        return build_code_graph.render_sexp(graph.value.nodes, graph.value.edges, str(root), "default", len(results))


def _python_index(imported: str) -> str:
    return _index({"app/__init__.py": "", "app/a.py": IMPORTER_PY, "app/b.py": imported}, "python")


def _typescript_index(imported: str) -> str:
    return _index({"src/a.ts": IMPORTER_TS, "src/b.ts": imported}, "typescript")


def _data_flows(index: str) -> list[str]:
    return [line for line in index.splitlines() if line.startswith("(data-flow ")]


def _loaded(root: Path, edges: str) -> code_graph_search.CodeGraph:
    sexp_path = root / ".wingbox/index.sexp"
    sexp_path.parent.mkdir(exist_ok=True)
    sexp_path.write_text(
        "(code-graph :schema 1 :files 2 :nodes 2 :edges 2 :violations 0)\n"
        "(paths\n  (app\n    (m0 a)\n    (m1 b)\n  )\n)\n"
        '(node m0 py "general module")\n(node m1 py "general module")\n'
        "(imports m0 m1)\n" + edges,
        encoding="utf-8",
    )
    graph = code_graph_search.CodeGraph()
    loaded = graph.load(str(sexp_path), str(root))
    if not isinstance(loaded, code_graph_search.MapLoadOk):
        raise AssertionError(f"graph did not load: {loaded}")
    return graph


def _printed(command, *arguments) -> str:
    output = io.StringIO()
    with redirect_stdout(output):
        command(*arguments)
    return output.getvalue()


def _analyze_lines(edges: str) -> list[str]:
    with tempfile.TemporaryDirectory() as directory:
        graph = _loaded(Path(directory).resolve(), edges)
        text = _printed(code_graph_search.cmd_analyze, graph, "app/a")
    return [line for line in text.splitlines() if line.startswith("(analyze-dataflow ")]


class PythonValidationTest(unittest.TestCase):
    def test_text_that_only_names_a_validator_gives_no_mark(self) -> None:
        """/* REQ-PY-007: a Python file whose comment or string names a validator, that declares only a shape (TypedDict, NamedTuple, a typed dataclass, a frozen dataclass, a dataclass with __post_init__), or that imports a validation library without using it is not a validation module: no evidence, and an import of it gives no data-flow fact. */"""
        fixtures = {
            "plain": "VALUE = 1\n",
            "comment": "# BaseModel\nVALUE = 1\n",
            "string": 'NOTE = "pydantic BaseModel field_validator @attr.s"\nVALUE = 1\n',
            "typed dict": "from typing import TypedDict\n\n\nclass Row(TypedDict):\n    name: str\n\n\nVALUE = 1\n",
            "named tuple": "from typing import NamedTuple\n\n\nclass Row(NamedTuple):\n    name: str\n\n\nVALUE = 1\n",
            "dataclass": (
                "from dataclasses import dataclass\n\n\n@dataclass(frozen=True)\nclass Row:\n    name: str\n\n"
                "    def __post_init__(self) -> None:\n        pass\n\n\nVALUE = 1\n"
            ),
            "unused import": "from pydantic import BaseModel, field_validator\nimport attrs\n\nVALUE = 1\n",
            "annotated": "from typing import Annotated\n\nCount = Annotated[int, 'positive']\nVALUE = 1\n",
        }
        for name, content in fixtures.items():
            with self.subTest(name):
                self.assertEqual("", _evidence("python", "app/b.py", content))
                self.assertEqual([], _data_flows(_python_index(content)))

    def test_a_validator_defined_in_the_file_is_the_evidence(self) -> None:
        """/* REQ-PY-008: a Python file is a validation module when its syntax tree holds a class whose base is a pydantic model class, a pydantic validator decorator, a pydantic schema call, or an attrs field with a validator, each named through an import of the library; the evidence is the kind and the qualified name. */"""
        fixtures = {
            "from pydantic import BaseModel\n\n\nclass User(BaseModel):\n    name: str\n": "base pydantic.BaseModel",
            "import pydantic as pd\n\n\nclass User(pd.BaseModel):\n    name: str\n": "base pydantic.BaseModel",
            "from pydantic_settings import BaseSettings as Settings\n\n\nclass Config(Settings):\n    port: int\n":
                "base pydantic_settings.BaseSettings",
            "from pydantic import RootModel\n\n\nclass Names(RootModel[list[str]]):\n    pass\n": "base pydantic.RootModel",
            "from pydantic import validate_call\n\n\n@validate_call\ndef area(side: int) -> int:\n    return side * side\n":
                "decorator pydantic.validate_call",
            "import pydantic\n\n\nclass Check:\n    @pydantic.field_validator('name')\n    def run(cls, value):\n        return value\n":
                "decorator pydantic.field_validator",
            "from pydantic.dataclasses import dataclass\n\n\n@dataclass\nclass Row:\n    name: str\n":
                "decorator pydantic.dataclasses.dataclass",
            "from pydantic import TypeAdapter\n\nNAMES = TypeAdapter(list[str])\n": "call pydantic.TypeAdapter",
            "import attrs\n\n\n@attrs.define\nclass Row:\n    size: int = attrs.field(validator=attrs.validators.gt(0))\n":
                "call attrs.field validator=",
        }
        for content, expected in fixtures.items():
            with self.subTest(expected):
                self.assertEqual(expected, _evidence("python", "app/b.py", content))

    def test_a_name_that_is_not_the_imported_validator_gives_nothing(self) -> None:
        """/* REQ-PY-009: a base, decorator or call counts only when its name is bound by the import of the validation library: a class of the file named BaseModel, a name of another module, a name that a parameter, a later import or a later glob import also binds, and an attrs class or field without a validator give no evidence. */"""
        fixtures = {
            "own class": "class BaseModel:\n    pass\n\n\nclass User(BaseModel):\n    name: str\n",
            "other module": "from app.models import BaseModel\n\n\nclass User(BaseModel):\n    name: str\n",
            "redefined": "from pydantic import BaseModel\n\n\nclass BaseModel:\n    pass\n\n\nclass User(BaseModel):\n    pass\n",
            "shadowed": (
                "from pydantic import TypeAdapter\n\n\ndef build(TypeAdapter):\n    return TypeAdapter(int)\n"
            ),
            "attrs shape": "import attrs\n\n\n@attrs.define\nclass Row:\n    size: int = attrs.field(default=0)\n",
            "import of the same name": "from pydantic import BaseModel\nfrom app.models import BaseModel\n\n\nclass User(BaseModel):\n    pass\n",
            "glob import after": "from pydantic import BaseModel\nfrom app.models import *\n\n\nclass User(BaseModel):\n    pass\n",
            "type only": "from pydantic import BaseModel\n\n\ndef show(model: BaseModel) -> str:\n    return str(model)\n",
        }
        for name, content in fixtures.items():
            with self.subTest(name):
                self.assertEqual("", _evidence("python", "app/b.py", content))

    def test_the_code_graph_writes_the_mark_with_its_evidence(self) -> None:
        """/* REQ-PY-010: an import of a validation module is written as (data-flow <src> <tgt> :validation-module :evidence "<kind> <name>"); the retired mark :validated is never written, and a parse result that claims validation without evidence gives no fact. */"""
        model = "from pydantic import BaseModel\n\n\nclass User(BaseModel):\n    name: str\n\n\nVALUE = 1\n"
        index = _python_index(model)
        flows = _data_flows(index)
        self.assertEqual(1, len(flows), flows)
        self.assertRegex(flows[0], r'^\(data-flow \S+ \S+ :validation-module :evidence "base pydantic\.BaseModel"\)$')
        self.assertNotIn(":validated", index)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            results = []
            for relative_path, content in {"app/__init__.py": "", "app/a.py": IMPORTER_PY, "app/b.py": "VALUE = 1\n"}.items():
                path = root / relative_path
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
                result = build_code_graph.parse_file((str(path), "python", "fixture"))
                if relative_path == "app/b.py":
                    result.has_validation = True
                results.append(result)
            graph = build_graph(results, str(root))
            if not isinstance(graph, Ok):
                raise AssertionError(f"graph build failed: {graph.error}")
            self.assertEqual([], [edge for edge in graph.value.edges if edge["type"] == "DATA_FLOWS_TO"])

    def test_the_parse_cache_keeps_the_evidence_and_drops_older_results(self) -> None:
        """/* REQ-PY-011: the parse cache stores the evidence with the result and reads it back; its schema version is above 5, so a result cached with the flag of the text detector is parsed again. */"""
        result = _parse("python", "app/b.py", "from pydantic import BaseModel\n\n\nclass User(BaseModel):\n    name: str\n")
        entry = json.loads(json.dumps(build_code_graph._serialize_file_result(result)))
        self.assertEqual("base pydantic.BaseModel", entry["result"]["validation_evidence"])
        decoded = build_code_graph.decode_cache_entry(entry)
        if not isinstance(decoded, Ok):
            raise AssertionError(f"cache entry was not read: {decoded.error}")
        self.assertEqual("base pydantic.BaseModel", decoded.value.validation_evidence)
        self.assertTrue(decoded.value.has_validation)
        entry["result"]["validation_evidence"] = 7
        self.assertIsInstance(build_code_graph.decode_cache_entry(entry), build_code_graph.Err)
        self.assertGreater(build_code_graph.CACHE_SCHEMA_VERSION, 5)


class TypeScriptValidationTest(unittest.TestCase):
    def test_text_that_only_names_a_validator_gives_no_mark(self) -> None:
        """/* REQ-TS-VALID-001: a TypeScript or JavaScript file whose comment or string holds the text of a validation call ("quiz.", ".parse(", "zod."), that parses JSON, that imports a validation library for types only or without using it is not a validation module: no evidence, and an import of it gives no data-flow fact. */"""
        fixtures = {
            "quiz": "// quiz. counter\nexport const total = 1;\n",
            "string": 'export const note = "z.object(Joi.yup.ajv).safeParse(";\nexport const total = 1;\n',
            "json": 'export const total = JSON.parse("1");\n',
            "type import": 'import type { ZodType } from "zod";\nexport type Shape = ZodType;\nexport const total = 1;\n',
            "unused import": 'import { z } from "zod";\nimport Joi from "joi";\nexport const total = 1;\n',
            "type use": 'import { z } from "zod";\nexport type Shape = z.infer<typeof other>;\ndeclare const other: unknown;\nexport const total = 1;\n',
            "own z": "const z = { object: (shape: unknown) => shape };\nexport const total = z.object({});\n",
            "shadowed": 'import { z } from "zod";\nexport const total = [1].map((z) => z.toFixed());\n',
        }
        for name, content in fixtures.items():
            with self.subTest(name):
                self.assertEqual("", _evidence("typescript", "src/b.ts", content))
                self.assertEqual([], _data_flows(_typescript_index(content)))

    def test_a_schema_built_with_an_imported_library_is_the_evidence(self) -> None:
        """/* REQ-TS-VALID-002: a TypeScript or JavaScript file is a validation module when it calls a name bound by an import (or a top-level require) of zod, joi, yup or ajv; the evidence is the call as written and the library, and an import of the file gives (data-flow ... :validation-module :evidence "call z.object from zod"). */"""
        fixtures = {
            ("src/b.ts", 'import { z } from "zod";\nexport const User = z.object({ name: z.string() });\n'): "call z.object from zod",
            ("src/b.ts", 'import * as zod from "zod/v4";\nexport const Name = zod.string().min(1);\n'): "call zod.string from zod/v4",
            ("src/b.ts", 'import Joi from "joi";\nexport const User = Joi.object({});\n'): "call Joi.object from joi",
            ("src/b.ts", 'import { object, string } from "yup";\nexport const User = object({ name: string() });\n'):
                "call object from yup",
            ("src/b.ts", 'import Ajv from "ajv";\nexport const ajv = new Ajv();\n'): "call Ajv from ajv",
            ("src/b.js", 'const Joi = require("joi");\nmodule.exports = Joi.object({});\n'): "call Joi.object from joi",
            ("src/b.js", 'const { object } = require("yup");\nmodule.exports = object({});\n'): "call object from yup",
        }
        for (name, content), expected in fixtures.items():
            with self.subTest(expected):
                self.assertEqual(expected, _evidence("typescript", name, content))
        flows = _data_flows(_typescript_index('import { z } from "zod";\nexport const total = z.number().parse(1);\n'))
        self.assertEqual(1, len(flows), flows)
        self.assertRegex(flows[0], r'^\(data-flow \S+ \S+ :validation-module :evidence "call z\.number from zod"\)$')


class OtherLanguagesValidationTest(unittest.TestCase):
    def _check(self, language: str, name: str, without: dict[str, str], with_evidence: dict[str, str]) -> None:
        for label, content in without.items():
            with self.subTest(language=language, without=label):
                self.assertEqual("", _evidence(language, name, content))
        for content, expected in with_evidence.items():
            with self.subTest(language=language, evidence=expected):
                self.assertEqual(expected, _evidence(language, name, content))

    def test_go(self) -> None:
        """/* REQ-GO-005: a Go file is a validation module when a struct field tag has the key validate or binding, or when it calls New of an imported github.com/go-playground/validator package; a comment, a string or the import alone gives no evidence. */"""
        package = "package dto\n\n"
        self._check(
            "go", "dto/user.go",
            {
                "comment": package + '// validate:"required" binding:"required" go-playground/validator\ntype User struct{ Name string }\n',
                "string": package + 'const Note = "validate:\\"required\\" go-playground/validator"\n',
                "other tag": package + 'type User struct {\n\tName string `json:"validate"`\n}\n',
                "blank import": package + 'import _ "github.com/go-playground/validator/v10"\n',
                "other package": package + 'import "example.com/app/validator"\n\nvar check = validator.New()\n',
            },
            {
                package + 'type User struct {\n\tName string `json:"name" validate:"required,min=2"`\n}\n': "tag validate",
                package + 'type Login struct {\n\tMail string `binding:"required"`\n}\n': "tag binding",
                package + 'import "github.com/go-playground/validator/v10"\n\nvar check = validator.New()\n':
                    "call validator.New from github.com/go-playground/validator/v10",
                package + 'import v "github.com/go-playground/validator"\n\nfunc Check() { _ = v.New() }\n':
                    "call v.New from github.com/go-playground/validator",
            },
        )

    def test_java(self) -> None:
        """/* REQ-JAVA-006: a Java file is a validation module when an annotation or a supertype is named through an import of jakarta.validation, javax.validation or org.springframework.validation (or written with that package); with a wildcard import the evidence names the import, and a comment, a string, the import alone or an annotation of the same name from another package gives no evidence. */"""
        self._check(
            "java", "app/User.java",
            {
                "comment": "package app;\n\n// @NotNull @Valid jakarta.validation\npublic class User { String name; }\n",
                "string": 'package app;\n\npublic class User { String note = "@Valid javax.validation"; }\n',
                "import only": "package app;\n\nimport jakarta.validation.constraints.NotNull;\n\npublic class User { String name; }\n",
                "other package": "package app;\n\nimport org.jetbrains.annotations.NotNull;\n\npublic class User { @NotNull String name; }\n",
                "no import": "package app;\n\npublic class User { @NotNull String name; }\n",
                "wildcard, unknown name": "package app;\n\nimport jakarta.validation.constraints.*;\n\npublic class User { @Getter String name; }\n",
            },
            {
                "package app;\n\nimport jakarta.validation.constraints.NotNull;\n\npublic class User { @NotNull String name; }\n":
                    "annotation jakarta.validation.constraints.NotNull",
                "package app;\n\nimport javax.validation.Valid;\n\npublic class Api { void save(@Valid User user) {} }\n":
                    "annotation javax.validation.Valid",
                "package app;\n\npublic class User { @jakarta.validation.constraints.Size(min = 1) String name; }\n":
                    "annotation jakarta.validation.constraints.Size",
                "package app;\n\nimport jakarta.validation.constraints.*;\n\npublic class User { @NotBlank String name; }\n":
                    "annotation NotBlank with import jakarta.validation.constraints.*",
                "package app;\n\nimport org.springframework.validation.annotation.Validated;\n\n@Validated\npublic class Api {}\n":
                    "annotation org.springframework.validation.annotation.Validated",
                "package app;\n\nimport jakarta.validation.ConstraintValidator;\n\n"
                "public class Rule implements ConstraintValidator<Even, Integer> {}\n":
                    "base jakarta.validation.ConstraintValidator",
            },
        )

    def test_ruby(self) -> None:
        """/* REQ-RUBY-011: a Ruby file is a validation module when the body of a class or module, or the `included do` block of a concern, calls validates, validate or a validates_... method without a receiver; the evidence names the method and the class. A comment, a string, a call inside a method or a call on a receiver gives no evidence. */"""
        self._check(
            "ruby", "app/models/user.rb",
            {
                "comment": "class User\n  # validates :name, presence: true\nend\n",
                "string": 'class User\n  NOTE = "validates :name"\nend\n',
                "heredoc": "class User\n  NOTE = <<~TEXT\n    validates :name\n  TEXT\nend\n",
                "inside a method": "class User\n  def run\n    validate(record)\n  end\nend\n",
                "on a receiver": "class User\n  schema.validates :name\nend\n",
                "top level": "validate :name\n",
            },
            {
                "class User < ApplicationRecord\n  validates :name, presence: true\nend\n": "call validates in User",
                "class Order\n  include ActiveModel::Validations\n  validate :total_is_positive\nend\n": "call validate in Order",
                "module Named\n  validates_presence_of :name\nend\n": "call validates_presence_of in Named",
                "module Named\n  extend ActiveSupport::Concern\n  included do\n    validates :name, presence: true\n  end\nend\n":
                    "call validates in Named",
                "class Order\n  validate { errors.add(:base, 'empty') if lines.empty? }\nend\n": "call validate in Order",
            },
        )

    def test_php(self) -> None:
        """/* REQ-PHP-024: a PHP file is a validation module when a class extends Illuminate\\Foundation\\Http\\FormRequest, when it calls make on the Illuminate\\Support\\Facades\\Validator facade or validate on a parameter typed Illuminate\\Http\\Request, or when it carries the attribute Livewire\\Attributes\\Validate, each named through a use statement or written in full; a comment, a string, the use alone or a class of the same short name from another namespace gives no evidence. */"""
        head = "<?php\nnamespace App\\Http;\n\n"
        self._check(
            "php", "app/Http/StoreUser.php",
            {
                "comment": head + "// $request->validate( Validator::make( FormRequest #[Validate]\nclass StoreUser {}\n",
                "string": head + "class StoreUser { const NOTE = 'Validator::make( FormRequest'; }\n",
                "use only": head + "use Illuminate\\Foundation\\Http\\FormRequest;\nuse Illuminate\\Support\\Facades\\Validator;\n\nclass StoreUser {}\n",
                "own FormRequest": head + "class StoreUser extends FormRequest {}\n",
                "other Validator": head + "use App\\Support\\Validator;\n\nclass StoreUser { function run() { return Validator::make([], []); } }\n",
                "untyped": head + "class StoreUser { function run($request) { return $request->validate([]); } }\n",
            },
            {
                head + "use Illuminate\\Foundation\\Http\\FormRequest;\n\nclass StoreUser extends FormRequest {}\n":
                    "base Illuminate\\Foundation\\Http\\FormRequest",
                head + "use Illuminate\\Support\\Facades\\Validator as V;\n\nclass StoreUser { function run() { return V::make([], []); } }\n":
                    "call Illuminate\\Support\\Facades\\Validator::make",
                head + "class StoreUser { function run() { return \\Illuminate\\Support\\Facades\\Validator::make([], []); } }\n":
                    "call Illuminate\\Support\\Facades\\Validator::make",
                head + "use Illuminate\\Http\\Request;\n\nclass StoreUser { function run(Request $request) { return $request->validate([]); } }\n":
                    "call Illuminate\\Http\\Request->validate",
                head + "use Livewire\\Attributes\\Validate;\n\nclass StoreUser { #[Validate('required')]\n public string $title = ''; }\n":
                    "attribute Livewire\\Attributes\\Validate",
            },
        )

    def test_csharp(self) -> None:
        """/* REQ-CSHARP-006: a C# file is a validation module when it carries a validation attribute of System.ComponentModel.DataAnnotations or a base type ValidationAttribute, IValidatableObject or FluentValidation's AbstractValidator, written in full or together with the using directive of the namespace, which the evidence then names; a comment, a string, the using alone or the attribute without the using gives no evidence. */"""
        self._check(
            "csharp", "App/User.cs",
            {
                "comment": "namespace App;\n\n// [Required] FluentValidation ValidationAttribute\npublic class User {}\n",
                "string": 'namespace App;\n\npublic class User { string Note = "[Required] FluentValidation"; }\n',
                "using only": "using System.ComponentModel.DataAnnotations;\nusing FluentValidation;\n\nnamespace App;\n\npublic class User {}\n",
                "no using": "namespace App;\n\npublic class User { [Required] public string Name { get; set; } }\n",
                "not a rule": "using System.ComponentModel.DataAnnotations;\n\nnamespace App;\n\npublic class User { [Key] public int Id { get; set; } }\n",
            },
            {
                "using System.ComponentModel.DataAnnotations;\n\nnamespace App;\n\npublic class User { [Required] public string Name { get; set; } }\n":
                    "attribute Required with using System.ComponentModel.DataAnnotations",
                "namespace App;\n\npublic class User { [System.ComponentModel.DataAnnotations.Range(1, 5)] public int Age { get; set; } }\n":
                    "attribute System.ComponentModel.DataAnnotations.Range",
                "using DA = System.ComponentModel.DataAnnotations;\n\nnamespace App;\n\npublic class User { [DA.EmailAddress] public string Mail { get; set; } }\n":
                    "attribute System.ComponentModel.DataAnnotations.EmailAddress",
                "using FluentValidation;\n\nnamespace App;\n\npublic class UserRules : AbstractValidator<User> {}\n":
                    "base AbstractValidator with using FluentValidation",
                "using System.ComponentModel.DataAnnotations;\n\nnamespace App;\n\npublic class Even : ValidationAttribute {}\n":
                    "base ValidationAttribute with using System.ComponentModel.DataAnnotations",
            },
        )

    def test_dart(self) -> None:
        """/* REQ-DART-016: a Dart file is a validation module when a call passes the named argument validator; the evidence names the callee. A comment, a string, a type named FormState or an input formatter gives no evidence. */"""
        self._check(
            "dart", "lib/login.dart",
            {
                "comment": "// validator: TextFormField FormState\nclass Login {}\n",
                "string": "const note = 'validator: TextFormField FieldValidator';\n",
                "form key": "class Login {\n  final key = GlobalKey<FormState>();\n}\n",
                "formatter": "class Upper extends TextInputFormatter {}\n",
                "null": "Widget field() => TextFormField(validator: null);\n",
            },
            {
                "Widget field() => TextFormField(\n  validator: (value) => value == null ? 'required' : null,\n);\n":
                    "argument validator of TextFormField",
                "Widget field() => Form(child: AppField(label: 'a', validator: checkName));\n":
                    "argument validator of AppField",
                "Widget field() => const AppField(validator: checkName);\n": "argument validator of AppField",
                "Widget field() => ui.AppField.named(validator: checkName);\n": "argument validator of ui.AppField.named",
            },
        )


class StoredMarkTest(unittest.TestCase):
    def test_analyze_prints_the_marks_the_stored_fact_carries(self) -> None:
        """/* REQ-CS-084: `analyze` prints on an analyze-dataflow line the marks of the stored data-flow fact and nothing else: none for a fact without marks, the validation mark with its evidence text, and one line per different set of marks of one pair. */"""
        self.assertEqual(["(analyze-dataflow app/a app/b :lang py)"], _analyze_lines("(data-flow m0 m1)\n"))
        self.assertEqual(
            ['(analyze-dataflow app/a app/b :lang py :validation-module :evidence "base pydantic.BaseModel")'],
            _analyze_lines('(data-flow m0 m1 :validation-module :evidence "base pydantic.BaseModel")\n'),
        )
        self.assertEqual(
            [
                "(analyze-dataflow app/a app/b :lang py)",
                '(analyze-dataflow app/a app/b :lang py :validation-module :evidence "call z.object from zod")',
            ],
            _analyze_lines('(data-flow m0 m1)\n(data-flow m0 m1 :validation-module :evidence "call z.object from zod")\n'),
        )

    def test_the_retired_mark_of_older_graphs_is_not_printed(self) -> None:
        """/* REQ-CS-085: `:validated`, which versions up to 0.11.0 set from a text match, is read as no mark: `analyze` and `module` print the data-flow fact of such a graph without it. */"""
        self.assertEqual(["(analyze-dataflow app/a app/b :lang py)"], _analyze_lines("(data-flow m0 m1 :validated)\n"))
        with tempfile.TemporaryDirectory() as directory:
            graph = _loaded(Path(directory).resolve(), "(data-flow m0 m1 :validated)\n")
            text = _printed(code_graph_search.cmd_module, graph, "app/a")
        self.assertIn("(data-flow app/a app/b)", text.splitlines())
        self.assertNotIn("validated", text)

    def test_module_prints_the_evidence_with_the_mark(self) -> None:
        """/* REQ-CS-086: `module` prints a data-flow fact as stored, the evidence text included and quoted, also when the text holds a backslash. */"""
        stored = '(data-flow m0 m1 :validation-module :evidence "base Illuminate\\\\Foundation\\\\Http\\\\FormRequest")\n'
        with tempfile.TemporaryDirectory() as directory:
            graph = _loaded(Path(directory).resolve(), stored)
            text = _printed(code_graph_search.cmd_module, graph, "app/a")
        self.assertIn(
            '(data-flow app/a app/b :validation-module :evidence "base Illuminate\\\\Foundation\\\\Http\\\\FormRequest")',
            text.splitlines(),
        )
        edge = next(edge for edge in graph.all_edges if edge.etype == "data-flow")
        self.assertEqual("base Illuminate\\Foundation\\Http\\FormRequest", edge.evidence)

    def test_a_built_graph_is_read_back_with_the_same_mark(self) -> None:
        """/* REQ-CS-087: the data-flow fact the builder writes for a validation module is read back by `analyze` with the same mark and evidence, and the fixture whose imported file only names a validator in a comment gives an analyze answer without a data-flow line. */"""
        model = "from pydantic import BaseModel\n\n\nclass User(BaseModel):\n    name: str\n\n\nVALUE = 1\n"
        for imported, expected in (
            (model, ['(analyze-dataflow app/a app/b :lang py :validation-module :evidence "base pydantic.BaseModel")']),
            ("# BaseModel\nVALUE = 1\n", []),
        ):
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                sexp_path = root / ".wingbox/index.sexp"
                sexp_path.parent.mkdir(exist_ok=True)
                sexp_path.write_text(_python_index(imported), encoding="utf-8")
                graph = code_graph_search.CodeGraph()
                self.assertIsInstance(graph.load(str(sexp_path), str(root)), code_graph_search.MapLoadOk)
                text = _printed(code_graph_search.cmd_analyze, graph, "app/a")
                self.assertEqual(expected, [line for line in text.splitlines() if line.startswith("(analyze-dataflow ")])
                self.assertNotIn(":validated", text)


if __name__ == "__main__":
    unittest.main()
