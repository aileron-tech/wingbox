# Release integrity: what a reader can check about a release

This page says, for a Wingbox release, what ties the files a user installs to the source of the tag, how to check each tie, and what is not tied yet. The steps a maintainer runs are in [AGENTS.md](../../AGENTS.md), section 3 (step 7) and section 6.1.

## 1. What exists

| Item | What it shows | How to check | Kept true by |
|---|---|---|---|
| Exact pins of the dependencies | A given version of Wingbox installs the same version of every parser and of PyYAML each time | `pyproject.toml`, `[project] dependencies`: every line is `name==version` | `tests/test_language_parsers.py` (REQ-DEPS-012) |
| Workflow actions at a commit | A workflow of this repository runs the code of an action that was read, not whatever a tag points to later | `grep -n "uses:" .github/workflows/*.yml`: 40 hexadecimal characters after every `@`, the release tag as a comment | `tests/test_release_integrity.py` (REQ-REL-001) |
| `SHA256SUMS` on the GitHub release | The file a user holds is, byte for byte, a file of the release | `gh release download vX.Y.Z --pattern SHA256SUMS`, then `shasum -a 256 -c SHA256SUMS` (or `sha256sum -c`) in the folder with the downloaded files; a file that is not there is reported as missing | `tools/release_files.py`, REQ-REL-002 |
| `wingbox-X.Y.Z.cdx.json` on the GitHub release | What the release is made of: the package, the SHA-256 of each of its built files, every dependency with its pinned version and its `pkg:pypi/...` identifier | Any CycloneDX 1.5 reader; `python3 -m json.tool wingbox-X.Y.Z.cdx.json` | `tools/release_files.py`, REQ-REL-003 to REQ-REL-005 |
| The build gives the same bytes | Two builds of one tree are the same files, so anyone can rebuild a tag and compare | Section 2 | `tests/test_release_integrity.py` (REQ-REL-006), on every push, on Linux, macOS and Windows |

The first release that carries `SHA256SUMS` and the SBOM is the one after 0.14.0.

The SBOM lists what `pyproject.toml` declares: the dependencies of `[project] dependencies` (scope `required`) and those that only an extra names (scope `optional`). Nothing is resolved from a package index, so a package that a dependency itself requires is not listed. The build backend (`hatchling`) is not part of the installed package and is not listed.

## 2. Rebuilding a release from its tag

```bash
git clone --depth 1 --branch vX.Y.Z https://github.com/aileron-tech/wingbox /tmp/wingbox-vX.Y.Z
uv build --out-dir /tmp/wingbox-rebuild /tmp/wingbox-vX.Y.Z
shasum -a 256 /tmp/wingbox-rebuild/wingbox-X.Y.Z*
curl -s https://pypi.org/pypi/wingbox/X.Y.Z/json | python3 -c "import json,sys; [print(u['digests']['sha256'], '', u['filename']) for u in json.load(sys.stdin)['urls']]"
```

The two digests of the rebuild are the two digests PyPI answers (and, from the release after 0.14.0 on, two lines of `SHA256SUMS`).

Two conditions decide the bytes:

- `SOURCE_DATE_EPOCH`. A release is built without it; the build backend then stamps every file with its own fixed date (2020-02-02). With the variable set the archives carry that date instead and the digests differ from the release, although the contents are the same.
- The version of the build backend. `pyproject.toml` asks for `hatchling` without a version, and the wheel records the one that built it (`unzip -p wingbox-X.Y.Z-py3-none-any.whl 'wingbox-X.Y.Z.dist-info/WHEEL'`, line `Generator`). To rebuild with that version: write `hatchling==<that version>` into a file and add `--build-constraints <file>` to `uv build`.

Checked for 0.14.0 on 2026-11-19, on macOS: a shallow clone of the tag, built as above with `hatchling==1.33.0` under Python 3.11, 3.12, 3.13 and 3.14, gave each time the two files PyPI serves:

```text
0bf88e2f2480bb158e5f24b948214fb3411271a378a8e05845ab286275c74107  wingbox-0.14.0-py3-none-any.whl
1080db7396af6250facc82cd756acb656d150f24843625b561ab55e4514b7b71  wingbox-0.14.0.tar.gz
```

Not assessed: whether a build on another operating system gives the same bytes as these (the test compares two builds on one machine, on each of the three systems), and the same check for `wingbox-http`. When two builds differ, compare what is inside (`unzip` and `tar -xzf` into two folders, then `diff -r`): the contents are what is installed.

## 3. What these files do not show

- Who built the files. `SHA256SUMS` and the SBOM are attached by the same person who uploads the release, with no signature: they show that a file was not changed after the release was made, not that the release was made from the tag by the project. The rebuild of section 2 is the check that does not rest on the maintainer's word.
- Provenance on PyPI. PyPI serves no attestation for the files of 0.14.0 (`https://pypi.org/integrity/wingbox/0.14.0/wingbox-0.14.0-py3-none-any.whl/provenance` answers 404). The files are uploaded from the maintainer's machine with a token scoped to the project (AGENTS.md, section 4). Section 4 below is the step that changes this.
- Anything about what the tool answers. For that, see the tests and the benchmarks of the repository.

