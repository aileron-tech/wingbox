"""A folder named `vendor` is left out only when it holds dependencies, not on its name alone."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts import build_code_graph


def _write(root: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def _discovered(root: Path) -> set[str]:
    return {Path(path).relative_to(root).as_posix() for path, _language, _repo in build_code_graph.discover_files(str(root))}


class VendorFolderTest(unittest.TestCase):
    def test_a_first_party_folder_named_vendor_inside_the_sources_is_kept(self) -> None:
        """/* REQ-CS-075: a folder named vendor inside the sources, with no dependency manager's files in it or a project manifest next to it (bitwarden/clients libs/common/src/tools/extension/vendor: mail providers), is part of the graph with its import edges; `vendor` folders that hold dependencies stay out: Go (modules.txt), Composer (autoload.php or composer/), Ruby (bundle/), and a vendor folder next to a project manifest (package.json, go.mod, composer.json, Gemfile ...). */"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            _write(root, {
                "package.json": '{"name": "clients"}\n',
                "libs/common/src/tools/extension/vendor/fastmail.ts": "export const fastmail = 1;\n",
                "libs/common/src/tools/extension/vendor/index.ts": 'export { fastmail } from "./fastmail";\n',
                "libs/common/src/tools/extension/registry.ts": 'import { fastmail } from "./vendor";\nexport const all = [fastmail];\n',
                "vendor/jquery.js": "window.$ = 1;\n",
                "go/go.mod": "module example.com/app\n\ngo 1.22\n",
                "go/main.go": "package main\n",
                "go/vendor/modules.txt": "# example.com/lib v1.0.0\n",
                "go/vendor/example.com/lib/lib.go": "package lib\n",
                "php/composer.json": "{}\n",
                "php/src/App.php": "<?php\nclass App {}\n",
                "php/vendor/autoload.php": "<?php\n",
                "php/vendor/acme/lib/src/Lib.php": "<?php\nclass Lib {}\n",
                "ruby/Gemfile": "source 'https://rubygems.org'\n",
                "ruby/vendor/bundle/ruby/3.3.0/gems/x/lib/x.rb": "module X; end\n",
            })
            found = _discovered(root)
            self.assertIn("libs/common/src/tools/extension/vendor/fastmail.ts", found)
            self.assertIn("libs/common/src/tools/extension/vendor/index.ts", found)
            for left_out in ("vendor/jquery.js", "go/vendor/example.com/lib/lib.go", "php/vendor/acme/lib/src/Lib.php",
                             "ruby/vendor/bundle/ruby/3.3.0/gems/x/lib/x.rb"):
                self.assertNotIn(left_out, found)
            self.assertIn("go/main.go", found)


if __name__ == "__main__":
    unittest.main()
