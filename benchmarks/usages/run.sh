#!/bin/sh
# Find usages on public code, from nothing: "I will rename or change exported symbol S of file X. Where is S
# used?" Wingbox, CodeGraph and Graphify against the language tooling's own find usages (the files) and the
# SCIP index of the same engines (the use sites).
#
# Usage: benchmarks/usages/run.sh <work dir>
# Needs: git, uv, node (20 or later) with npm, rustup, curl. Network: GitHub, PyPI, the npm registry,
# crates.io and static.rust-lang.org. No model, no key. Everything is written under <work dir>.
#
# The repositories are third-party code and none of it is executed: dependencies are installed with
# lifecycle scripts off (npm --ignore-scripts; pnpm --ignore-scripts --ignore-pnpmfile; Python wheels only),
# Rust crates are only fetched, and rust-analyzer runs with build scripts and proc macros off (its build-script
# command is replaced by `true`). Nothing is built and no test is run. safety.json records the check.
# CodeGraph runs with a private HOME, no daemon and no file watcher; Graphify with no model backend
# (--code-only, GRAPHIFY_BACKEND=none).
#
# VERIFY=0      do not compare the results with the committed ones.
set -eu

HERE=$(cd "$(dirname "$0")" && pwd)
T=$HERE/tools
W=${1:?usage: run.sh <work dir>}
mkdir -p "$W"
W=$(cd "$W" && pwd)
WINGBOX_SPEC=wingbox==0.5.1
CODEGRAPH_SPEC=@colbymchenry/codegraph@1.6.2
GRAPHIFY_SPEC=graphifyy==0.9.110
TYPESCRIPT=6.0.3
SCIP_TYPESCRIPT=0.6.0
SCIP_PYTHON=0.6.8
PYRIGHT=1.1.414
PNPM=12.18.0
RUST_TOOLCHAIN=1.99.0
RUST_ANALYZER=2026-11-02
PY="python3 -I"
cd "$W"
mkdir -p out corpus bench/plain bench/wb bench/cg bench/gf cfg scip logs cg_home
export GRAPHIFY_BACKEND=none

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

step "tools, each in its own folder under $W"
for venv in venv_wb venv_gf; do
  [ -d "$venv" ] || uv venv --quiet --python 3.12 "$venv"
done
uv pip install --quiet --python venv_wb/bin/python "$(with_all "$WINGBOX_SPEC")"
uv pip install --quiet --python venv_gf/bin/python "$GRAPHIFY_SPEC"
[ -x cg/node_modules/.bin/codegraph ] || npm install --silent --prefix "$W/cg" --no-audit --no-fund --no-save "$CODEGRAPH_SPEC"
# Every CodeGraph run as in benchmarks/graphify/run.sh: a private HOME, no daemon, no file watcher, no background network calls.
codegraph() {
  env HOME="$W/cg_home" DO_NOT_TRACK=1 CODEGRAPH_NO_DAEMON=1 CODEGRAPH_NO_WATCH=1 CODEGRAPH_NO_DOWNLOAD=1 NO_COLOR=1 \
    "$W/cg/node_modules/.bin/codegraph" "$@"
}
[ -d tools_node/node_modules ] || npm install --prefix tools_node --ignore-scripts --save-exact --no-audit --no-fund \
  "typescript@$TYPESCRIPT" "@sourcegraph/scip-typescript@$SCIP_TYPESCRIPT" "@sourcegraph/scip-python@$SCIP_PYTHON" \
  "pyright@$PYRIGHT" "pnpm@$PNPM" > logs/tools_node.log 2>&1
export RUSTUP_HOME="$W/rust/rustup" CARGO_HOME="$W/rust/cargo" RUSTUP_TOOLCHAIN=$RUST_TOOLCHAIN RUST_TOOLCHAIN
rustup toolchain install "$RUST_TOOLCHAIN" --profile minimal --component rust-src --no-self-update > logs/rust.log 2>&1
TC=$(ls -d "$RUSTUP_HOME/toolchains/$RUST_TOOLCHAIN"-*)
if [ ! -x ra/rust-analyzer ]; then
  case "$(uname -s)-$(uname -m)" in
    Darwin-x86_64) RA_ASSET=rust-analyzer-x86_64-apple-darwin ;;
    Darwin-arm64) RA_ASSET=rust-analyzer-aarch64-apple-darwin ;;
    Linux-x86_64) RA_ASSET=rust-analyzer-x86_64-unknown-linux-gnu ;;
    Linux-aarch64) RA_ASSET=rust-analyzer-aarch64-unknown-linux-gnu ;;
  esac
  mkdir -p ra
  curl -sSfL -o ra/rust-analyzer.gz "https://github.com/rust-lang/rust-analyzer/releases/download/$RUST_ANALYZER/$RA_ASSET.gz"
  gunzip -f ra/rust-analyzer.gz && chmod +x ra/rust-analyzer
