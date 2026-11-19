#!/bin/sh
# Reproduce tests A, B and C of benchmarks/graphify/README.md from nothing.
#
# Usage: benchmarks/graphify/run.sh <work dir>
# Needs: git, curl, uv (https://docs.astral.sh/uv/), npm. Network: GitHub, PyPI and npm. No model, no key.
# Takes about 15 minutes and 1.5 GB. Everything is written under <work dir>.
# WINGBOX_SPEC overrides the Wingbox under test (default: the release named below),
# for example WINGBOX_SPEC=/path/to/a/checkout.
# RUN_ONLY=corpus stops after the environments and the corpus are in place (used by tools/time_builds.py).
set -eu

HERE=$(cd "$(dirname "$0")" && pwd)
T=$HERE/tools
W=${1:?usage: run.sh <work dir>}
mkdir -p "$W"
W=$(cd "$W" && pwd)
WINGBOX_SPEC=${WINGBOX_SPEC:-wingbox==0.5.1}
GRAPHIFY_SPEC=graphifyy==0.9.110
GRIMP_SPEC=grimp==3.20
CODEGRAPH_SPEC=@colbymchenry/codegraph@1.6.2
GRAPHIFY_COMMIT=18cd78f4b8c9698db861aa8d7ad1adbf3c836642
export GRAPHIFY_BACKEND=none
cd "$W"
mkdir -p out corpus/karpathy work/gf work/wb work/cg bench/plain bench/wb bench/gf bench/cg bench/log cg cg_home

step() { printf '\n== %s\n' "$1"; }

# The Wingbox under test with every language parser: the extra `all`. The 0.12 releases install C#, Java,
# Go, Ruby, PHP and Dart only with it; later versions carry every parser as a dependency and keep the
# extra. A release up to 0.11.0 has no such extra: uv says so and installs it as it is.
with_all() {
  case "$1" in
    wingbox==*) printf 'wingbox[all]==%s' "${1#wingbox==}" ;;
    *) printf '%s[all]' "$1" ;;
  esac
}

step "environments"
for venv in venv_wb venv_gf venv_ref; do
  [ -d "$venv" ] || uv venv --quiet --python 3.12 "$venv"
done
uv pip install --quiet --python venv_wb/bin/python "$(with_all "$WINGBOX_SPEC")"
uv pip install --quiet --python venv_gf/bin/python "$GRAPHIFY_SPEC"
uv pip install --quiet --python venv_ref/bin/python "$GRIMP_SPEC"
# CodeGraph from npm into its own folder; it runs on the Node runtime its package bundles.
[ -x cg/node_modules/.bin/codegraph ] || npm install --silent --prefix "$W/cg" --no-audit --no-fund --no-save "$CODEGRAPH_SPEC"
PY="$W/venv_ref/bin/python -I"
WINGBOX=$W/venv_wb/bin/wingbox
GRAPHIFY=$W/venv_gf/bin/graphify
CG_BUNDLE=$(ls -d "$W"/cg/node_modules/@colbymchenry/codegraph-*-* | head -1)
# Every CodeGraph run: a private HOME, no daemon, no file watcher, no background network calls.
codegraph() {
  env HOME="$W/cg_home" DO_NOT_TRACK=1 CODEGRAPH_NO_DAEMON=1 CODEGRAPH_NO_WATCH=1 CODEGRAPH_NO_DOWNLOAD=1 NO_COLOR=1 \
    "$W/cg/node_modules/.bin/codegraph" "$@"
}
cg_graph() {
  env HOME="$W/cg_home" DO_NOT_TRACK=1 "$CG_BUNDLE/node" --disable-warning=ExperimentalWarning "$T/cg_graph.js" "$CG_BUNDLE/lib/dist/index.js" "$@"
}
"$WINGBOX" --version
"$GRAPHIFY" --version
echo "codegraph $(codegraph --version)"

step "corpus at pinned commits"
fetch() { # <dir> <url> <commit>
  if [ ! -d "$1/.git" ]; then
    mkdir -p "$1"
    git -C "$1" init --quiet
    git -C "$1" remote add origin "$2"
    git -C "$1" fetch --quiet --depth 1 origin "$3"
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
} | tee out/commits.txt
if [ "${RUN_ONLY:-}" = corpus ]; then
  echo "RUN_ONLY=corpus: environments and corpus are ready in $W"
  exit 0
