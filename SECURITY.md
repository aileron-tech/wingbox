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
- the MCP server `wingbox mcp` and the search hook `wingbox hook pre-search`;
- the plugins in `plugins/wingbox` (manifests, launcher, skill) and
  `wingbox install` / `uninstall` / `update`.

Out of scope:

- vulnerabilities in Claude Code, Codex, other MCP clients, uv, pipx or
  tree-sitter themselves (report them to their maintainers);
- findings that need an attacker who already controls your machine or your
  repository's files, beyond what Wingbox adds to that access;
- the website wingbox.dev and the example reports.