## 4. Next step, not active: publishing to PyPI through trusted publishing

Status: proposal. Nothing in this section is in use; releases are published as AGENTS.md section 3 says. The workflow below is text in this page, not a file under `.github/workflows/`, and it has never run.

With trusted publishing PyPI accepts an upload from one named workflow of this repository, identified by the short-lived OpenID Connect token GitHub gives that run. No upload token is stored anywhere, and PyPI records for each file an attestation that names the repository, the workflow and the commit. The `/provenance` address above then answers 200.

What the maintainer does, once:

1. On PyPI, signed in as an owner of the project `wingbox`: "Manage" > "Publishing" (`https://pypi.org/manage/project/wingbox/settings/publishing/`) > "Add a new publisher" > "GitHub", with these values:

   | Field | Value |
   |---|---|
   | Owner | `aileron-tech` |
   | Repository name | `wingbox` |
   | Workflow name | `publish-pypi.yml` |
   | Environment name | `pypi` |

2. The same four values for the project `wingbox-http` (`https://pypi.org/manage/project/wingbox-http/settings/publishing/`).
3. On GitHub, in the repository settings: "Environments" > "New environment", name `pypi`. Add the maintainer under "Required reviewers": a version is uploaded to PyPI once and for good, and with a reviewer each upload waits for an approval.
4. Add the workflow below as `.github/workflows/publish-pypi.yml` through a pull request.
5. Publish the next release with it. Then check `https://pypi.org/integrity/wingbox/X.Y.Z/wingbox-X.Y.Z-py3-none-any.whl/provenance` (200, the attestation names `aileron-tech/wingbox` and `publish-pypi.yml`), rewrite steps 3, 5 and 7 of AGENTS.md section 3 and its section 4 for the workflow, and delete the token on PyPI and the keychain entry.

The workflow `publish-mcp` starts on the same event and waits ten minutes for the version to appear on PyPI. An approval that takes longer makes it fail; start it again by hand (`gh workflow run publish-mcp.yml`) after the upload.

```yaml
# Proposed, not active (docs/assurance/release-integrity.md, section 4).
name: publish-pypi

on:
  release:
    types: [published]

permissions:
  contents: read

jobs:
  build:
    runs-on: ubuntu-latest
    timeout-minutes: 15
    steps:
      - uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262  # v4.4.0
      - name: Install uv
        run: pipx install uv
      - name: The tag is the version of pyproject.toml
        run: |
          version="$(python3 -c "import tomllib; print(tomllib.load(open('pyproject.toml', 'rb'))['project']['version'])")"
          test "v${version}" = "${GITHUB_REF_NAME}"
      - name: Build both distributions
        run: |
          uv build --out-dir dist/wingbox
          uv build packages/wingbox-http --out-dir dist/wingbox-http
      - uses: actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02  # v4.6.2
        with:
          name: dist
          path: dist/

  wingbox:
    needs: build
    runs-on: ubuntu-latest
    timeout-minutes: 15
    environment: pypi
    permissions:
      id-token: write   # the token PyPI exchanges for the right to upload; no stored secret
    steps:
      - uses: actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093  # v4.3.0
        with:
          name: dist
          path: dist/
      - uses: pypa/gh-action-pypi-publish@7245906fb61fa33a13554f9d5fb856fe61758086  # v1.14.3
        with:
          packages-dir: dist/wingbox/

  wingbox-http:
    needs: wingbox   # it requires exactly this version of wingbox
    runs-on: ubuntu-latest
    timeout-minutes: 15
    environment: pypi
    permissions:
      id-token: write
    steps:
      - uses: actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093  # v4.3.0
        with:
          name: dist
          path: dist/
      - uses: pypa/gh-action-pypi-publish@7245906fb61fa33a13554f9d5fb856fe61758086  # v1.14.3
        with:
          packages-dir: dist/wingbox-http/

  release-files:
    needs: wingbox-http
    runs-on: ubuntu-latest
    timeout-minutes: 10
    permissions:
      contents: write   # attaches SHA256SUMS and the SBOM to the release
    steps:
      - uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262  # v4.4.0
      - uses: actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093  # v4.3.0
        with:
          name: dist
          path: dist/
      - name: Checksums and SBOM
        env:
          GH_TOKEN: ${{ github.token }}
        run: |
          mkdir release
          cp dist/wingbox/* dist/wingbox-http/* release/
          python3 tools/release_files.py release
          gh release upload "${GITHUB_REF_NAME}" release/SHA256SUMS release/wingbox-*.cdx.json
```

The commits of the four actions were read from their release tags on 2026-11-19 (`gh api repos/<owner>/<repo>/git/ref/tags/<tag>`): `actions/checkout` v4.4.0, `actions/upload-artifact` v4.6.2, `actions/download-artifact` v4.3.0, `pypa/gh-action-pypi-publish` v1.14.3. Read them again when the file is added.
