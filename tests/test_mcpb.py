"""The Claude Desktop extension built by tools/build_mcpb.py (an MCP Bundle around `wingbox mcp`)."""
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import tomllib
import unittest
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCHEMA = REPO / "tools" / "mcpb" / "mcpb-manifest-v0.4.schema.json"
_spec = importlib.util.spec_from_file_location("build_mcpb", REPO / "tools" / "build_mcpb.py")
build_mcpb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build_mcpb)
VERSION = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
URI = re.compile(r"^[a-z][a-z0-9+.-]*://\S+$")
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PRIVATE = re.compile(r"/Users/|/home/|C:\\\\Users|scratchpad|/private/tmp", re.I)


def schema_errors(value, schema, root, where="$") -> list[str]:
    """Errors of value against a JSON Schema draft-07 subset: the keywords the MCPB schema uses."""
    if "$ref" in schema:
        target = root
        for part in schema["$ref"].lstrip("#/").split("/"):
            target = target[part]
        return schema_errors(value, target, root, where)
    errors: list[str] = []
    kinds = {"object": dict, "array": list, "string": str, "boolean": bool, "number": (int, float), "integer": int}
    wanted = schema.get("type")
    if wanted and not any(isinstance(value, kinds[k]) and not (k in ("number", "integer") and isinstance(value, bool))
                          for k in ([wanted] if isinstance(wanted, str) else wanted)):
        return [f"{where}: expected {wanted}"]
    if "const" in schema and value != schema["const"]:
        errors.append(f"{where}: must be {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{where}: {value!r} not in {schema['enum']}")
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            errors.append(f"{where}: too short")
        if "pattern" in schema and not re.search(schema["pattern"], value):
            errors.append(f"{where}: does not match {schema['pattern']}")
        if schema.get("format") == "uri" and not URI.match(value):
            errors.append(f"{where}: not a URI")
        if schema.get("format") == "email" and not EMAIL.match(value):
            errors.append(f"{where}: not an email")
    if "anyOf" in schema and all(schema_errors(value, option, root, where) for option in schema["anyOf"]):
        errors.append(f"{where}: matches none of anyOf")
    if isinstance(value, list) and "items" in schema:
        for index, item in enumerate(value):
            errors += schema_errors(item, schema["items"], root, f"{where}[{index}]")
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        errors += [f"{where}: missing {name}" for name in schema.get("required", []) if name not in value]
        extra = schema.get("additionalProperties", True)
        for name, item in value.items():
            if name in properties:
                errors += schema_errors(item, properties[name], root, f"{where}.{name}")
            elif extra is False:
                errors.append(f"{where}: unexpected {name}")
            elif isinstance(extra, dict):
                errors += schema_errors(item, extra, root, f"{where}.{name}")
    return errors


def server_tools() -> list[dict[str, str]]:
    """tools/list of `wingbox mcp`, asked over stdio."""
    lines = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "0"}}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    ]
    env = dict(os.environ, PYTHONPATH=str(REPO / "src") + os.pathsep + os.environ.get("PYTHONPATH", ""))
    with tempfile.TemporaryDirectory() as tmp:
        done = subprocess.run([sys.executable, "-m", "wingbox", "mcp", "--root", tmp], capture_output=True, env=env, timeout=120,
                              input="".join(json.dumps(line) + "\n" for line in lines).encode())
    replies = [json.loads(line) for line in done.stdout.decode().splitlines() if line.strip()]
    tools = next(reply for reply in replies if reply.get("id") == 2)["result"]["tools"]
    return [{"name": tool["name"], "description": tool["description"]} for tool in tools]


class BundleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.files = build_mcpb.bundle_files()
        cls.manifest = json.loads(cls.files["manifest.json"])

    def test_manifest_validates_against_the_spec_schema(self) -> None:
        """/* REQ-MCPB-001: the manifest validates against the MCPB manifest schema v0.4 (vendored in tools/mcpb with its source URL and date), uses the `uv` server type with src/server.py, and names Wingbox's author, repository, homepage https://wingbox.dev, license MIT, the plugin icon (512x512 PNG), macOS, Windows and Linux, and a required directory setting passed to the server as --root with --build-missing. */"""
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        self.assertEqual([], schema_errors(self.manifest, schema, schema))
        self.assertIn("modelcontextprotocol/mcpb", (SCHEMA.parent / "SOURCE.txt").read_text(encoding="utf-8"))
        manifest = self.manifest
        self.assertEqual(("wingbox", "Wingbox", "MIT", "https://wingbox.dev"),
                         (manifest["name"], manifest["display_name"], manifest["license"], manifest["homepage"]))
        self.assertEqual("https://github.com/aileron-tech/wingbox", manifest["repository"]["url"])
        self.assertTrue(manifest["long_description"].startswith(build_mcpb.SLOGAN))
        self.assertEqual(self.files[manifest["icon"]], (REPO / "plugins" / "wingbox" / "assets" / "icon.png").read_bytes())
        self.assertEqual(["darwin", "win32", "linux"], manifest["compatibility"]["platforms"])
        server = manifest["server"]
        self.assertEqual(("uv", "src/server.py"), (server["type"], server["entry_point"]))
        self.assertIn(server["entry_point"], self.files)
        args = server["mcp_config"]["args"]
        self.assertEqual(["--root", "${user_config.repository}", "--build-missing"], args[-3:])
        setting = manifest["user_config"]["repository"]
        self.assertEqual(("directory", True), (setting["type"], setting["required"]))

    def test_tools_are_the_servers_tools(self) -> None:
        """/* REQ-MCPB-002: the tools of the manifest are exactly the name and description of every tool `wingbox mcp` lists over stdio, in its order. */"""
        self.assertEqual(server_tools(), self.manifest["tools"])

    def test_version_is_the_projects(self) -> None:
        """/* REQ-MCPB-003: the manifest version and the bundle's pyproject.toml carry the version of pyproject.toml, and the bundle installs exactly that release of wingbox from PyPI. */"""
        self.assertEqual(VERSION, self.manifest["version"])
        bundle_project = tomllib.loads(self.files["pyproject.toml"].decode())["project"]
        self.assertEqual(VERSION, bundle_project["version"])
        self.assertEqual([f"wingbox=={VERSION}"], bundle_project["dependencies"])

    def test_archive_is_deterministic_and_holds_only_the_bundle(self) -> None:
        """/* REQ-MCPB-004: tools/build_mcpb.py writes dist/wingbox-<version>.mcpb with exactly manifest.json, pyproject.toml, src/server.py, icon.png and LICENSE, no local path or private name in any text file, and the same bytes on every build. */"""
        with tempfile.TemporaryDirectory() as tmp:
            outputs = []
            for name in ("a", "b"):
                done = subprocess.run([sys.executable, str(REPO / "tools" / "build_mcpb.py"), "--out", str(Path(tmp) / name)],
                                      capture_output=True, text=True, check=True)
                outputs.append(Path(done.stdout.strip()))
            self.assertEqual(f"wingbox-{VERSION}.mcpb", outputs[0].name)
            self.assertEqual(outputs[0].read_bytes(), outputs[1].read_bytes())
            with zipfile.ZipFile(io.BytesIO(outputs[0].read_bytes())) as archive:
                names = archive.namelist()
                texts = {name: archive.read(name) for name in names if name != "icon.png"}
        self.assertEqual(sorted(["manifest.json", "pyproject.toml", "src/server.py", "icon.png", "LICENSE"]), sorted(names))
        for name, data in texts.items():
            self.assertIsNone(PRIVATE.search(data.decode("utf-8")), name)


if __name__ == "__main__":
    unittest.main()
