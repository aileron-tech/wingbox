"""Release integrity: workflow actions pinned to commits, the checksum file and the SBOM of a release, a reproducible build."""
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
WORKFLOWS = REPO / ".github" / "workflows"
TOOL = REPO / "tools" / "release_files.py"
USES = re.compile(r"^\s*(?:-\s*)?uses:\s*(?P<target>\S+)(?:\s+#\s*(?P<comment>\S+))?\s*$")
PINNED = re.compile(r"^[\w.-]+/[\w./-]+@[0-9a-f]{40}$")
TAG = re.compile(r"^v\d+\.\d+\.\d+$")
SUM_LINE = re.compile(r"^([0-9a-f]{64})  (\S+)$")

STAND_IN_PROJECT = """\
[project]
name = "demo-tool"
version = "1.2.3"
license = "MIT"
dependencies = [
    "tree-sitter==0.25.2",
    "PyYAML==6.0.3",
    'left_pad==0.1.0; sys_platform != "win32"',
]

[project.optional-dependencies]
same = ["PyYAML==6.0.3"]
http = ["demo-tool-http==1.2.3"]
"""
STAND_IN_FILES = {
    "demo_tool-1.2.3.tar.gz": b"the source archive",
    "demo_tool-1.2.3-py3-none-any.whl": b"the wheel",
    "demo_tool_http-1.2.3-py3-none-any.whl": b"another distribution of the same release",
}


def load_tool():
    spec = importlib.util.spec_from_file_location("release_files", TOOL)
    module = importlib.util.module_from_spec(spec)
    sys.modules["release_files"] = module
    spec.loader.exec_module(module)
    return module


def stand_in(folder: Path, project: str = STAND_IN_PROJECT) -> tuple[Path, Path]:
    """A dist folder with three built files, the .gitignore uv writes there, and the pyproject.toml they were built from."""
    dist = folder / "dist"
    dist.mkdir()
    (dist / ".gitignore").write_bytes(b"*")
    for name, content in STAND_IN_FILES.items():
        (dist / name).write_bytes(content)
    pyproject = folder / "pyproject.toml"
    pyproject.write_text(project, encoding="utf-8")
    return dist, pyproject


def run_tool(dist: Path, pyproject: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(TOOL), str(dist), "--pyproject", str(pyproject)],
                          capture_output=True, text=True)


def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


class WorkflowPinTest(unittest.TestCase):
    def test_every_action_names_a_full_commit(self) -> None:
        """/* REQ-REL-001: every `uses:` of .github/workflows/*.yml that is not a local `./` action names its action at a full commit (40 hexadecimal characters after the @) and carries the release tag of that commit as a trailing comment (`# vX.Y.Z`): a tag can be moved to other code, a commit cannot. */"""
        found, offenders = 0, []
        for workflow in sorted(WORKFLOWS.glob("*.yml")) + sorted(WORKFLOWS.glob("*.yaml")):
            for number, line in enumerate(workflow.read_text(encoding="utf-8").splitlines(), start=1):
                match = USES.match(line)
                if match is None or match["target"].startswith("./"):
                    continue
                found += 1
                if not PINNED.match(match["target"]) or not TAG.match(match["comment"] or ""):
                    offenders.append(f"{workflow.name}:{number}: {line.strip()}")
        self.assertGreater(found, 0, "no uses: line was read")
        self.assertEqual([], offenders)