fi
[ -f out/graphify_published_graph.json ] || curl -fsSL -o out/graphify_published_graph.json \
  "https://raw.githubusercontent.com/Graphify-Labs/graphify/$GRAPHIFY_COMMIT/worked/karpathy-repos/graph.json"

step "one clean copy of each corpus per tool"
export_tree() { # <git dir> <destination> [<prefix>/]
  mkdir -p "$2"
  git -C "$1" archive --format=tar ${3:+--prefix="$3"} HEAD | tar -x -C "$2"
}
for tool in gf wb cg; do
  if [ ! -d "work/$tool/karpathy" ]; then
    for repo in nanoGPT minGPT micrograd; do export_tree "corpus/karpathy/$repo" "work/$tool/karpathy" "$repo/"; done
  fi
  [ -d "work/$tool/fastapi" ] || export_tree corpus/fastapi "work/$tool/fastapi"
done
for repo in chrome-devtools-mcp agent-browser claudes-c-compiler fastapi; do
  for setup in plain wb gf cg; do
    [ -d "bench/$setup/$repo" ] || export_tree "corpus/$repo" "bench/$setup/$repo"
  done
done

step "graphs"
for corpus in karpathy fastapi; do
  ( cd "work/wb/$corpus" && "$WINGBOX" build ) > "bench/log/work.$corpus.wingbox_build.log" 2>&1
  ( cd "work/gf/$corpus" && "$GRAPHIFY" extract . --code-only ) > "bench/log/work.$corpus.graphify_extract.log" 2>&1
  [ -d "work/cg/$corpus/.codegraph" ] || ( cd "work/cg/$corpus" && codegraph init --yes ) > "bench/log/work.$corpus.codegraph_init.log" 2>&1
  echo "work/$corpus built"
done
for repo in chrome-devtools-mcp agent-browser claudes-c-compiler fastapi; do
  ( cd "bench/wb/$repo" && "$WINGBOX" build ) > "bench/log/$repo.wingbox_build.log" 2>&1
  ( cd "bench/gf/$repo" && "$GRAPHIFY" extract . --code-only ) > "bench/log/$repo.graphify_extract.log" 2>&1
  ( cd "bench/gf/$repo" && "$GRAPHIFY" cluster-only . --no-label --no-viz ) > "bench/log/$repo.graphify_cluster.log" 2>&1
  [ -d "bench/cg/$repo/.codegraph" ] || ( cd "bench/cg/$repo" && codegraph init --yes ) > "bench/log/$repo.codegraph_init.log" 2>&1
  echo "bench/$repo built"
done

step "reference edges, from neither tool"
$PY "$T/py_edges.py" corpus/karpathy out/karpathy.ref.json --repo-roots nanoGPT,minGPT,micrograd | tail -1
$PY "$T/py_edges.py" corpus/fastapi out/fastapi.ref.json | tail -1
"$W/venv_ref/bin/python" "$T/grimp_check.py" corpus/fastapi out/fastapi.ref.json out/fastapi.grimp_check.json fastapi tests docs_src scripts | head -1
$PY "$T/ts_resolve.py" corpus/chrome-devtools-mcp "" out/chrome-devtools-mcp.ts_refs.json | tail -1
$PY "$T/rust_resolve.py" corpus/agent-browser out/agent-browser.rust_refs.json | tail -1
$PY "$T/rust_resolve.py" corpus/claudes-c-compiler out/claudes-c-compiler.rust_refs.json | tail -1
$PY "$T/gold.py" "$W" "$HERE/gold"

step "test A: Graphify's token formula"
for corpus in karpathy fastapi; do
  "$W/venv_gf/bin/python" -I "$T/formula_test.py" "work/gf/$corpus" "work/wb/$corpus" "$WINGBOX" "out/$corpus.formula.json" > "out/$corpus.formula.log" 2>&1
  echo "$corpus formula done"
done
( cd work/gf/karpathy && "$GRAPHIFY" benchmark ) > out/graphify_benchmark_literal.txt 2>&1
"$W/venv_gf/bin/python" -I "$T/gf_published.py" out/graphify_published_graph.json 92616 out/karpathy.gf_published.json \
  "what connects micrograd to nanoGPT" "how does FlashAttention improve memory efficiency" "what are the core abstractions" \
  "how are errors handled" "how does the attention mechanism work" > out/karpathy.gf_published.log 2>&1

