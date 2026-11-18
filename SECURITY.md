# Security policy

## Reporting a vulnerability

Report a vulnerability privately through GitHub: open the Security tab of
https://github.com/aileron-tech/wingbox and select "Report a vulnerability"
(https://github.com/aileron-tech/wingbox/security/advisories/new). Please do
not open a public issue for a security problem.

Include the Wingbox version (`wingbox --version`), the client (Claude Code,
Codex, another MCP client or the command line), the steps to reproduce and
what an attacker gains.

We aim to acknowledge a report within 7 days and to agree with you on a fix
and a disclosure date. Wingbox is maintained by a small team, so a fix may
take longer than the acknowledgement; we keep you informed in the advisory.

## Supported versions

Only the latest release on PyPI and its plugin in this repository receive
security fixes. Upgrade with `wingbox update` or `pipx upgrade wingbox`.

## Scope

In scope:

- the `wingbox` command and Python package (`src/`, `scripts/`);
- the MCP server `wingbox mcp` and the hooks (`wingbox hook pre-search`,
  `dirty`, `session-start`, `git`);
- the plugins in `plugins/wingbox` (manifests, skill) and
  `wingbox install` / `uninstall` / `update`;
- `wingbox add <language>`, the one command that downloads a parser: it may
  fetch only a wheel listed in the package (`wingbox/assets/parsers.json`)
  from `https://files.pythonhosted.org`, must refuse a file whose sha256
  differs from the pinned one, and unpacks only inside `~/.wingbox/parsers`
  (or `WINGBOX_PARSERS_DIR`). A download started by any other command, a
  build, a refresh, a hook or the MCP server included, is a vulnerability;
- any write outside the two places Wingbox writes to: the repository's
  `.wingbox/` folder and the user's own settings in the home folder (the
  assistants' files and `~/.wingbox/`). A test runs every command in a
  temporary repository with a temporary home folder and fails on any other
  changed path (`tests/test_write_zones.py`).

Out of scope:

- vulnerabilities in Claude Code, Codex, other MCP clients, uv, pipx or
  tree-sitter themselves (report them to their maintainers);
- findings that need an attacker who already controls your machine or your
  repository's files, beyond what Wingbox adds to that access;
- the website wingbox.dev and the example reports.
