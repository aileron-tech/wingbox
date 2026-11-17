"""Capability rules: what a layer may do (SQL in repositories, HTTP in infrastructure, no framework in the domain)."""
import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO))

from scripts.architecture.capabilities import detect  # noqa: E402
from wingbox import cli  # noqa: E402

CONFIG = """parts:
  .:
    style: layered
    layers:
      api: [routes, controllers]
      service: [services]
      infra: [repositories]
      domain: [models, entities]
      config: [config]
"""


def _run(arguments: list[str], cwd: Path) -> tuple[int, str]:
    output = io.StringIO()
    saved = os.getcwd()
    os.chdir(cwd)
    try:
        with redirect_stdout(output):
            status = cli.main(arguments)
    finally:
        os.chdir(saved)
    return status, output.getvalue()


def _project(files: dict[str, str], config: str = CONFIG) -> Path:
    directory = tempfile.TemporaryDirectory()
    root = Path(directory.name).resolve()
    root.joinpath(".keep").write_text("", encoding="utf-8")
    for relative, text in files.items():
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        (root / relative).write_text(text, encoding="utf-8")
    (root / ".wingbox").mkdir(exist_ok=True)
    (root / ".wingbox" / "config.yaml").write_text(config, encoding="utf-8")
    _project.keep.append(directory)
    return root


_project.keep = []   # the temporary folders live until the process ends


def _boundaries(root: Path) -> str:
    status, text = _run(["build", str(root)], root)
    assert status == 0, text
    return _run(["boundaries", "all", "--map", str(root / ".wingbox" / "index.sexp"), "--root", str(root)], root)[1]


PYTHON = {
    "src/repositories/orders_repository.py": 'def find(cursor, order_id):\n    cursor.execute("SELECT id, total FROM orders WHERE id = %s", (order_id,))\n    return cursor.fetchone()\n',
    "src/services/orders_service.py": 'from src.repositories.orders_repository import find\n\n\ndef total(cursor, order_id):\n    cursor.execute("SELECT total FROM orders WHERE id = %s", (order_id,))\n    return find(cursor, order_id)\n',
    "src/services/report_service.py": 'QUERY = "SELECT name FROM users WHERE active"\n\n\ndef names():\n    return QUERY\n',
    "src/services/http_service.py": 'from fastapi.responses import JSONResponse\n\n\ndef answer(data):\n    return JSONResponse(data)\n',
    "src/routes/orders.py": 'from src.repositories.orders_repository import find\nfrom src.repositories.logging_repository import log\n\n\ndef get(cursor, order_id):\n    log(order_id)\n    return find(cursor, order_id)\n',
    "src/repositories/logging_repository.py": 'def log(message):\n    print(message)\n',
    "src/models/order.py": 'from sqlalchemy import Column\nfrom fastapi import HTTPException\n\n\nclass Order:\n    pass\n',
    "src/config/settings.py": 'import os\n\nDATABASE_URL = os.environ["DATABASE_URL"]\n',
}

TYPESCRIPT = {
    "src/repositories/users.repository.ts": "import { PrismaClient } from '@prisma/client';\nconst prisma = new PrismaClient();\nexport const all = () => prisma.user.findMany();\n",
    "src/services/users.service.ts": "import { all } from '../repositories/users.repository';\nexport const users = async (db: any) => {\n  const rows = await db.query('SELECT id FROM users');\n  return all();\n};\n",
    "src/services/search.service.ts": "const SEARCH = `SELECT id FROM users WHERE name LIKE $1`;\nexport const search = () => SEARCH;\n",
    "src/controllers/users.controller.ts": "import axios from 'axios';\nimport { users } from '../services/users.service';\nexport const list = async () => { await axios.get('https://example.com'); return users(null); };\n",
    "src/entities/user.ts": "import { Injectable } from '@nestjs/common';\n@Injectable()\nexport class User { id = 0; }\n",
    "src/config/env.ts": "export const port = process.env.PORT;\n",
}