step "test B: edges against the import statements"
for corpus in karpathy fastapi; do
  $PY "$T/wb_edges.py" "work/wb/$corpus" "out/$corpus.wb.edges.json" --ext .py,.js,.ts,.mjs > /dev/null
  $PY "$T/pairs.py" wingbox "out/$corpus.wb.edges.json" "out/$corpus.wb.dependency.pairs.json" > /dev/null
  $PY "$T/score_edges.py" "$corpus: wingbox" "out/$corpus.ref.json" "out/$corpus.wb.dependency.pairs.json" "out/$corpus.wb.dependency.score.all.json"
  for mode in modules imports; do
    $PY "$T/gf_edges.py" "work/gf/$corpus/graphify-out/graph.json" "work/gf/$corpus" "out/$corpus.gf.$mode.graph.json" --mode "$mode" --ext .py > /dev/null
    $PY "$T/pairs.py" graphify "out/$corpus.gf.$mode.graph.json" "out/$corpus.gf.$mode.pairs.json" > /dev/null
    $PY "$T/score_edges.py" "$corpus: graphify $mode" "out/$corpus.ref.json" "out/$corpus.gf.$mode.pairs.json" "out/$corpus.gf.$mode.score.all.json"
  done
done
$PY "$T/score_edges.py" "fastapi/: wingbox" out/fastapi.ref.json out/fastapi.wb.dependency.pairs.json out/fastapi.wb.dependency.score.fastapi.json --scope fastapi/
$PY "$T/score_edges.py" "fastapi/: graphify modules" out/fastapi.ref.json out/fastapi.gf.modules.pairs.json out/fastapi.gf.modules.score.fastapi.json --scope fastapi/
for corpus in karpathy fastapi; do
  cg_graph pairs "work/cg/$corpus" "out/$corpus.cg.graph.json" > /dev/null
  $PY "$T/pairs.py" codegraph "out/$corpus.cg.graph.json" "out/$corpus.cg.all.pairs.json" > /dev/null
  $PY "$T/pairs.py" codegraph "out/$corpus.cg.graph.json" "out/$corpus.cg.imports.pairs.json" --kind imports > /dev/null
  $PY "$T/score_edges.py" "$corpus: codegraph all" "out/$corpus.ref.json" "out/$corpus.cg.all.pairs.json" "out/$corpus.cg.all.score.all.json"
  $PY "$T/score_edges.py" "$corpus: codegraph imports" "out/$corpus.ref.json" "out/$corpus.cg.imports.pairs.json" "out/$corpus.cg.imports.score.all.json"
done
$PY "$T/score_edges.py" "fastapi/: codegraph all" out/fastapi.ref.json out/fastapi.cg.all.pairs.json out/fastapi.cg.all.score.fastapi.json --scope fastapi/

step "test C: the impact command without an agent"
$PY "$T/command_check.py" "$W" out/command_check.json
$PY "$T/cg_command_check.py" "$W" out/command_check.codegraph.json

step "report"
{
  echo "date $(date -u +%Y-%m-%d)"
  echo "wingbox $("$WINGBOX" --version | awk '{print $NF}')"
  echo "graphify $(uv pip freeze --python venv_gf/bin/python 2>/dev/null | sed -n 's/^graphifyy==//p')"
  echo "grimp $(uv pip freeze --python venv_ref/bin/python 2>/dev/null | sed -n 's/^grimp==//p')"
  echo "codegraph $(codegraph --version)"
  echo "codegraph_date $(date -u +%Y-%m-%d)"
  echo "wingbox_date $(date -u +%Y-%m-%d)"
} > out/versions.txt
$PY "$T/make_report.py" out out/REPORT.md
echo "Tests A, B and C of $W/out/REPORT.md are to be equal to benchmarks/graphify/README.md."
echo "Test D (paid model sessions): python3 -I $T/agent_test.py $W run && python3 -I $T/agent_test.py $W report"
echo "Test F (build time of the three tools on this machine): python3 -I $T/time_builds_same_machine.py $W"