class ReleaseFilesTest(unittest.TestCase):
    def test_checksum_file_lists_every_file_of_the_folder(self) -> None:
        """/* REQ-REL-002: `tools/release_files.py <dist>` writes <dist>/SHA256SUMS with one line per file of the folder, the SBOM it writes included, SHA256SUMS itself and files whose name starts with a dot (the .gitignore `uv build` writes there) not: the SHA-256 in hexadecimal, two spaces, the file name, sorted by name, LF line ends (the form `shasum -a 256 -c` and `sha256sum -c` read); a second run writes the same bytes. */"""
        with tempfile.TemporaryDirectory() as tmp:
            dist, pyproject = stand_in(Path(tmp))
            done = run_tool(dist, pyproject)
            self.assertEqual(0, done.returncode, done.stdout + done.stderr)
            sums = dist / "SHA256SUMS"
            first = sums.read_bytes()
            self.assertNotIn(b"\r", first)
            self.assertTrue(first.endswith(b"\n"))
            lines = [SUM_LINE.match(line) for line in first.decode("ascii").splitlines()]
            self.assertTrue(all(lines), first)
            listed = {match[2]: match[1] for match in lines}
            names = [match[2] for match in lines]
            self.assertEqual(sorted([*STAND_IN_FILES, "demo-tool-1.2.3.cdx.json"]), names)
            for name in names:
                self.assertEqual(sha256((dist / name).read_bytes()), listed[name], name)
            self.assertEqual([str(dist / "demo-tool-1.2.3.cdx.json"), str(sums)], done.stdout.split("\n")[:2])
            again = run_tool(dist, pyproject)
            self.assertEqual(0, again.returncode, again.stdout + again.stderr)
            self.assertEqual(first, sums.read_bytes())

    def test_sbom_lists_the_package_its_files_and_its_pinned_dependencies(self) -> None:
        """/* REQ-REL-003: `tools/release_files.py <dist>` writes <dist>/<name>-<version>.cdx.json, a CycloneDX 1.5 JSON document: the package of pyproject.toml as metadata.component (name, version, purl `pkg:pypi/<name>@<version>`, its license expression) with one file component per built file of that distribution in the folder (name and SHA-256; a file of another distribution is not one of them); one library component per dependency with its name as written, its pinned version and its purl with the normalized name, an environment marker kept as a property, a dependency named only by an extra marked optional and one named twice listed once; the dependency graph from the package to each of them; no timestamp and a serial number derived from the content, so that a second run writes the same bytes. */"""
        with tempfile.TemporaryDirectory() as tmp:
            dist, pyproject = stand_in(Path(tmp))
            done = run_tool(dist, pyproject)
            self.assertEqual(0, done.returncode, done.stdout + done.stderr)
            path = dist / "demo-tool-1.2.3.cdx.json"
            first = path.read_bytes()
            first.decode("ascii")
            bom = json.loads(first)
            self.assertEqual(("CycloneDX", "1.5", 1), (bom["bomFormat"], bom["specVersion"], bom["version"]))
            self.assertRegex(bom["serialNumber"], r"^urn:uuid:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
            self.assertNotIn("timestamp", bom["metadata"])
            package = bom["metadata"]["component"]
            self.assertEqual(("application", "demo-tool", "1.2.3", "pkg:pypi/demo-tool@1.2.3", "pkg:pypi/demo-tool@1.2.3"),
                             (package["type"], package["name"], package["version"], package["purl"], package["bom-ref"]))
            self.assertEqual([{"expression": "MIT"}], package["licenses"])
            own = ["demo_tool-1.2.3-py3-none-any.whl", "demo_tool-1.2.3.tar.gz"]
            self.assertEqual(own, [item["name"] for item in package["components"]])
            for item in package["components"]:
                self.assertEqual("file", item["type"])
                self.assertEqual([{"alg": "SHA-256", "content": sha256(STAND_IN_FILES[item["name"]])}], item["hashes"])
            listed = {item["name"]: item for item in bom["components"]}
            self.assertEqual(["PyYAML", "demo-tool-http", "left_pad", "tree-sitter"], sorted(listed))
            self.assertEqual(len(listed), len(bom["components"]))
            wanted = {
                "tree-sitter": ("0.25.2", "pkg:pypi/tree-sitter@0.25.2", "required"),
                "PyYAML": ("6.0.3", "pkg:pypi/pyyaml@6.0.3", "required"),
                "left_pad": ("0.1.0", "pkg:pypi/left-pad@0.1.0", "required"),
                "demo-tool-http": ("1.2.3", "pkg:pypi/demo-tool-http@1.2.3", "optional"),
            }
            for name, (version, purl, scope) in wanted.items():
                item = listed[name]
                self.assertEqual(("library", version, purl, purl, scope),
                                 (item["type"], item["version"], item["purl"], item["bom-ref"], item["scope"]), name)
            self.assertEqual([{"name": "python:environment-marker", "value": 'sys_platform != "win32"'}],
                             listed["left_pad"]["properties"])
            self.assertNotIn("properties", listed["PyYAML"])
            graph = {entry["ref"]: entry.get("dependsOn", []) for entry in bom["dependencies"]}
            self.assertEqual(sorted(purl for _, purl, _ in wanted.values()), graph["pkg:pypi/demo-tool@1.2.3"])
            self.assertEqual(sorted([package["purl"], *(purl for _, purl, _ in wanted.values())]), sorted(graph))
            again = run_tool(dist, pyproject)
            self.assertEqual(0, again.returncode, again.stdout + again.stderr)
            self.assertEqual(first, path.read_bytes())

    def test_a_dependency_without_an_exact_version_or_a_folder_without_the_package_is_refused(self) -> None:
        """/* REQ-REL-004: `tools/release_files.py` exits 1, names the cause on standard error and writes neither file when a dependency of pyproject.toml is not pinned with `==` (an SBOM names versions, not ranges), when the folder holds no built file of the package at its version, and when the folder does not exist. */"""
        with tempfile.TemporaryDirectory() as tmp:
            loose = Path(tmp) / "loose"
            loose.mkdir()
            dist, pyproject = stand_in(loose, STAND_IN_PROJECT.replace("tree-sitter==0.25.2", "tree-sitter>=0.25"))
            done = run_tool(dist, pyproject)
            self.assertEqual(1, done.returncode, done.stdout + done.stderr)
            self.assertIn("tree-sitter>=0.25", done.stderr)
            self.assertEqual(sorted([".gitignore", *STAND_IN_FILES]), sorted(p.name for p in dist.iterdir()))

            other = Path(tmp) / "other"
            other.mkdir()
            dist, pyproject = stand_in(other, STAND_IN_PROJECT.replace('version = "1.2.3"', 'version = "1.2.4"'))
            done = run_tool(dist, pyproject)
            self.assertEqual(1, done.returncode, done.stdout + done.stderr)
            self.assertIn("demo_tool-1.2.4", done.stderr)
            self.assertEqual(sorted([".gitignore", *STAND_IN_FILES]), sorted(p.name for p in dist.iterdir()))

            done = run_tool(Path(tmp) / "missing", pyproject)
            self.assertEqual(1, done.returncode, done.stdout + done.stderr)
            self.assertIn("missing", done.stderr)
            self.assertFalse((Path(tmp) / "missing").exists())

    def test_the_sbom_of_this_repository_names_every_dependency_of_the_package(self) -> None:
        """/* REQ-REL-005: for the pyproject.toml of this repository the SBOM names the package at its version and exactly the distributions that `dependencies` and the extras name, each once with the version pinned there; the tool imports the standard library only. */"""
        tool = load_tool()
        project = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["project"]
        bom = tool.sbom(project, [])
        self.assertEqual((project["name"], project["version"]),
                         (bom["metadata"]["component"]["name"], bom["metadata"]["component"]["version"]))
        named = {}
        for requirement in [*project["dependencies"], *(r for extra in project["optional-dependencies"].values() for r in extra)]:
            name, _, rest = requirement.partition("==")
            named[name.strip()] = re.split(r"[;\s]", rest.strip(), maxsplit=1)[0]
        self.assertEqual(named, {item["name"]: item["version"] for item in bom["components"]})
        self.assertEqual(len(named), len(bom["components"]))
        required = {r.partition("==")[0].strip() for r in project["dependencies"]}
        self.assertEqual(required, {item["name"] for item in bom["components"] if item["scope"] == "required"})
        imported = set(re.findall(r"^(?:import|from)\s+([\w.]+)", TOOL.read_text(encoding="utf-8"), re.M))
        self.assertLessEqual({name.split(".")[0] for name in imported} - {"__future__"}, set(sys.stdlib_module_names))


@unittest.skipUnless(shutil.which("uv"), "uv is not on PATH: the build of the release cannot be run")
class ReproducibleBuildTest(unittest.TestCase):
    def test_two_builds_of_the_same_tree_give_the_same_bytes(self) -> None:
        """/* REQ-REL-006: `uv build`, the build command of a release, run twice on the same tree into two folders with the same SOURCE_DATE_EPOCH writes a source archive and a wheel that are byte for byte the same in both folders, and leaves no file behind in the tree; skipped, and reported as skipped, when uv is not on PATH. */"""
        tree = sorted(p.relative_to(REPO).as_posix() for p in REPO.iterdir())
        env = {**os.environ, "SOURCE_DATE_EPOCH": "1767225600"}   # 2026-01-01T00:00:00Z
        with tempfile.TemporaryDirectory() as tmp:
            built = []
            for name in ("first", "second"):
                out = Path(tmp) / name
                done = subprocess.run(["uv", "build", "--out-dir", str(out), str(REPO)],
                                      capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
                self.assertEqual(0, done.returncode, done.stdout + done.stderr)
                built.append({p.name: p.read_bytes() for p in out.iterdir() if p.suffix in (".gz", ".whl")})
        version = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
        self.assertEqual([f"wingbox-{version}-py3-none-any.whl", f"wingbox-{version}.tar.gz"], sorted(built[0]))
        self.assertEqual(sorted(built[0]), sorted(built[1]))
        for name in built[0]:
            self.assertEqual(sha256(built[0][name]), sha256(built[1][name]), name)
        self.assertEqual(tree, sorted(p.relative_to(REPO).as_posix() for p in REPO.iterdir()))


if __name__ == "__main__":
    unittest.main()
