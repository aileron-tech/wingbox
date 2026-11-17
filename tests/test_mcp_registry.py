import json
import re
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class McpRegistryTest(unittest.TestCase):
    def test_server_json_matches_the_package(self) -> None:
        """/* REQ-MCP-013: server.json, the entry of the MCP Registry, names the PyPI package at the version of pyproject.toml, starts the server with `wingbox mcp` over stdio, and keeps the registry's limits (name pattern, description of at most 100 characters). */"""
        server = json.loads((ROOT / "server.json").read_text(encoding="utf-8"))
        version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
        self.assertEqual("io.github.aileron-tech/wingbox", server["name"])
        self.assertRegex(server["name"], r"^[a-zA-Z0-9.-]+/[a-zA-Z0-9._-]+$")
        self.assertLessEqual(len(server["description"]), 100)
        self.assertEqual(version, server["version"])
        (package,) = server["packages"]
        self.assertEqual(("pypi", "wingbox", version), (package["registryType"], package["identifier"], package["version"]))
        self.assertEqual({"type": "stdio"}, package["transport"])
        self.assertEqual([{"type": "positional", "value": "mcp"}], package["packageArguments"])

    def test_readme_carries_the_ownership_line(self) -> None:
        """/* REQ-MCP-014: the README, which PyPI shows as the package description, carries `mcp-name: <server name>` followed by a boundary, so the MCP Registry can verify who owns the package. */"""
        server = json.loads((ROOT / "server.json").read_text(encoding="utf-8"))
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertRegex(readme, r"mcp-name: " + re.escape(server["name"]) + r"(\s|-->|<)")


if __name__ == "__main__":
    unittest.main()
