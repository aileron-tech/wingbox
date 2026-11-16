from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.build_code_graph import Ok, build_graph, discover_files
from scripts.lang.interface import FileResult
from scripts.lang.typescript import parse_typescript_treesitter


class TypeScriptResolverTests(unittest.TestCase):
    def _write(self, root: Path, relative_path: str, content: str) -> Path:
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def _parse(self, root: Path, relative_path: str, content: str) -> FileResult:
        path = self._write(root, relative_path, content)
        return parse_typescript_treesitter(
            str(path),
            content,
            root.name,
            path.stat().st_mtime,
        )

    def _import_edges(
        self,
        results: list[FileResult],
        root: Path,
    ) -> set[tuple[str, str]]:
        graph_result = build_graph(results, str(root))
        if not isinstance(graph_result, Ok):
            self.fail(f"TypeScript graph build failed: {graph_result.error}")
        return {
            (edge["source"], edge["target"])
            for edge in graph_result.value.edges
            if edge["type"] == "IMPORTS"
        }

    def test_url_in_a_tsconfig_string_keeps_its_path_aliases(self) -> None:
        """/* REQ-TS-ALIAS-001: A "//" inside a tsconfig string, such as a $schema URL, is not a comment, so the paths of that tsconfig still resolve. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write(
                root,
                "apps/chat/tsconfig.json",
                """{
  "$schema": "https://json.schemastore.org/tsconfig",
  "compilerOptions": {
    "paths": {
      "@/*": ["./*"]
    }
  }
}
""",
            )
            results = [
                self._parse(
                    root,
                    "apps/chat/app/page.tsx",
                    'import { cn } from "@/lib/utils";\nexport const Page = cn;\n',
                ),
                self._parse(
                    root,
                    "apps/chat/lib/utils.ts",
                    "export const cn = 1;\n",
                ),
            ]

            import_edges = self._import_edges(results, root)

        self.assertIn(("apps/chat/app/page", "apps/chat/lib/utils"), import_edges)

    def test_tsconfig_comments_and_trailing_commas_are_ignored(self) -> None:
        """/* REQ-TS-ALIAS-002: Line comments, block comments and trailing commas of a tsconfig are ignored, so its paths still resolve. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write(
                root,
                "tsconfig.json",
                """{
  /* See https://example.com/tsconfig for the meaning of each option */
  "compilerOptions": {
    "baseUrl": ".", // the project root
    "paths": {
      "~/*": ["src/*",], /* one target */
    },
  },
}
""",
            )
            results = [
                self._parse(
                    root,
                    "src/app.ts",
                    'import { helper } from "~/helper";\nexport const app = helper;\n',
                ),
                self._parse(root, "src/helper.ts", "export const helper = 1;\n"),
            ]

            import_edges = self._import_edges(results, root)

        self.assertIn(("src/app", "src/helper"), import_edges)

    def test_paths_inherited_through_extends_resolve_from_the_declaring_file(
        self,
    ) -> None:
        """/* REQ-TS-ALIAS-003: A tsconfig that only extends a base file inherits its paths, and the inherited paths resolve relative to the file that declares them. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write(
                root,
                "tsconfig.base.json",
                '{"compilerOptions": {"paths": {"@shared/*": ["./libs/shared/src/*"]}}}\n',
            )
            self._write(
                root,
                "apps/web/tsconfig.json",
                '{"extends": "../../tsconfig.base.json"}\n',
            )
            results = [
                self._parse(
                    root,
                    "apps/web/src/main.ts",
                    'import { format } from "@shared/format";\nexport const main = format;\n',
                ),
                self._parse(
                    root,
                    "libs/shared/src/format.ts",
                    "export const format = 1;\n",
                ),
            ]

            import_edges = self._import_edges(results, root)

        self.assertEqual(
            {("apps/web/src/main", "libs/shared/src/format")},
            import_edges,
        )

    def test_extends_chain_and_list_let_the_nearest_declaration_win(self) -> None:
        """/* REQ-TS-ALIAS-004: Through a chain or a list of extends the nearest paths and the nearest baseUrl win, and paths resolve against the effective baseUrl. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write(
                root,
                "tsconfig.base.json",
                '{"compilerOptions": {"baseUrl": "./packages", "paths": {"@core/*": ["core/src/*"]}}}\n',
            )
            self._write(
                root,
                "configs/strict.json",
                '{"extends": "../tsconfig.base", "compilerOptions": {"strict": true}}\n',
            )
            self._write(
                root,
                "configs/aliases.json",
                '{"compilerOptions": {"paths": {"@core/*": ["core/next/*"], "@ui/*": ["ui/src/*"]}}}\n',
            )
            self._write(
                root,
                "apps/api/tsconfig.json",
                '{"extends": "../../configs/strict.json"}\n',
            )
            self._write(
                root,
                "apps/web/tsconfig.json",
                '{"extends": ["../../configs/strict.json", "../../configs/aliases.json"]}\n',
            )
            consumer = (
                'import { core } from "@core/api";\n'
                'import { button } from "@ui/button";\n'
                "export const main = [core, button];\n"
            )
            results = [
                self._parse(root, "apps/api/main.ts", consumer),
                self._parse(root, "apps/web/main.ts", consumer),
                self._parse(
                    root, "packages/core/src/api.ts", "export const core = 1;\n"
                ),
                self._parse(
                    root, "packages/core/next/api.ts", "export const core = 2;\n"
                ),
                self._parse(
                    root, "packages/ui/src/button.ts", "export const button = 1;\n"
                ),
            ]

            import_edges = self._import_edges(results, root)

        self.assertEqual(
            {
                ("apps/api/main", "packages/core/src/api"),
                ("apps/web/main", "packages/core/next/api"),
                ("apps/web/main", "packages/ui/src/button"),
            },
            import_edges,
        )

    def test_circular_extends_keeps_the_paths_of_the_tsconfig_itself(self) -> None:
        """/* REQ-TS-ALIAS-005: A circular extends chain ends at the repeated file; the paths declared by the tsconfig itself still resolve. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write(
                root,
                "tsconfig.json",
                '{"extends": "./tsconfig.loop.json", "compilerOptions": {"paths": {"~/*": ["./src/*"]}}}\n',
            )
            self._write(
                root,
                "tsconfig.loop.json",
                '{"extends": "./tsconfig.json"}\n',
            )
            results = [
                self._parse(
                    root,
                    "src/app.ts",
                    'import { helper } from "~/helper";\nexport const app = helper;\n',
                ),
                self._parse(root, "src/helper.ts", "export const helper = 1;\n"),
            ]

            import_edges = self._import_edges(results, root)

        self.assertEqual({("src/app", "src/helper")}, import_edges)

    def test_tsconfig_saved_with_a_byte_order_mark_is_read(self) -> None:
        """/* REQ-TS-ALIAS-006: A tsconfig saved with a UTF-8 byte order mark is read, so its paths still resolve. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write(
                root,
                "tsconfig.json",
                '\ufeff{"compilerOptions": {"paths": {"~/*": ["./src/*"]}}}\n',
            )
            results = [
                self._parse(
                    root,
                    "src/app.ts",
                    'import { helper } from "~/helper";\nexport const app = helper;\n',
                ),
                self._parse(root, "src/helper.ts", "export const helper = 1;\n"),
            ]

            import_edges = self._import_edges(results, root)

        self.assertEqual({("src/app", "src/helper")}, import_edges)

    def test_bare_import_of_a_repository_package_reaches_its_source(self) -> None:
        """/* REQ-TS-WS-001: An import that names a package of the repository resolves to the source entry of that package. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write(
                root,
                "packages/core/package.json",
                '{"name": "@fixture/core"}\n',
            )
            self._write(
                root,
                "packages/app/package.json",
                '{"name": "@fixture/app", "dependencies": {"@fixture/core": "workspace:^"}}\n',
            )
            results = [
                self._parse(
                    root,
                    "packages/app/lib/main.ts",
                    'import { core } from "@fixture/core";\nexport const main = core;\n',
                ),
                self._parse(
                    root,
                    "packages/core/src/index.ts",
                    "export const core = 1;\n",
                ),
            ]

            import_edges = self._import_edges(results, root)

        self.assertEqual(
            {("packages/app/lib/main", "packages/core/src")},
            import_edges,
        )

    def test_subpath_import_resolves_inside_the_repository_package(self) -> None:
        """/* REQ-TS-WS-002: A subpath import of a repository package resolves to the file of that path inside the package, and a path into its build output resolves to the source. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write(
                root,
                "packages/core/package.json",
                '{"name": "@fixture/core", "main": "./dist/index.js"}\n',
            )
            results = [
                self._parse(
                    root,
                    "apps/web/main.ts",
                    "\n".join(
                        (
                            'import { fake } from "@fixture/core/testing/fake";',
                            'import { format } from "@fixture/core/dist/text/format.js";',
                            "export const main = [fake, format];",
                            "",
                        )
                    ),
                ),
                self._parse(
                    root,
                    "packages/core/src/index.ts",
                    "export const core = 1;\n",
                ),
                self._parse(
                    root,
                    "packages/core/testing/fake.ts",
                    "export const fake = 1;\n",
                ),
                self._parse(
                    root,
                    "packages/core/src/text/format.ts",
                    "export const format = 1;\n",
                ),
            ]

            import_edges = self._import_edges(results, root)

        self.assertEqual(
            {
                ("apps/web/main", "packages/core/testing/fake"),
                ("apps/web/main", "packages/core/src/text/format"),
            },
            import_edges,
        )

    def test_exports_map_selects_the_module_of_each_subpath(self) -> None:
        """/* REQ-TS-WS-003: The exports map of a repository package decides the module of the package root, of a named subpath and of a pattern subpath. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write(
                root,
                "packages/core/package.json",
                """{
  "name": "@fixture/core",
  "main": "./dist/legacy.js",
  "exports": {
    ".": {"types": "./dist/api.d.ts", "import": "./dist/api.js"},
    "./client": {"types": "./dist/http/client.d.ts", "default": "./dist/http/client.js"},
    "./features/*": "./dist/features/*.js",
    "./package.json": "./package.json"
  }
}
""",
            )
            results = [
                self._parse(
                    root,
                    "apps/web/main.ts",
                    "\n".join(
                        (
                            'import { api } from "@fixture/core";',
                            'import { client } from "@fixture/core/client";',
                            'import { login } from "@fixture/core/features/login";',
                            "export const main = [api, client, login];",
                            "",
                        )
                    ),
                ),
                self._parse(
                    root, "packages/core/src/api.ts", "export const api = 1;\n"
                ),
                self._parse(
                    root,
                    "packages/core/src/legacy.ts",
                    "export const legacy = 1;\n",
                ),
                self._parse(
                    root,
                    "packages/core/src/http/client.ts",
                    "export const client = 1;\n",
                ),
                self._parse(
                    root,
                    "packages/core/src/features/login.ts",
                    "export const login = 1;\n",
                ),
            ]

            import_edges = self._import_edges(results, root)

        self.assertEqual(
            {
                ("apps/web/main", "packages/core/src/api"),
                ("apps/web/main", "packages/core/src/http/client"),
                ("apps/web/main", "packages/core/src/features/login"),
            },
            import_edges,
        )

    def test_entry_in_the_build_output_resolves_to_the_source_file(self) -> None:
        """/* REQ-TS-WS-004: A package entry that points at a build artefact (main, module, types) resolves to the TypeScript source it is built from, not to the index fallback. */"""
        manifests = (
            '{"name": "@fixture/core", "main": "./dist/server.js"}',
            '{"name": "@fixture/core", "module": "dist/esm/server.mjs"}',
            '{"name": "@fixture/core", "types": "./dist/types/server.d.ts"}',
            '{"name": "@fixture/core", "source": "./src/server.ts", "main": "./dist/bundle.js"}',
        )
        for manifest in manifests:
            with self.subTest(manifest=manifest):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    self._write(root, "packages/core/package.json", manifest)
                    results = [
                        self._parse(
                            root,
                            "apps/web/main.ts",
                            'import { server } from "@fixture/core";\nexport const main = server;\n',
                        ),
                        self._parse(
                            root,
                            "packages/core/src/server.ts",
                            "export const server = 1;\n",
                        ),
                        self._parse(
                            root,
                            "packages/core/src/index.ts",
                            "export const other = 1;\n",
                        ),
                    ]

                    import_edges = self._import_edges(results, root)

                self.assertEqual(
                    {("apps/web/main", "packages/core/src/server")},
                    import_edges,
                )

    def test_package_outside_the_repository_stays_external(self) -> None:
        """/* REQ-TS-WS-005: An import of a package that no package.json of the repository declares creates no edge, even when its name starts like a repository package. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write(
                root,
                "packages/core/package.json",
                '{"name": "@fixture/core"}\n',
            )
            results = [
                self._parse(
                    root,
                    "apps/web/main.ts",
                    "\n".join(
                        (
                            'import { useState } from "react";',
                            'import { render } from "react-dom/client";',
                            'import { absent } from "@fixture/absent";',
                            'import { extra } from "@fixture/core-extra";',
                            'import { readFile } from "node:fs/promises";',
                            "export const main = [useState, render, absent, extra, readFile];",
                            "",
                        )
                    ),
                ),
                self._parse(
                    root,
                    "packages/core/src/index.ts",
                    "export const core = 1;\n",
                ),
            ]

            import_edges = self._import_edges(results, root)

        self.assertEqual(set(), import_edges)

    def test_package_name_declared_twice_is_not_guessed(self) -> None:
        """/* REQ-TS-WS-006: A package name declared by several package.json files resolves only for an importer inside one of them; elsewhere it creates no edge. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            results: list[FileResult] = []
            for copy in ("first", "second"):
                self._write(
                    root,
                    f"templates/{copy}/package.json",
                    '{"name": "@fixture/template"}\n',
                )
                results.extend(
                    (
                        self._parse(
                            root,
                            f"templates/{copy}/src/index.ts",
                            "export const template = 1;\n",
                        ),
                        self._parse(
                            root,
                            f"templates/{copy}/demo/run.ts",
                            'import { template } from "@fixture/template";\nexport const run = template;\n',
                        ),
                    )
                )
            results.append(
                self._parse(
                    root,
                    "apps/web/main.ts",
                    'import { template } from "@fixture/template";\nexport const main = template;\n',
                )
            )

            import_edges = self._import_edges(results, root)

        self.assertEqual(
            {
                ("templates/first/demo/run", "templates/first/src"),
                ("templates/second/demo/run", "templates/second/src"),
            },
            import_edges,
        )

    def _write_shared_workspace(self, root: Path) -> list[FileResult]:
        """A monorepo whose root manifests list `backend`, `frontend` and `shared`."""
        self._write(
            root,
            "package.json",
            '{"name": "fixture-monorepo", "private": true, "workspaces": ["backend", "frontend", "shared"]}\n',
        )
        self._write(
            root,
            "pnpm-workspace.yaml",
            'packages:\n  - "backend"\n  - "frontend"\n  - "shared"\n',
        )
        self._write(
            root,
            "shared/package.json",
            """{
  "name": "@fixture/shared",
  "exports": {
    ".": "./index.ts",
    "./consts": "./consts.ts",
    "./api-sdk": "./generated/sdk.gen.ts"
  }
}
""",
        )
        return [
            self._parse(root, "shared/index.ts", "export const shared = 1;\n"),
            self._parse(root, "shared/consts.ts", "export const limit = 1;\n"),
            self._parse(root, "shared/generated/sdk.gen.ts", "export const sdk = 1;\n"),
        ]

    def test_workspace_member_is_resolved_from_a_sibling_package(self) -> None:
        """/* REQ-TS-WS-007: A package listed by the workspace globs of the root package.json and pnpm-workspace.yaml resolves by its scoped name from a sibling package, root and subpaths alike. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            results = self._write_shared_workspace(root)
            self._write(
                root,
                "backend/package.json",
                '{"name": "@fixture/backend", "dependencies": {"@fixture/shared": "workspace:*"}}\n',
            )
            results.append(
                self._parse(
                    root,
                    "backend/src/server.ts",
                    "\n".join(
                        (
                            'import { shared } from "@fixture/shared";',
                            'import { limit } from "@fixture/shared/consts";',
                            'import { sdk } from "@fixture/shared/api-sdk";',
                            "export const server = [shared, limit, sdk];",
                            "",
                        )
                    ),
                )
            )

            import_edges = self._import_edges(results, root)

        self.assertEqual(
            {
                ("backend/src/server", "shared"),
                ("backend/src/server", "shared/consts"),
                ("backend/src/server", "shared/generated/sdk.gen"),
            },
            import_edges,
        )

    def test_alias_and_package_name_reach_the_same_module_once(self) -> None:
        """/* REQ-TS-WS-008: A repository package that a tsconfig alias also maps resolves to the same source file by either way, and one importer gets one edge to it. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            results = self._write_shared_workspace(root)
            self._write(
                root,
                "backend/tsconfig.json",
                '{"compilerOptions": {"paths": {"@fixture/shared/*": ["../shared/*"]}}}\n',
            )
            results.extend(
                (
                    self._parse(
                        root,
                        "backend/src/server.ts",
                        "\n".join(
                            (
                                'import { shared } from "@fixture/shared";',
                                'import { limit } from "@fixture/shared/consts";',
                                'import type { Limit } from "@fixture/shared/consts.ts";',
                                'import { sdk } from "@fixture/shared/api-sdk";',
                                'export { limit as again } from "../../shared/consts";',
                                "export const server = [shared, limit, sdk];",
                                "",
                            )
                        ),
                    ),
                    self._parse(
                        root,
                        "frontend/src/page.tsx",
                        'import { limit } from "@fixture/shared/consts";\nexport const page = limit;\n',
                    ),
                )
            )

            graph_result = build_graph(results, str(root))
            if not isinstance(graph_result, Ok):
                self.fail(f"TypeScript graph build failed: {graph_result.error}")
            import_edges = [
                (edge["source"], edge["target"])
                for edge in graph_result.value.edges
                if edge["type"] == "IMPORTS"
            ]

        self.assertEqual(
            [
                ("backend/src/server", "shared"),
                ("backend/src/server", "shared/consts"),
                ("backend/src/server", "shared/generated/sdk.gen"),
                ("frontend/src/page", "shared/consts"),
            ],
            sorted(import_edges),
        )

    def test_javascript_files_are_modules_with_their_imports(self) -> None:
        """/* REQ-TS-JS-001: .js, .jsx, .mjs and .cjs files are discovered and become modules; their import and require statements create edges, and JSX in a .js file is parsed. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            sources = {
                "web/app.js": 'import { View } from "./view.jsx";\nexport const app = () => <View />;\n',
                "web/view.jsx": 'import { util } from "../lib/util";\nexport const View = () => <p>{util}</p>;\n',
                "lib/util.js": "export const util = 1;\n",
                "scripts/build.mjs": 'import legacy from "./legacy.cjs";\nexport default legacy;\n',
                "scripts/legacy.cjs": 'const { util } = require("../lib/util.js");\nmodule.exports = util;\n',
            }
            for relative_path, content in sources.items():
                self._write(root, relative_path, content)

            discovered = {
                Path(found).relative_to(root).as_posix(): language
                for found, language, _ in discover_files(str(root))
            }
            results = [
                self._parse(root, relative_path, content)
                for relative_path, content in sources.items()
            ]
            graph_result = build_graph(results, str(root))
            if not isinstance(graph_result, Ok):
                self.fail(f"JavaScript graph build failed: {graph_result.error}")
            module_ids = {node["id"] for node in graph_result.value.nodes}
            import_edges = {
                (edge["source"], edge["target"])
                for edge in graph_result.value.edges
                if edge["type"] == "IMPORTS"
            }

        self.assertEqual(dict.fromkeys(sources, "typescript"), discovered)
        self.assertEqual({"javascript"}, {result.language for result in results})
        self.assertEqual(
            {
                "web/app",
                "web/view",
                "lib/util",
                "scripts/build.mjs",
                "scripts/legacy.cjs",
            },
            module_ids,
        )
        self.assertEqual(
            {
                ("web/app", "web/view"),
                ("web/view", "lib/util"),
                ("scripts/build.mjs", "scripts/legacy.cjs"),
                ("scripts/legacy.cjs", "lib/util"),
            },
            import_edges,
        )

    def test_js_specifier_prefers_the_typescript_source(self) -> None:
        """/* REQ-TS-JS-002: An import written as ./x.js maps to x.ts when x.ts exists, also when a real x.js lies next to it. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            results = [
                self._parse(
                    root,
                    "src/main.ts",
                    'import { value } from "./value.js";\nexport const main = value;\n',
                ),
                self._parse(root, "src/value.ts", "export const value = 1;\n"),
                self._parse(root, "src/value.js", "export const value = 1;\n"),
            ]

            import_edges = self._import_edges(results, root)

        self.assertEqual({("src/main", "src/value")}, import_edges)

    def test_real_javascript_file_is_the_target_without_a_typescript_source(
        self,
    ) -> None:
        """/* REQ-TS-JS-003: Without x.ts an import of ./x.js or ./x resolves to the real x.js, before a folder index of the same name. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            results = [
                self._parse(
                    root,
                    "src/main.ts",
                    'import { helper } from "./helper.js";\nexport const main = helper;\n',
                ),
                self._parse(
                    root,
                    "src/other.ts",
                    'import { helper } from "./helper";\nexport const other = helper;\n',
                ),
                self._parse(root, "src/helper.js", "export const helper = 1;\n"),
                self._parse(root, "src/helper/index.ts", "export const nested = 1;\n"),
            ]

            import_edges = self._import_edges(results, root)

        self.assertEqual(
            {("src/main", "src/helper"), ("src/other", "src/helper")},
            import_edges,
        )

    def test_mjs_and_cjs_specifiers_resolve_by_their_own_extension(self) -> None:
        """/* REQ-TS-JS-004: An import of ./x.mjs or ./x.cjs resolves to the file with that extension, not to x.ts or x.js of the same name. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            results = [
                self._parse(
                    root,
                    "tools/run.ts",
                    "\n".join(
                        (
                            'import { load } from "./load.mjs";',
                            'import { legacy } from "./load.cjs";',
                            'import { typed } from "./load.js";',
                            "export const run = [load, legacy, typed];",
                            "",
                        )
                    ),
                ),
                self._parse(root, "tools/load.ts", "export const typed = 1;\n"),
                self._parse(root, "tools/load.mjs", "export const load = 1;\n"),
                self._parse(root, "tools/load.cjs", "exports.legacy = 1;\n"),
            ]

            import_edges = self._import_edges(results, root)

        self.assertEqual(
            {
                ("tools/run", "tools/load"),
                ("tools/run", "tools/load.mjs"),
                ("tools/run", "tools/load.cjs"),
            },
            import_edges,
        )

    def test_mts_and_cts_files_are_modules_named_by_mjs_and_cjs_imports(self) -> None:
        """/* REQ-TS-JS-006: .mts and .cts files are TypeScript modules (declaration files .d.mts and .d.cts are not); an import of ./x.mjs or ./x.cjs names x.mts or x.cts when it exists, as TypeScript resolves it. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            sources = {
                "scripts/build.mts": 'import { shard } from "./shard.mjs";\nimport { legacy } from "./legacy.cjs";\nimport { core } from "../src/core";\nexport const run = [shard, legacy, core];\n',
                "scripts/shard.mts": "export const shard = 1;\n",
                "scripts/legacy.cts": "export const legacy = 1;\n",
                "scripts/types.d.mts": "export declare const typed: number;\n",
                "src/core.ts": "export const core = 1;\n",
            }
            for relative_path, content in sources.items():
                self._write(root, relative_path, content)
            discovered = sorted(
                Path(item[0]).resolve().relative_to(root).as_posix() for item in discover_files(str(root))
            )
            results = [self._parse(root, relative_path, content) for relative_path, content in sources.items() if not relative_path.endswith(".d.mts")]
            import_edges = self._import_edges(results, root)

        self.assertEqual(["scripts/build.mts", "scripts/legacy.cts", "scripts/shard.mts", "src/core.ts"], discovered)
        self.assertEqual(
            {
                ("scripts/build.mts", "scripts/shard.mts"),
                ("scripts/build.mts", "scripts/legacy.cts"),
                ("scripts/build.mts", "src/core"),
            },
            import_edges,
        )

    def test_module_mocks_and_commented_dynamic_imports_are_dependencies(self) -> None:
        """/* REQ-TS-RT-002: a test that mocks a module (vi.mock, vi.doMock, vi.importActual, vi.importMock, jest.mock, jest.doMock, jest.requireActual, jest.requireMock with a literal) depends on it, and import() or require() whose literal follows a comment names the module. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            results = [
                self._parse(
                    root,
                    "src/app.test.ts",
                    "\n".join(
                        (
                            'vi.mock("./clients/llm", () => ({ call: vi.fn() }));',
                            'vi.doMock("./clients/mcp");',
                            'const real = await vi.importActual<typeof import("./models/user")>("./models/user");',
                            'jest.mock("./queue");',
                            'const actual = jest.requireActual("./cache");',
                            "export async function load() {",
                            "  const { default: Provider } = await import(",
                            "    // runtime-gated model",
                            '    "./models/provider"',
                            "  );",
                            "  return [Provider, real, actual];",
                            "}",
                            "",
                        )
                    ),
                ),
                self._parse(root, "src/clients/llm.ts", "export const call = 1;\n"),
                self._parse(root, "src/clients/mcp.ts", "export const mcp = 1;\n"),
                self._parse(root, "src/models/user.ts", "export const user = 1;\n"),
                self._parse(root, "src/models/provider.ts", "export default class Provider {}\n"),
                self._parse(root, "src/queue.ts", "export const queue = 1;\n"),
                self._parse(root, "src/cache.ts", "export const cache = 1;\n"),
            ]
            import_edges = self._import_edges(results, root)

        self.assertEqual(
            {
                ("src/app.test", "src/clients/llm"),
                ("src/app.test", "src/clients/mcp"),
                ("src/app.test", "src/models/user"),
                ("src/app.test", "src/models/provider"),
                ("src/app.test", "src/queue"),
                ("src/app.test", "src/cache"),
            },
            import_edges,
        )

    def test_generated_javascript_is_not_a_module(self) -> None:
        """/* REQ-TS-JS-005: Minified files (.min.js or a line over 5,000 characters), bundles (bundle in the name) and JavaScript under node_modules, dist, build or vendor are not discovered; written JavaScript next to them is. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            written = "export const value = 1;\n"
            minified = "/* licence */\n" + "a=1;" * 2000 + "\n"
            for relative_path, content in {
                "src/app.js": written,
                "src/long-line.js": 'export const text = "' + "x" * 3000 + '";\n',
                "src/lib.min.js": written,
                "src/hook.js": minified,
                "src/widget.bundle.js": written,
                "src/third_party/engine-bundle.js": written,
                "node_modules/pkg/index.js": written,
                "dist/app.js": written,
                "build/app.mjs": written,
                "vendor/lib.cjs": written,
            }.items():
                self._write(root, relative_path, content)

            discovered = sorted(
                Path(found).relative_to(root).as_posix()
                for found, _, _ in discover_files(str(root))
            )

        self.assertEqual(["src/app.js", "src/long-line.js"], discovered)

    def test_import_through_the_output_directory_reaches_the_source(self) -> None:
        """/* REQ-TS-OUT-001: A relative import that goes through the outDir of a tsconfig resolves to the file under its rootDir that it is compiled from. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write(
                root,
                "tsconfig.json",
                '{"compilerOptions": {"outDir": "./out/gen", "rootDir": "."}}\n',
            )
            results = [
                self._parse(
                    root,
                    "scripts/generate.ts",
                    "\n".join(
                        (
                            'import { options } from "../out/gen/src/config/options.js";',
                            'import { server } from "../out/gen/tests/server.js";',
                            "export const generate = [options, server];",
                            "",
                        )
                    ),
                ),
                self._parse(
                    root,
                    "tests/smoke.test.mjs",
                    'import { options } from "../out/gen/src/config/options.js";\nexport default options;\n',
                ),
                self._parse(
                    root, "src/config/options.ts", "export const options = 1;\n"
                ),
                self._parse(root, "tests/server.ts", "export const server = 1;\n"),
            ]

            import_edges = self._import_edges(results, root)

        self.assertEqual(
            {
                ("scripts/generate", "src/config/options"),
                ("scripts/generate", "tests/server"),
                ("tests/smoke.test.mjs", "src/config/options"),
            },
            import_edges,
        )

    def test_package_entry_in_a_declared_output_directory_reaches_its_source(
        self,
    ) -> None:
        """/* REQ-TS-OUT-002: The outDir and rootDir of any tsconfig*.json of a package map its exports entry and a relative import of its build output to the same source file. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write(
                root,
                "packages/plugin/package.json",
                '{"name": "@fixture/plugin", "exports": {"./runtime": "./dist/runtime/start.js"}}\n',
            )
            self._write(
                root,
                "packages/plugin/tsconfig.runtime.json",
                """{
  // emits dist/runtime from extension/lib
  "compilerOptions": {"outDir": "dist/runtime", "rootDir": "extension/lib"}
}
""",
            )
            results = [
                self._parse(
                    root,
                    "apps/web/main.ts",
                    'import { start } from "@fixture/plugin/runtime";\nexport const main = start;\n',
                ),
                self._parse(
                    root,
                    "packages/plugin/test/start.test.mjs",
                    'import { start } from "../dist/runtime/start.js";\nexport default start;\n',
                ),
                self._parse(
                    root,
                    "packages/plugin/extension/lib/start.ts",
                    "export const start = 1;\n",
                ),
            ]

            import_edges = self._import_edges(results, root)

        self.assertEqual(
            {
                ("apps/web/main", "packages/plugin/extension/lib/start"),
                (
                    "packages/plugin/test/start.test.mjs",
                    "packages/plugin/extension/lib/start",
                ),
            },
            import_edges,
        )

    def test_inherited_output_directory_without_root_directory(self) -> None:
        """/* REQ-TS-OUT-003: An outDir inherited through extends counts, and without a rootDir the source is looked up next to the tsconfig, then in its src folder. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write(
                root,
                "packages/core/tsconfig.build.json",
                '{"compilerOptions": {"outDir": "./lib"}, "include": ["src"]}\n',
            )
            self._write(
                root,
                "packages/core/tsconfig.json",
                '{"extends": "./tsconfig.build.json"}\n',
            )
            results = [
                self._parse(
                    root,
                    "packages/core/test/format.test.ts",
                    "\n".join(
                        (
                            'import { format } from "../lib/format.js";',
                            'import { version } from "../lib/version.js";',
                            "export const checked = [format, version];",
                            "",
                        )
                    ),
                ),
                self._parse(
                    root, "packages/core/src/format.ts", "export const format = 1;\n"
                ),
                self._parse(
                    root, "packages/core/version.ts", "export const version = 1;\n"
                ),
            ]

            import_edges = self._import_edges(results, root)

        self.assertEqual(
            {
                ("packages/core/test/format.test", "packages/core/src/format"),
                ("packages/core/test/format.test", "packages/core/version"),
            },
            import_edges,
        )

    def test_output_directory_mapping_never_guesses(self) -> None:
        """/* REQ-TS-OUT-004: A file that really lies in the output directory is the target itself, an output path without a source creates no edge, and a build folder that no tsconfig declares is not mapped. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write(
                root,
                "tsconfig.json",
                '{"compilerOptions": {"outDir": "./out", "rootDir": "./src"}}\n',
            )
            results = [
                self._parse(
                    root,
                    "scripts/check.ts",
                    "\n".join(
                        (
                            'import { kept } from "../out/kept.js";',
                            'import { gone } from "../out/gone.js";',
                            'import { other } from "../release/other.js";',
                            "export const check = [kept, gone, other];",
                            "",
                        )
                    ),
                ),
                self._parse(root, "out/kept.js", "export const kept = 1;\n"),
                self._parse(root, "src/kept.ts", "export const kept = 2;\n"),
                self._parse(root, "src/other.ts", "export const other = 1;\n"),
            ]

            import_edges = self._import_edges(results, root)

        self.assertEqual({("scripts/check", "out/kept")}, import_edges)


if __name__ == "__main__":
    unittest.main()