fi
"$W/venv_wb/bin/wingbox" --version
"$W/venv_gf/bin/graphify" --version
echo "codegraph $(codegraph --version)"
ra/rust-analyzer --version

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
  fetch corpus/chrome-devtools-mcp https://github.com/ChromeDevTools/chrome-devtools-mcp 8ba2ac945ebaf1adb92208c29f3a30e73a920956
  fetch corpus/immich https://github.com/immich-app/immich e5d6eb7be67834f59d90e4b7a86f5dec7ac9af86 server
  fetch corpus/agent-browser https://github.com/vercel-labs/agent-browser 53384771fbafc261ba67608ac806dd5aa70b84f0
  fetch corpus/claudes-c-compiler https://github.com/anthropics/claudes-c-compiler 6f1b99acb2f4ec2414592136c2009fe7713deec3
  fetch corpus/fastapi https://github.com/fastapi/fastapi 2a1bd230b454a51644c7e2087f525b2dd245edbf
} | tee out/commits.txt

step "clean copies of each repository: bench/plain for the language tooling, one per tool under test"
export_tree() { # <git dir> <destination> <tree-ish> [paths...]
  src=$1; dest=$2; tree=$3; shift 3
  mkdir -p "$dest"
  git -C "$src" archive --format=tar "$tree" "$@" | tar -x -C "$dest"
}
for setup in wb cg gf; do
  for repo in chrome-devtools-mcp agent-browser claudes-c-compiler fastapi; do
    [ -d "bench/$setup/$repo" ] || export_tree "corpus/$repo" "bench/$setup/$repo" HEAD
  done
  [ -d "bench/$setup/immich-server" ] || export_tree corpus/immich "bench/$setup/immich-server" HEAD:server
done
for repo in chrome-devtools-mcp agent-browser claudes-c-compiler fastapi; do
  [ -d "bench/plain/$repo" ] || export_tree "corpus/$repo" "bench/plain/$repo" HEAD
done
if [ ! -d bench/plain/immich ]; then
  # pnpm needs the manifests of every workspace project named in the lockfile, and the sources of the
  # workspace package the server depends on.
  projects=$(awk '/^importers:/{p=1; n=0; next} p&&/^[^ ]/{p=0} p&&/^  [^ ].*:$/{sub(/^  /,""); sub(/:$/,""); a[n++]=$0}
                  END{for(i=0;i<n;i++) if (a[i] != ".") print a[i]}' corpus/immich/pnpm-lock.yaml)
  manifests=""
  for project in $projects; do manifests="$manifests $project/package.json"; done
  # shellcheck disable=SC2086
  export_tree corpus/immich bench/plain/immich HEAD package.json pnpm-lock.yaml pnpm-workspace.yaml $manifests server packages/plugin-sdk
fi

step "dependencies for the language tooling (lifecycle scripts off)"
NPM_CACHE="$W/cache/npm"
[ -d bench/plain/chrome-devtools-mcp/node_modules ] || ( cd bench/plain/chrome-devtools-mcp && \
  npm ci --ignore-scripts --no-audit --no-fund --cache "$NPM_CACHE" ) > logs/deps.chrome-devtools-mcp.log 2>&1
[ -d bench/plain/immich/node_modules ] || ( cd bench/plain/immich && \
  node "$W/tools_node/node_modules/pnpm/bin/pnpm.mjs" install --frozen-lockfile --ignore-scripts --ignore-pnpmfile --filter "immich..." \
  --store-dir "$W/cache/pnpm" ) > logs/deps.immich-server.log 2>&1
for crate in agent-browser/cli claudes-c-compiler; do
  name=$(echo "$crate" | cut -d/ -f1)
  locked=""; [ -f "bench/plain/$crate/Cargo.lock" ] && locked=--locked
  ( cd "bench/plain/$crate" && "$TC/bin/cargo" fetch $locked ) > "logs/deps.$name.log" 2>&1
done
if [ ! -d venv_py ]; then
  uv export --project corpus/fastapi --frozen --no-dev --no-emit-project --no-hashes --format requirements-txt > out/fastapi.requirements.txt
  uv venv --quiet --seed --python 3.12 venv_py
  uv pip install --quiet --python venv_py/bin/python --only-binary :all: -r out/fastapi.requirements.txt > logs/deps.fastapi.log 2>&1
fi

step "settings of the language servers"
RA_INIT='{"cargo": {"buildScripts": {"enable": false, "overrideCommand": ["true"]}}, "procMacro": {"enable": false}, "checkOnSave": false, "cachePriming": {"enable": true}}'
echo "$RA_INIT" > cfg/ra-scip.json
cat > cfg/rust-analyzer.json <<EOF
{"tool": "rust-analyzer $RUST_ANALYZER", "command": ["$W/ra/rust-analyzer"], "wait": "quiescent",
 "env": {"CARGO_HOME": "$CARGO_HOME", "RUSTUP_HOME": "$RUSTUP_HOME", "RUSTUP_TOOLCHAIN": "$RUST_TOOLCHAIN",
         "PATH": "$TC/bin:/usr/bin:/bin", "CARGO": "$TC/bin/cargo", "RUSTC": "$TC/bin/rustc",
         "RUST_SRC_PATH": "$TC/lib/rustlib/src/rust/library", "CARGO_NET_OFFLINE": "true"},
 "init": $RA_INIT}
EOF
cat > cfg/pyright.json <<EOF
{"tool": "pyright $PYRIGHT", "command": ["node", "$W/tools_node/node_modules/pyright/langserver.index.js", "--stdio"],
 "wait": "log:^Found [0-9]+ source files", "language_id": "python",
 "config": {"python": {"pythonPath": "$W/venv_py/bin/python"},
            "python.analysis": {"diagnosticMode": "openFilesOnly", "autoSearchPaths": true, "useLibraryCodeForTypes": true}}}
EOF
TS_LIB=$W/tools_node/node_modules/typescript
plain_root() { case $1 in immich-server) echo bench/plain/immich/server ;; *) echo "bench/plain/$1" ;; esac; }
REPOS="chrome-devtools-mcp:typescript immich-server:typescript agent-browser:rust claudes-c-compiler:rust fastapi:python"

