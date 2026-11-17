"""TypeScript imports written from `baseUrl` (no alias) resolve as the compiler resolves them."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.build_code_graph import Ok, build_graph
from scripts.lang.typescript import parse_typescript_treesitter


def _edges(root: Path, files: dict[str, str]) -> set[tuple[str, str]]:
    results = []
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        if name.endswith(".ts"):
            results.append(parse_typescript_treesitter(str(path), text, root.name, path.stat().st_mtime))
    graph = build_graph(results, str(root))
    assert isinstance(graph, Ok), graph
    return {(edge["source"], edge["target"]) for edge in graph.value.edges if edge["type"] == "IMPORTS"}


class BaseUrlTest(unittest.TestCase):
    def test_an_import_written_from_base_url_resolves_with_and_without_paths(self) -> None:
        """/* REQ-TS-BASEURL-001: with `baseUrl` in the owning tsconfig, a bare specifier that no `paths` entry matches is looked up below baseUrl, as TypeScript does (thingsboard ui-ngx: `src/app/core/http/dashboard-report.service` from public-api.ts); `paths` entries still win, a tsconfig with baseUrl and no paths works the same, and a specifier that names no file there stays a package import. */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            edges = _edges(root, {
                "ui-ngx/tsconfig.json": '{"compilerOptions": {"baseUrl": "./", "paths": {"@core/*": ["src/app/core/*"]}}}\n',
                "ui-ngx/src/app/core/http/dashboard-report.service.ts": "export class DashboardReportService {}\n",
                "ui-ngx/src/app/core/http/public-api.ts": "export * from 'src/app/core/http/dashboard-report.service';\n",
                "ui-ngx/src/app/core/api/widget-api.models.ts": "export interface WidgetApi {}\n",
                "ui-ngx/src/app/modules/flot.ts": (
                    "import { WidgetApi } from 'src/app/core/api/widget-api.models';\n"
                    "import { DashboardReportService } from '@core/http/dashboard-report.service';\n"
                    "import { of } from 'rxjs';\nexport const x: WidgetApi | DashboardReportService | typeof of = of;\n"
                ),
                "lib/tsconfig.json": '{"compilerOptions": {"baseUrl": "."}}\n',
                "lib/util/fmt.ts": "export const fmt = 1;\n",
                "lib/main.ts": "import { fmt } from 'util/fmt';\nexport const m = fmt;\n",
            })
            self.assertIn(("ui-ngx/src/app/core/http/public-api", "ui-ngx/src/app/core/http/dashboard-report.service"), edges)
            self.assertIn(("ui-ngx/src/app/modules/flot", "ui-ngx/src/app/core/api/widget-api.models"), edges)
            self.assertIn(("ui-ngx/src/app/modules/flot", "ui-ngx/src/app/core/http/dashboard-report.service"), edges)
            self.assertIn(("lib/main", "lib/util/fmt"), edges)
            self.assertFalse([edge for edge in edges if "rxjs" in edge[1]])


if __name__ == "__main__":
    unittest.main()
