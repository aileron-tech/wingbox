#!/bin/sh
# The tests of benchmarks/impact/README.md that need no model, from nothing.
#
# Usage: benchmarks/impact/run.sh <work dir>
# Needs: git, uv (https://docs.astral.sh/uv/). Network: GitHub and PyPI. No model, no key.
# Everything is written under <work dir>. The same run in a clean container: Dockerfile.benchmark.
#
# WINGBOX_SPEC   the Wingbox under test (default: the release named below), e.g. a path to a checkout.
# RUN_ONLY=corpus  stop after the environments and the corpus are in place (used by tools/time_builds.py).
# VERIFY=0       do not compare the results with the committed ones (first run of a new version).
set -eu

HERE=$(cd "$(dirname "$0")" && pwd)
T=$HERE/tools
W=${1:?usage: run.sh <work dir>}
mkdir -p "$W"
W=$(cd "$W" && pwd)
WINGBOX_SPEC=${WINGBOX_SPEC:-wingbox==0.2.2}
GRIMP_SPEC=grimp==3.20
cd "$W"
mkdir -p out corpus/karpathy bench/plain bench/wb bench/log

step() { printf '\n== %s\n' "$1"; }

step "environments"
for venv in venv_wb venv_ref; do
  [ -d "$venv" ] || uv venv --quiet --python 3.12 "$venv"
done
uv pip install --quiet --python venv_wb/bin/python "$WINGBOX_SPEC"
uv pip install --quiet --python venv_ref/bin/python "$GRIMP_SPEC"
PY="$W/venv_ref/bin/python -I"
WINGBOX=$W/venv_wb/bin/wingbox
"$WINGBOX" --version

step "corpus at pinned commits"
fetch() { # <dir> <url> <commit> [<subtree for a sparse checkout>]
  if [ ! -d "$1/.git" ]; then
    mkdir -p "$1"
    git -C "$1" init --quiet
    git -C "$1" remote add origin "$2"
    if [ -n "${4:-}" ]; then
      git -C "$1" sparse-checkout set "$4"
      git -C "$1" fetch --quiet --depth 1 --filter=blob:none origin "$3"
    else
      git -C "$1" fetch --quiet --depth 1 origin "$3"
    fi
    git -C "$1" checkout --quiet FETCH_HEAD
  fi
  echo "$(basename "$1") $(git -C "$1" rev-parse HEAD)"
}
{
  fetch corpus/karpathy/nanoGPT https://github.com/karpathy/nanoGPT 3adf61e154c3fe3fca428ad6bc3818b27a3b8291
  fetch corpus/karpathy/minGPT https://github.com/karpathy/minGPT 37baab71b9abea1b76ab957409a1cc2fbfba8a26
  fetch corpus/karpathy/micrograd https://github.com/karpathy/micrograd 7bc720e951fe422b8f8814aa5aa1b64121d26b4c
  fetch corpus/fastapi https://github.com/fastapi/fastapi 2a1bd230b454a51644c7e2087f525b2dd245edbf
  fetch corpus/chrome-devtools-mcp https://github.com/ChromeDevTools/chrome-devtools-mcp 8ba2ac945ebaf1adb92208c29f3a30e73a920956
  fetch corpus/agent-browser https://github.com/vercel-labs/agent-browser 53384771fbafc261ba67608ac806dd5aa70b84f0
  fetch corpus/claudes-c-compiler https://github.com/anthropics/claudes-c-compiler 6f1b99acb2f4ec2414592136c2009fe7713deec3
  fetch corpus/immich https://github.com/immich-app/immich e5d6eb7be67834f59d90e4b7a86f5dec7ac9af86 server
} | tee out/commits.txt
if [ "${RUN_ONLY:-}" = corpus ]; then
  echo "RUN_ONLY=corpus: environments and corpus are ready in $W"
  exit 0
fi

step "one clean copy of each corpus: plain and for Wingbox"
export_tree() { # <git dir> <destination> [<prefix>/] [<tree-ish>]
  mkdir -p "$2"
  git -C "$1" archive --format=tar ${3:+--prefix="$3"} "${4:-HEAD}" | tar -x -C "$2"
}
for setup in plain wb; do
  if [ ! -d "bench/$setup/karpathy" ]; then
    for repo in nanoGPT minGPT micrograd; do export_tree "corpus/karpathy/$repo" "bench/$setup/karpathy" "$repo/"; done
  fi
  for repo in fastapi chrome-devtools-mcp agent-browser claudes-c-compiler; do
    [ -d "bench/$setup/$repo" ] || export_tree "corpus/$repo" "bench/$setup/$repo"
  done
  [ -d "bench/$setup/immich-server" ] || export_tree corpus/immich "bench/$setup/immich-server" "" HEAD:server
done

step "graphs"
for repo in karpathy fastapi chrome-devtools-mcp agent-browser claudes-c-compiler immich-server; do
  ( cd "bench/wb/$repo" && "$WINGBOX" build ) > "bench/log/$repo.wingbox_build.log" 2>&1
  echo "$repo built"
done

step "reference edges, from resolvers that share no code with Wingbox"
$PY "$T/py_edges.py" corpus/karpathy out/karpathy.ref.json --repo-roots nanoGPT,minGPT,micrograd 2>/dev/null | tail -1
$PY "$T/py_edges.py" corpus/fastapi out/fastapi.ref.json | tail -1
"$W/venv_ref/bin/python" "$T/grimp_check.py" corpus/fastapi out/fastapi.ref.json out/fastapi.grimp_check.json fastapi tests docs_src scripts | head -1
$PY "$T/ts_resolve.py" corpus/chrome-devtools-mcp "" out/chrome-devtools-mcp.ts_refs.json | tail -1
$PY "$T/ts_resolve.py" corpus/immich server out/immich-server.ts_refs.raw.json | tail -1
$PY "$T/strip_scope.py" out/immich-server.ts_refs.raw.json server out/immich-server.ts_refs.json
$PY "$T/rust_resolve.py" corpus/agent-browser out/agent-browser.rust_refs.json | tail -1
$PY "$T/rust_resolve.py" corpus/claudes-c-compiler out/claudes-c-compiler.rust_refs.json | tail -1

step "test: edges against the import statements"
$PY "$HERE/edge_test.py" "$W" out/edges.json

step "test: the impact command on the sampled questions"
$PY "$HERE/impact100.py" "$W" sample
$PY "$HERE/impact100.py" "$W" command > bench/log/command.log
tail -1 bench/log/command.log

step "results"
$PY "$HERE/impact100.py" "$W" results "$W/out/results"
cp out/edges.json out/fastapi.grimp_check.json out/commits.txt out/results/
if [ "${VERIFY:-1}" = 1 ] && [ -d "$HERE/results" ]; then
  $PY "$HERE/verify.py" "$HERE/results" "$W/out/results"
else
  echo "VERIFY=0 or no committed results: nothing compared"
fi