step "exported symbols, from each language's own parser"
for item in $REPOS; do
  repo=${item%%:*}; language=${item#*:}
  [ -f "out/$repo.symbols.json" ] && continue
  case $language in
    typescript) node "$T/ts_refs.mjs" "$TS_LIB" "$(plain_root "$repo")" symbols "out/$repo.symbols.json" ;;
    python) $PY "$T/py_symbols.py" "$(plain_root "$repo")" "out/$repo.symbols.json" ;;
    rust) $PY "$T/rs_symbols.py" "$(plain_root "$repo")" "out/$repo.symbols.json" ;;
  esac
done
$PY "$HERE/usages.py" "$W" candidates

step "reference files: find usages of the language tooling, in the drawn order, until 20 symbols used by another file"
for item in $REPOS; do
  repo=${item%%:*}; language=${item#*:}
  if [ ! -f "out/$repo.reference.json" ]; then
    case $language in
      typescript) node "$T/ts_refs.mjs" "$TS_LIB" "$(plain_root "$repo")" refs "out/$repo.candidates.json" 20 "out/$repo.reference.json" ;;
      python) $PY "$T/lsp_refs.py" cfg/pyright.json "$(plain_root "$repo")" refs "out/$repo.candidates.json" 20 "out/$repo.reference.json" ;;
      rust) $PY "$T/lsp_refs.py" cfg/rust-analyzer.json "$(plain_root "$repo")" refs "out/$repo.candidates.json" 20 "out/$repo.reference.json" ;;
    esac > "logs/reference.$repo.log"
  fi
  tail -1 "logs/reference.$repo.log"
done
$PY "$HERE/usages.py" "$W" questions

step "reference sites: SCIP indexes of the same engines"
for item in $REPOS; do
  repo=${item%%:*}; language=${item#*:}
  [ -s "scip/$repo.scip" ] || sh "$T/scip_index.sh" "$repo" "$language" "$(plain_root "$repo")" "$W/scip/$repo.scip" "$W" > "logs/scip.$repo.log" 2>&1
done
$PY "$HERE/usages.py" "$W" sites

step "indexes of the tools under test"
for item in $REPOS; do
  repo=${item%%:*}
  [ -f "bench/wb/$repo/.wingbox/index.sexp" ] || ( cd "bench/wb/$repo" && "$W/venv_wb/bin/wingbox" build ) > "logs/$repo.wingbox_build.log" 2>&1
  [ -d "bench/cg/$repo/.codegraph" ] || ( cd "bench/cg/$repo" && codegraph init --yes ) > "logs/$repo.codegraph_init.log" 2>&1
  [ -f "bench/gf/$repo/graphify-out/graph.json" ] || ( cd "bench/gf/$repo" && "$W/venv_gf/bin/graphify" extract . --code-only ) > "logs/$repo.graphify_extract.log" 2>&1
  echo "$repo indexed"
done

step "the questions, to each tool"
$PY "$HERE/usages.py" "$W" wingbox > logs/wingbox.queries.log
$PY "$HERE/usages.py" "$W" codegraph > logs/codegraph.queries.log
$PY "$HERE/usages.py" "$W" graphify 1 > logs/graphify_depth1.queries.log
$PY "$HERE/usages.py" "$W" graphify 2 > logs/graphify.queries.log

step "safety: nothing was built"
$PY "$HERE/usages.py" "$W" safety

step "results"
$PY "$HERE/usages.py" "$W" versions
$PY "$HERE/usages.py" "$W" results "$W/out/results"
$PY "$HERE/make_report.py" "$W/out/results" "$W/out/REPORT.md"
if [ "${VERIFY:-1}" = 1 ] && [ -f "$HERE/results/rows.json" ]; then
  $PY "$HERE/verify.py" "$HERE/results" "$W/out/results"
else
  echo "VERIFY=0 or no committed results: nothing compared"
fi
echo "$W/out/REPORT.md is to be equal to benchmarks/usages/README.md, apart from the date in Versions."
