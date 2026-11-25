"""Release integrity: workflow actions pinned to commits, the checksum file and the SBOM of a release, a reproducible build."""
import collections
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

import yaml

REPO = Path(__file__).resolve().parents[1]
WORKFLOWS = REPO / ".github" / "workflows"
TOOL = REPO / "tools" / "release_files.py"
# A pinned action as it must be written: on a line of its own, at a full commit, the release tag alone as the comment.
PINNED_LINE = re.compile(r"^\s*(?:-\s+)?uses:\s+(?P<target>[\w.-]+/[\w./-]+@[0-9a-f]{40})\s+#\s(?P<tag>v\d+\.\d+\.\d+)\s*$")
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


def workflow_files() -> list[Path]:
    return sorted(WORKFLOWS.glob("*.yml")) + sorted(WORKFLOWS.glob("*.yaml"))


def _uses(node):
    """Every value of a `uses` key of a workflow read as YAML, however the key is written (block, flow, quoted)."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "uses":
                yield str(value)
            else:
                yield from _uses(value)
    elif isinstance(node, list):
        for item in node:
            yield from _uses(item)


def unpinned_uses(text: str) -> tuple[int, list[str]]:
    """(how many actions a workflow names, those not written as `uses: owner/name@<40 hex>  # vX.Y.Z`).

    The actions are read from the parsed YAML, so no way of writing a `uses` is skipped; each of them must
    then be found on a line of the pinned form, which is where the release tag (a comment) can be read."""
    written = collections.Counter(match["target"] for match in map(PINNED_LINE.match, text.splitlines()) if match)
    named = [target for target in _uses(yaml.safe_load(text)) if not target.startswith("./")]
    offenders = []
    for target in named:
        if written[target] > 0:
            written[target] -= 1
        else:
            offenders.append(target)
    return len(named), offenders


def _steps(workflow: dict):
    """(job name, the shell GitHub runs the step with or None for its default, the step) of every step."""
    shared = ((workflow.get("defaults") or {}).get("run") or {}).get("shell")
    for name, job in workflow["jobs"].items():
        default = ((job.get("defaults") or {}).get("run") or {}).get("shell", shared)
        for step in job.get("steps", []):
            yield name, step.get("shell", default), step


_PIPE = re.compile(r"(?<![|>])\|(?![|])")
_PIPEFAIL = re.compile(r"^set -[eux]*o pipefail$")


def unguarded_pipes(workflow: dict) -> list[str]:
    """The `run` steps that pipe one command into another and pass when the first one fails.

    Without `shell:` GitHub runs a step as `bash -e`: the status of a pipeline is that of its last command.
    `shell: bash` adds `-o pipefail`; any other step has to begin with `set -o pipefail` itself."""
    offenders = []
    for job, shell, step in _steps(workflow):
        script = step.get("run")
        if script is None or shell not in (None, "bash", "sh"):
            continue
        if _PIPE.search(re.sub(r"'[^']*'", "''", script)) is None or shell == "bash":
            continue
        lines = [line.strip() for line in script.splitlines() if line.strip() and not line.strip().startswith("#")]
        if not _PIPEFAIL.match(lines[0]):
            offenders.append(f"{job}: {step.get('name', lines[0])}")
    return offenders


def _secret(values) -> bool:
    return any("secrets." in str(value) for value in (values or {}).values())


def run_step(script: str, folder: Path, stubs: dict[str, str], env: dict[str, str]) -> subprocess.CompletedProcess:
    """Run the script of a step the way GitHub does without `shell:` (bash -e), with stand-ins for some commands."""
    tools = folder / "stand-ins"
    tools.mkdir(exist_ok=True)
    for name, body in stubs.items():
        (tools / name).write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8")
        (tools / name).chmod(0o755)
    path = folder / "step.sh"
    path.write_text(script, encoding="utf-8")
    environment = {**os.environ, **env, "PATH": str(tools) + os.pathsep + os.environ.get("PATH", "")}
    return subprocess.run(["bash", "--noprofile", "--norc", "-e", str(path)], cwd=str(folder / "work"),
                          capture_output=True, text=True, env=environment)


class WorkflowPinTest(unittest.TestCase):
    def test_every_action_names_a_full_commit(self) -> None:
        """/* REQ-REL-001: every `uses:` of .github/workflows/*.yml that is not a local `./` action names its action at a full commit (40 hexadecimal characters after the @) and carries the release tag of that commit as a trailing comment (`# vX.Y.Z`): a tag can be moved to other code, a commit cannot. */"""
        found, offenders = 0, []
        for workflow in workflow_files():
            count, unpinned = unpinned_uses(workflow.read_text(encoding="utf-8"))
            found += count
            offenders += [f"{workflow.name}: {target}" for target in unpinned]
        self.assertGreater(found, 0, "no uses: was read")
        self.assertEqual([], offenders)

    def test_no_way_of_writing_an_action_escapes_the_pin_check(self) -> None:
        """/* REQ-REL-9900: the check of REQ-REL-001 reads a workflow as YAML, so it also names an action at a tag that is written with a comment of several words, in flow style (`{uses: ...}`) or under a quoted key, and an action at a commit whose comment is not the release tag alone; an action at a commit with its tag and a local `./` action pass. */"""
        commit = "0123456789abcdef0123456789abcdef01234567"
        good = f"jobs:\n  a:\n    steps:\n      - uses: owner/name@{commit}  # v1.2.3\n      - uses: ./local/action\n"
        self.assertEqual((1, []), unpinned_uses(good))
        for step, target in (
            ("- uses: owner/name@v4  # moved by its owner", "owner/name@v4"),
            ("- {uses: owner/name@v4}", "owner/name@v4"),
            ('- "uses": owner/name@v4', "owner/name@v4"),
            ("- uses: owner/name@v4", "owner/name@v4"),
            (f"- uses: owner/name@{commit}", f"owner/name@{commit}"),
            (f"- uses: owner/name@{commit}  # v1.2.3 and more", f"owner/name@{commit}"),
            (f"- uses: owner/name@{commit[:39]}  # v1.2.3", f"owner/name@{commit[:39]}"),
        ):
            with self.subTest(step=step):
                self.assertEqual((2, [target]), unpinned_uses(good + "      " + step + "\n"))


class WorkflowSafetyTest(unittest.TestCase):
    def workflow(self, name: str) -> dict:
        return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))

    def step(self, workflow: dict, job: str, name: str) -> dict:
        return next(step for step in workflow["jobs"][job]["steps"] if step.get("name", "").startswith(name))

    def test_a_pipeline_fails_when_its_first_command_fails(self) -> None:
        """/* REQ-REL-9901: every `run` step of a workflow that pipes one command into another (`docker run ... | tee`, `wingbox build ... | tail`) fails when the first command fails: it runs under `shell: bash`, which sets pipefail, or its first command is `set -o pipefail` (`set -euo pipefail`); without it GitHub's default `bash -e` takes the status of the last command. */"""
        offenders = []
        for workflow in workflow_files():
            offenders += [f"{workflow.name}: {step}" for step in unguarded_pipes(yaml.safe_load(workflow.read_text(encoding="utf-8")))]
        self.assertEqual([], offenders)
        plain = {"jobs": {"a": {"steps": [{"name": "run", "run": "make | tee log"}]}}}
        self.assertEqual(["a: run"], unguarded_pipes(plain))
        plain["jobs"]["a"]["steps"][0]["run"] = "set -euo pipefail\nmake | tee log"
        self.assertEqual([], unguarded_pipes(plain))
        plain["jobs"]["a"]["steps"][0] = {"name": "run", "shell": "bash", "run": "make | tee log"}
        self.assertEqual([], unguarded_pipes(plain))
        plain["jobs"]["a"]["steps"][0] = {"name": "run", "run": "grep -E 'a|b' log || true"}
        self.assertEqual([], unguarded_pipes(plain))

    def test_the_map_request_gives_the_token_only_to_the_steps_that_call_gh(self) -> None:
        """/* REQ-REL-9902: in map-request.yml, which builds a repository named in an issue anyone can open, no secret is in the environment of the workflow or of the job: a step holds the token exactly when its script calls `gh`, so the steps that clone, install, build and collect run without it. */"""
        workflow = self.workflow("map-request.yml")
        job = workflow["jobs"]["map"]
        self.assertFalse(_secret(workflow.get("env")))
        self.assertFalse(_secret(job.get("env")))
        with_token = []
        for step in job["steps"]:
            calls = re.search(r"\bgh (?:api|issue)\b", step.get("run", "")) is not None
            self.assertEqual(calls, _secret(step.get("env")) or _secret(step.get("with")), step.get("name", step.get("uses")))
            with_token += [step["name"]] if calls else []
        self.assertEqual(["Read and check the requested repository", "Tell the issue"], with_token)

    def test_the_map_request_removes_a_committed_wingbox_folder_before_the_build(self) -> None:
        """/* REQ-REL-9903: map-request.yml removes `target/.wingbox` right after the clone and before Wingbox is installed or run, so a report, a map or a configuration that the requested repository commits is never taken for what the build wrote. */"""
        steps = self.workflow("map-request.yml")["jobs"]["map"]["steps"]
        scripts = [step.get("run", "") for step in steps]
        clone = next(index for index, script in enumerate(scripts) if "git clone" in script)
        build = next(index for index, script in enumerate(scripts) if "wingbox build" in script)
        lines = [line.strip() for line in scripts[clone].splitlines()]
        self.assertIn("rm -rf target/.wingbox", lines)
        self.assertGreater(lines.index("rm -rf target/.wingbox"), next(i for i, line in enumerate(lines) if line.startswith("git clone")))
        self.assertLess(clone, build)
        self.assertFalse(any("target" in script for script in scripts[:clone]))

    @unittest.skipIf(os.name == "nt" or shutil.which("bash") is None, "the steps are bash scripts of a Linux runner")
    def test_a_failed_build_of_the_map_request_fails_the_step(self) -> None:
        """/* REQ-REL-9904: the build step of map-request.yml, run as GitHub runs it, exits non-zero when `wingbox build` fails or is stopped by its time limit, although its output goes through `tail`; it exits 0 when the build succeeds. */"""
        workflow = self.workflow("map-request.yml")
        step = self.step(workflow, "map", "Build the graph")
        for status, passes in ((0, True), (3, False), (124, False)):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as tmp:
                folder = Path(tmp)
                (folder / "work").mkdir()
                done = run_step(step["run"], folder, {"timeout": 'shift; exec "$@"', "wingbox": f"echo build; exit {status}"}, {})
                self.assertEqual(passes, done.returncode == 0, done.stdout + done.stderr)

    @unittest.skipIf(os.name == "nt" or shutil.which("bash") is None, "the steps are bash scripts of a Linux runner")
    def test_the_map_request_uploads_only_regular_files_the_build_wrote(self) -> None:
        """/* REQ-REL-9905: the collect step of map-request.yml copies REPORT.md and map.html into the uploaded folder only when each is a regular file in a real `target/.wingbox` folder: when one of them, or the folder, is a symbolic link (to a file of the runner) or is missing, the step fails and the uploaded folder holds no content of the linked file. */"""
        step = self.step(self.workflow("map-request.yml"), "map", "Collect")

        def collect(prepare) -> tuple[subprocess.CompletedProcess, Path]:
            folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
            work = folder / "work"
            (work / "target" / ".wingbox").mkdir(parents=True)
            (work / "commit.txt").write_text("0123abc\n", encoding="utf-8")
            (work / "version.txt").write_text("wingbox 0.0.0\n", encoding="utf-8")
            (folder / "secret.txt").write_text("a file of the runner\n", encoding="utf-8")
            (work / "target" / ".wingbox" / "REPORT.md").write_text("# report\n", encoding="utf-8")
            (work / "target" / ".wingbox" / "map.html").write_text("<html></html>\n", encoding="utf-8")
            prepare(work / "target" / ".wingbox", folder / "secret.txt")
            return run_step(step["run"], folder, {}, {"NAME": "owner/name"}), work / "out"

        done, out = collect(lambda wingbox, secret: None)
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertEqual(["REPORT.md", "map.html", "source.txt"], sorted(path.name for path in out.iterdir()))
        self.assertEqual("# report\n", (out / "REPORT.md").read_text(encoding="utf-8"))
        self.assertIn("repository owner/name\ncommit 0123abc\nwingbox 0.0.0\n", (out / "source.txt").read_text(encoding="utf-8"))

        def link(name):
            def prepare(wingbox: Path, secret: Path) -> None:
                (wingbox / name).unlink()
                (wingbox / name).symlink_to(secret)
            return prepare

        def linked_folder(wingbox: Path, secret: Path) -> None:
            real = wingbox.with_name("elsewhere")
            wingbox.rename(real)
            wingbox.symlink_to(real, target_is_directory=True)

        def missing(wingbox: Path, secret: Path) -> None:
            (wingbox / "map.html").unlink()

        for prepare in (link("REPORT.md"), link("map.html"), linked_folder, missing):
            done, out = collect(prepare)
            self.assertNotEqual(0, done.returncode, done.stdout + done.stderr)
            copied = [path for path in out.rglob("*") if path.is_file()] if out.exists() else []
            self.assertFalse(any(path.is_symlink() or "runner" in path.read_text(encoding="utf-8") for path in copied), copied)

    def test_the_registry_job_outlives_its_own_wait(self) -> None:
        """/* REQ-REL-9906: the job of publish-mcp.yml that waits for the version on PyPI has a time limit longer than the longest its wait loop can take (every attempt: the time limit of its request plus its pause), so a version that never appears ends with the error of the loop and not with a cancelled job; the request of every attempt has a time limit. */"""
        workflow = self.workflow("publish-mcp.yml")
        job = workflow["jobs"]["publish"]
        script = self.step(workflow, "publish", "Wait until")["run"]
        attempts = int(re.search(r"seq 1 (\d+)", script)[1])
        pause = int(re.search(r"sleep (\d+)", script)[1])
        request = re.search(r"curl [^|\n]*--max-time (\d+)", script)
        self.assertIsNotNone(request, "the request of an attempt has no time limit")
        longest = attempts * (pause + int(request[1]))
        self.assertGreater(job["timeout-minutes"] * 60, longest + 120)

    def test_the_push_triggers_name_no_branch_but_main(self) -> None:
        """/* REQ-REL-9907: tests.yml and docs.yml run on a push to `main` only (a pull request runs them for every other branch): their lists of branches name no feature branch, which would stay there after the branch is gone. */"""
        for name in ("tests.yml", "docs.yml"):
            workflow = self.workflow(name)
            triggers = workflow.get("on", workflow.get(True))   # YAML 1.1 reads the key `on` as true
            self.assertEqual(["main"], triggers["push"]["branches"], name)
            self.assertIn("pull_request", triggers, name)

    def test_a_change_of_documents_runs_every_test_that_reads_a_document(self) -> None:
        """/* REQ-REL-9908: docs.yml, the only workflow a change of Markdown files starts, runs the test modules that compare a document with the code or with result files: tests.test_english_only, test_readme_numbers, test_plugins, test_mcpb, test_assurance_docs, test_output_formats_cli (SKILL.md, REQ-INSTALL-045), test_json_errors (references/code-mode.md, REQ-INSTALL-046), test_mcp_registry (README, REQ-MCP-014), test_benchmark_report and test_benchmark_cpp_collisions (the benchmark READMEs, REQ-BENCH-001, -008, -009, -011, -024); every module it names exists. */"""
        script = self.step(self.workflow("docs.yml"), "docs", "Documentation tests")["run"]
        named = re.findall(r"\btests\.(test_\w+)", script)
        for module in named:
            self.assertTrue((REPO / "tests" / f"{module}.py").is_file(), module)
        wanted = {"test_english_only", "test_readme_numbers", "test_plugins", "test_mcpb", "test_assurance_docs",
                  "test_output_formats_cli", "test_json_errors", "test_mcp_registry", "test_benchmark_report",
                  "test_benchmark_cpp_collisions"}
        self.assertLessEqual(wanted, set(named))


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
