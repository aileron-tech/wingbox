"""What the fix packages of the 0.19.1 review must do together: one condition, one message."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SOURCES = {
    "package.json": '{"name": "shop"}\n',
    "src/core/a.ts": "export const a = 1;\n",
    "src/ui/b.ts": 'import { a } from "../core/a";\nexport const b = a;\n',
}
CONFIG = "parts:\n  src:\n    style: declared\n    order:\n      - core: [core]\n      - ui: [ui]\n"


class LinkedConfigTest(unittest.TestCase):
    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        base = Path(os.path.realpath(folder.name))
        self.root, self.outside, self.home = base / "project", base / "outside", base / "home"
        for path in (self.root / ".wingbox", self.outside, self.home):
            path.mkdir(parents=True)
        for name, text in SOURCES.items():
            (self.root / name).parent.mkdir(parents=True, exist_ok=True)
            (self.root / name).write_text(text, encoding="utf-8")

    def build(self) -> subprocess.CompletedProcess:
        environment = {**os.environ, "HOME": str(self.home), "USERPROFILE": str(self.home), "WINGBOX_JOBS": "1",
                       "PYTHONPATH": str(REPO / "src")}
        return subprocess.run([sys.executable, "-P", "-m", "wingbox", "build", "."], cwd=self.root, env=environment,
                              capture_output=True, text=True, encoding="utf-8", check=False)

    def link(self, target: Path) -> Path:
        link = self.root / ".wingbox" / "config.yaml"
        try:
            os.symlink(target, link)
        except (OSError, NotImplementedError) as error:
            self.skipTest(f"this system does not let the test create a symbolic link: {error}")
        return link

    def test_a_build_names_a_config_that_is_a_link_out_of_the_project(self) -> None:
        """/* REQ-ZONE-9990: `wingbox build` stops with one error that names the link, in the words every writer below `.wingbox` uses ("<link> is a symbolic link"), when `.wingbox/config.yaml` is a symbolic link to a file outside the project or to nothing; the file outside stays byte for byte as it was and none is created. */"""
        victim = self.outside / "config.txt"
        victim.write_text(CONFIG, encoding="utf-8")
        for target in (victim, self.outside / "no-config"):
            with self.subTest(target=target.name):
                link = self.link(target)
                done = self.build()
                link.unlink()
                text = done.stderr + done.stdout
                self.assertNotEqual(0, done.returncode, text)
                self.assertNotIn("Traceback", text)
                self.assertIn(f"{link} is a symbolic link", text)
                self.assertEqual(1, text.count("is a symbolic link"), text)
                self.assertEqual(CONFIG, victim.read_text(encoding="utf-8"))
                self.assertEqual(["config.txt"], sorted(path.name for path in self.outside.iterdir()))

    def test_a_link_at_another_file_below_wingbox_is_named_at_most_once(self) -> None:
        """/* REQ-ZONE-9992: when `.wingbox/decisions.sexp` is a symbolic link to a file outside the project, `wingbox build` says "is a symbolic link" at most once in all it prints (the reader of the file and its writer do not both name the link), prints no traceback and leaves the file outside byte for byte as it was. */"""
        (self.root / ".wingbox" / "config.yaml").write_text(CONFIG, encoding="utf-8")
        victim = self.outside / "decisions.txt"
        victim.write_text("; not Wingbox's to write\n", encoding="utf-8")
        try:
            os.symlink(victim, self.root / ".wingbox" / "decisions.sexp")
        except (OSError, NotImplementedError) as error:
            self.skipTest(f"this system does not let the test create a symbolic link: {error}")
        done = self.build()
        text = done.stderr + done.stdout
        self.assertNotIn("Traceback", text)
        self.assertLessEqual(text.count("is a symbolic link"), 1, text)
        self.assertEqual("; not Wingbox's to write\n", victim.read_text(encoding="utf-8"))

    def test_a_config_linked_to_a_file_of_the_project_is_still_read(self) -> None:
        """/* REQ-ZONE-9991: a `.wingbox/config.yaml` that is a symbolic link to a regular file inside the project is read by `wingbox build` as before: the build ends with status 0 and the graph holds the part the file declares. */"""
        kept = self.root / "config" / "wingbox.yaml"
        kept.parent.mkdir()
        kept.write_text(CONFIG, encoding="utf-8")
        self.link(kept)
        done = self.build()
        self.assertEqual(0, done.returncode, done.stderr)
        graph = (self.root / ".wingbox" / "index.sexp").read_text(encoding="utf-8")
        self.assertIn('(arch-part "src" :style declared', graph)


class DottedPartKeyTest(unittest.TestCase):
    def test_a_part_written_with_a_leading_dot_is_judged_by_its_declared_order(self) -> None:
        """/* REQ-ARCH-9993: a config that names its part `./src` and declares `models` below `routes` is applied as the part `src`: after `wingbox build`, `wingbox boundaries --strict` names the import of a route by a model as a violation of that part, counts the 16 files as checked, ends `:status fail` and exits 1. Before, the part was dropped and the built-in layered style judged instead. */"""
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        base = Path(os.path.realpath(folder.name))
        root, home = base / "project", base / "home"
        home.mkdir()
        files = {
            "package.json": '{"name": "game"}\n',
            **{f"src/{name}/{name}{i}.ts": f"export const {name}{i} = {i};\n"
               for name in ("routes", "services", "models", "repositories") for i in range(4)},
            "src/models/models0.ts": 'import { routes0 } from "../routes/routes0";\nexport const models0 = routes0;\n',
            ".wingbox/config.yaml": "parts:\n  ./src:\n    style: declared\n    order:\n      - models: [models]\n      - routes: [routes]\n",
        }
        for name, content in files.items():
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            (root / name).write_text(content, encoding="utf-8")
        environment = {**os.environ, "HOME": str(home), "USERPROFILE": str(home), "WINGBOX_JOBS": "1",
                       "PYTHONPATH": str(REPO / "src")}

        def wingbox(*arguments: str) -> subprocess.CompletedProcess:
            return subprocess.run([sys.executable, "-P", "-m", "wingbox", *arguments], cwd=root, env=environment,
                                  capture_output=True, text=True, encoding="utf-8", check=False)

        built = wingbox("build", ".")
        self.assertEqual(0, built.returncode, built.stderr)
        graph = (root / ".wingbox" / "index.sexp").read_text(encoding="utf-8")
        self.assertIn('(arch-part "src" :style declared', graph)
        strict = wingbox("boundaries", "--strict")
        lines = strict.stdout.splitlines()
        self.assertEqual(1, strict.returncode, strict.stdout + strict.stderr)
        self.assertEqual(1, sum(line.startswith("(violation ") for line in lines), strict.stdout)
        self.assertIn(":part src", next(line for line in lines if line.startswith("(violation ")))
        self.assertTrue(lines[-1].startswith("(boundaries-result :status fail "), lines[-1])
        self.assertIn(":violations 1", lines[-1])


if __name__ == "__main__":
    unittest.main()