class CapabilityDetectionTest(unittest.TestCase):
    def test_each_language_reports_the_line_and_the_evidence(self) -> None:
        """/* REQ-CAP-001: SQL passed to a query call, query-builder and ORM queries, database and HTTP clients, framework imports, HTTP responses and environment reads are found by syntax with their line, in Python, TypeScript, PHP, Ruby, Java, C# and Go; comment lines are skipped. */"""
        cases = {
            "a.py": ('# cursor.execute("SELECT 1 FROM t")\nrows = session.query(User).all()\n', ("sql", 2)),
            "b.ts": ("const r = await knex('users').where({ id });\n", ("sql", 1)),
            "c.php": ("<?php\n$rows = DB::select('select * from users where id = ?', [$id]);\n", ("sql", 2)),
            "d.rb": ("rows = User.find_by_sql(\"SELECT * FROM users\")\n", ("sql", 1)),
            "e.java": ("import jakarta.persistence.EntityManager;\n", ("db-client", 1)),
            "f.cs": ("var users = context.Users.FromSqlRaw(\"SELECT * FROM Users\").ToList();\n", ("sql", 1)),
            "g.go": ("package x\n\nfunc f() { rows, _ := db.Query(\"SELECT id FROM t\") }\n", ("sql", 3)),
            "h.py": ("import httpx\n", ("http-client", 1)),
            "i.ts": ("import express from 'express';\n", ("framework", 1)),
            "j.java": ("return ResponseEntity.ok(body);\n", ("http-response", 1)),
            "k.go": ("v := os.Getenv(\"KEY\")\n", ("env", 1)),
        }
        for path, (text, (capability, line)) in cases.items():
            found = [(finding.capability, finding.line) for finding in detect(path, text) if not finding.suspect]
            self.assertIn((capability, line), found, path)
        self.assertEqual((), tuple(f for f in detect("a.py", '# cursor.execute("SELECT 1 FROM t")\n')))

    def test_a_sql_literal_not_passed_to_a_query_call_is_only_suspected(self) -> None:
        """/* REQ-CAP-002: a SQL statement in a string literal that no query call on the line (or the call opened just above) receives is a suspected finding; prose such as "select an option from the list" is no SQL; a TypeScript `import type` of a client is suspected too (the compiler erases it); the text of a multi-line string (a docstring, a script of another program in a template) is not code, only the SQL of a query call opened just above it counts; `db.delete('store', key)` of IndexedDB is no query builder. */"""
        suspected = detect("a.py", 'QUERY = "SELECT name FROM users"\n')
        self.assertEqual([("sql", 1, True)], [(f.capability, f.line, f.suspect) for f in suspected])
        self.assertEqual((), detect("b.ts", "const label = 'select an option from the list';\n"))
        typed = detect("d.ts", "import type { Selectable } from 'kysely';\n")
        self.assertEqual([("db-client", 1, True)], [(f.capability, f.line, f.suspect) for f in typed])
        self.assertEqual((), detect("idb.ts", "await db.delete('etags', key);\n"))   # IndexedDB: a store name, no SQL table
        self.assertEqual([("sql", 1)], [(f.capability, f.line) for f in detect("d.ts", "await db.delete(ordersTable).where(eq(ordersTable.id, id));\n")])
        embedded = "const SCRIPT = `import sqlite3\nrow = db.execute(\"SELECT value FROM items\")\n`;\n"
        self.assertEqual((), detect("e.ts", embedded))
        opened = detect("c.py", 'cursor.execute(\n    """\n    SELECT a FROM b\n    """\n)\n')
        self.assertEqual([("sql", 3, False)], [(f.capability, f.line, f.suspect) for f in opened])


class CapabilityRulesTest(unittest.TestCase):
    def test_python_sql_in_a_repository_passes_and_elsewhere_breaks_with_the_line(self) -> None:
        """/* REQ-CAP-003: in a layered Python part, SQL in a repository passes; the same query in a service is a capability-violation naming the file, layer, allowed layers, line and evidence; a query kept in a variable is a candidate; the ORM and the framework in a domain file break (domain-imports-orm); the environment read in config passes; a controller that imports a repository with database access skips the service (a logging "repository" does not count); a service that builds a JSONResponse breaks its pattern. */"""
        text = _boundaries(_project(PYTHON))
        self.assertNotIn('src/repositories/orders_repository.py :capability sql', text)
        self.assertIn('(capability-violation src/services/orders_service.py :capability sql :layer service :allowed (infra) :line 5 '
                      ':evidence "cursor.execute(\\"SELECT total FROM orders WHERE id = %s\\", (order_id,))"', text)
        self.assertIn('(capability-candidate src/services/report_service.py :capability sql :layer service', text)
        self.assertIn('(capability-violation src/models/order.py :capability db-client :layer domain :allowed (infra) :line 1', text)
        self.assertIn(":pattern domain-imports-orm", text)
        self.assertIn('(capability-violation src/models/order.py :capability framework :layer domain :forbidden (domain) :line 2', text)
        self.assertNotIn('src/config/settings.py :capability env', text)
        self.assertIn('(pattern-violation src/routes/orders.py :pattern controller-skips-service :target src/repositories/orders_repository.py', text)
        self.assertNotIn(":target src/repositories/logging_repository.py", text)
        self.assertIn('(pattern-violation src/services/http_service.py :pattern service-builds-http-response :line 5', text)
        self.assertIn("(capability-summary :violations 3 :known 0 :candidates 1 :patterns 2", text)

    def test_typescript_rules_follow_the_same_layers(self) -> None:
        """/* REQ-CAP-004: in a layered TypeScript part, Prisma in a repository passes; db.query with SQL in a service and axios in a controller break with their lines; a template literal query in a variable is a candidate; a NestJS import in an entity breaks the domain rule. */"""
        text = _boundaries(_project(TYPESCRIPT))
        self.assertNotIn('src/repositories/users.repository.ts :capability', text)
        self.assertIn('(capability-violation src/services/users.service.ts :capability sql :layer service :allowed (infra) :line 3', text)
        self.assertIn('(capability-violation src/controllers/users.controller.ts :capability http-client :layer api :allowed (infra) :line 1', text)
        self.assertIn('(capability-candidate src/services/search.service.ts :capability sql', text)
        self.assertIn('(capability-violation src/entities/user.ts :capability framework :layer domain', text)

    def test_the_config_restates_a_rule_and_the_baseline_accepts_a_finding(self) -> None:
        """/* REQ-CAP-005: `capabilities:` of a part in .wingbox/config.yaml replaces the style's rule for that capability; a `(baseline-capability <part> <module> <capability>)` line of .wingbox/baseline.sexp turns the violation into capability-violation-known. */"""
        config = CONFIG + "    capabilities:\n      sql: {allowed_in: [infra, service]}\n"
        text = _boundaries(_project(PYTHON, config))
        self.assertNotIn('src/services/orders_service.py :capability sql', text)
        root = _project(PYTHON)
        (root / ".wingbox" / "baseline.sexp").write_text(
            '(baseline-capability "." src/services/orders_service sql :from align)\n', encoding="utf-8")
        text = _boundaries(root)
        self.assertIn('(capability-violation-known src/services/orders_service.py :capability sql', text)
        self.assertIn(':known .wingbox/baseline.sexp', text)

    def test_report_and_advice_name_the_file_the_line_and_the_fix(self) -> None:
        """/* REQ-CAP-006: REPORT.md has a "What layers do" section with each violating file, its capability or pattern, layer, line and evidence; `wingbox advice --sexp` lists fix-capability and fix-pattern items with the fix the style prescribes. */"""
        root = _project(PYTHON)
        _boundaries(root)
        report = (root / ".wingbox" / "REPORT.md").read_text(encoding="utf-8")
        self.assertIn("## What layers do", report)
        self.assertIn("| `src/services/orders_service.py` | sql | service | 5 |", report)
        self.assertIn("| `src/routes/orders.py` | controller-skips-service | api |", report)
        advice = _run(["advice", "--sexp"], root)[1]
        self.assertIn(':kind fix-capability :target "src/services/orders_service.py"', advice)
        self.assertIn("move the query into a repository (DAO) in infrastructure", advice)
        self.assertIn(":kind fix-pattern", advice)

    def test_laravel_queries_belong_to_repositories(self) -> None:
        """/* REQ-CAP-007: in a layered PHP (Laravel) part, DB::select in a repository passes and the same call in a controller breaks with its line; an Http facade call in a service breaks the http-client rule. */"""
        files = {
            "app/Repositories/UserRepository.php": "<?php\nnamespace App\\Repositories;\n\nclass UserRepository\n{\n    public function all() { return DB::select('select * from users'); }\n}\n",
            "app/Http/Controllers/UserController.php": "<?php\nnamespace App\\Http\\Controllers;\n\nclass UserController\n{\n    public function index() { return DB::select('select * from users'); }\n}\n",
            "app/Services/Mailer.php": "<?php\nnamespace App\\Services;\n\nuse Illuminate\\Support\\Facades\\Http;\n\nclass Mailer {}\n",
        }
        config = "parts:\n  .:\n    style: layered\n    layers:\n      api: [Controllers]\n      service: [Services]\n      infra: [Repositories]\n"
        text = _boundaries(_project(files, config))
        self.assertNotIn("app/Repositories/UserRepository.php :capability", text)
        self.assertIn("(capability-violation app/Http/Controllers/UserController.php :capability sql :layer api :allowed (infra) :line 6", text)
        self.assertIn("(capability-violation app/Services/Mailer.php :capability http-client :layer service :allowed (infra) :line 4", text)

    def test_align_learn_accepts_the_current_breaks_in_the_baseline(self) -> None:
        """/* REQ-CAP-008: `wingbox align --learn` writes each capability violation into .wingbox/baseline.sexp as baseline-capability; after a rebuild they are capability-violation-known and only new ones are violations. */"""
        root = _project(PYTHON)
        _boundaries(root)
        self.assertEqual(0, _run(["align", "--learn", "--sexp"], root)[0])
        baseline = (root / ".wingbox" / "baseline.sexp").read_text(encoding="utf-8")
        self.assertIn('(baseline-capability "." src/services/orders_service sql :line 5 :from align)', baseline)
        text = _boundaries(root)
        self.assertIn("(capability-violation-known src/services/orders_service.py :capability sql", text)
        self.assertNotIn("(capability-violation src/services/orders_service.py", text)

    def test_a_rebuild_reads_again_only_changed_files(self) -> None:
        """/* REQ-CAP-009: the findings are kept by content hash in .wingbox/cache.capabilities.json (ignored by .wingbox/.gitignore); a rebuild takes an unchanged file's findings from it and reads a changed file again. */"""
        import json

        root = _project(PYTHON)
        _boundaries(root)
        path = root / ".wingbox" / "cache.capabilities.json"
        stored = json.loads(path.read_text(encoding="utf-8"))
        stored["files"]["src/services/orders_service.py"][1][0][2] = "kept from the cache"
        path.write_text(json.dumps(stored), encoding="utf-8")
        self.assertIn(':evidence "kept from the cache"', _boundaries(root))
        (root / "src/services/orders_service.py").write_text('def total(cursor):\n    cursor.execute("DELETE FROM orders")\n', encoding="utf-8")
        self.assertIn(':line 2 :evidence "cursor.execute(\\"DELETE FROM orders\\")"', _boundaries(root))


if __name__ == "__main__":
    unittest.main()
