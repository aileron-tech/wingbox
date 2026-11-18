#!/bin/sh
# The PHP benchmark of benchmarks/php/README.md, from nothing.
#
# Usage: benchmarks/php/run.sh <work dir>
# Needs: git, uv (https://docs.astral.sh/uv/), PHP 8.4 with token_get_all.
# Network: GitHub and PyPI. No model, no key.
# Everything is written under <work dir>. The same run in a clean container: Dockerfile.php.
#
# WINGBOX_SPEC    the Wingbox under test (default: the checkout that holds this script), a path or
#                 a package spec; `WINGBOX_SPEC=wingbox==X.Y.Z` runs a released version.
# WINGBOX_BUILD   the command that builds a graph (default: the installed `wingbox build`), for a
#                 sandbox whose process pool cannot start: a wrapper around scripts/build_code_graph.py.
# PYTHON          the interpreter of the benchmark scripts (default: python3, 3.11 or newer).
# RUN_ONLY=corpus stop after the environment and the corpora are in place.
# VERIFY=0        do not compare the results with the committed ones (first run of a new version).
set -eu

HERE=$(cd "$(dirname "$0")" && pwd)
T=$HERE/tools
W=${1:?usage: run.sh <work dir>}
mkdir -p "$W"
W=$(cd "$W" && pwd)
WINGBOX_SPEC=${WINGBOX_SPEC:-$(cd "$HERE/../.." && pwd)}
if [ -d "$WINGBOX_SPEC" ]; then
  WINGBOX_SOURCE=checkout
  WINGBOX_SPEC=$(cd "$WINGBOX_SPEC" && pwd)
else
  WINGBOX_SOURCE=$WINGBOX_SPEC
fi
PY=${PYTHON:-python3}
# uv must not write outside the work directory (a sandboxed or container run may
# have no writable home); every cache and interpreter lives under $W.
UV_CACHE_DIR=${UV_CACHE_DIR:-$W/.uv-cache}
UV_PYTHON_INSTALL_DIR=${UV_PYTHON_INSTALL_DIR:-$W/.uv-python}
UV_LINK_MODE=${UV_LINK_MODE:-copy}
export UV_CACHE_DIR UV_PYTHON_INSTALL_DIR UV_LINK_MODE
COMMITS=$HERE/results/commits.txt
[ -f "$COMMITS" ] || { echo "missing $COMMITS (the corpus pins)" >&2; exit 1; }
cd "$W"
mkdir -p out corpus bench/plain bench/wb bench/log

step() { printf '\n== %s\n' "$1"; }

# The Wingbox under test with every language parser: the extra `all` (C#, Java, Go, Ruby, PHP and Dart are
# installed on demand otherwise). A release up to 0.11.0 carries its parsers as dependencies and has no
# such extra: uv says so and installs it as it is.
with_all() {
  case "$1" in
    wingbox==*) printf 'wingbox[all]==%s' "${1#wingbox==}" ;;
    *) printf '%s[all]' "$1" ;;
  esac
}

step "environment"
[ -d venv_wb ] || uv venv --quiet --python 3.12 venv_wb
# --reinstall: a second run in the same work dir must test the current source,
# not the wheel installed the first time.
uv pip install --quiet --reinstall --no-cache --python venv_wb/bin/python "$(with_all "$WINGBOX_SPEC")"
WINGBOX=$W/venv_wb/bin/wingbox
WINGBOX_BUILD=${WINGBOX_BUILD:-$WINGBOX}
"$WINGBOX" --version
php -r 'echo "PHP ", PHP_VERSION, "\n";'

step "corpora at pinned commits"
fetch() { # <dir> <url> <commit>
  if [ ! -d "$1/.git" ]; then
    mkdir -p "$1"
    git -C "$1" init --quiet
    git -C "$1" remote add origin "$2"
    git -C "$1" fetch --quiet --depth 1 origin "$3" </dev/null
    git -C "$1" checkout --quiet FETCH_HEAD
  fi
  actual=$(git -C "$1" rev-parse HEAD)
  if [ "$actual" != "$3" ]; then
    echo "$1 is at $actual, expected $3" >&2
    exit 1
  fi
}
: > out/commits.txt
while read -r name url commit tag; do
  case "${name:-}" in
    ''|'#'*) continue ;;
  esac
  fetch "corpus/$name" "$url" "$commit"
  printf '%s %s %s %s\n' "$name" "$url" "$commit" "${tag:-}" >> out/commits.txt
done < "$COMMITS"
cat out/commits.txt
if [ "${RUN_ONLY:-}" = corpus ]; then
  echo "RUN_ONLY=corpus: the environment and the corpora are ready in $W"
  exit 0
fi

step "one clean copy of each corpus: plain and for Wingbox"
export_tree() { # <git dir> <destination>
  mkdir -p "$2"
  git -C "$1" archive --format=tar HEAD | tar -x -C "$2"
}
while read -r name url commit tag; do
  case "${name:-}" in
    ''|'#'*) continue ;;
  esac
  [ -d "bench/plain/$name" ] || export_tree "corpus/$name" "bench/plain/$name"
  [ -d "bench/wb/$name" ] || export_tree "corpus/$name" "bench/wb/$name"
done < out/commits.txt

step "graphs"
while read -r name url commit tag; do
  case "${name:-}" in
    ''|'#'*) continue ;;
  esac
  ( cd "bench/wb/$name" && "$WINGBOX_BUILD" build --exclude-dir vendor ) > "bench/log/$name.wingbox_build.log" 2>&1
  [ -f "bench/wb/$name/.wingbox/index.sexp" ] || { echo "no graph for $name" >&2; exit 1; }
  echo "$name built"
done < out/commits.txt

step "file lists, the reference resolver and the graph edges"
while read -r name url commit tag; do
  case "${name:-}" in
    ''|'#'*) continue ;;
  esac
  "$PY" -I "$T/list_files.py" "bench/plain/$name" "out/$name.files.json"
  php "$T/php_reference.php" "out/$name.files.json" "out/$name.php_refs.json" --root "bench/plain/$name"
  "$PY" -I "$T/graph_edges.py" "bench/wb/$name" "bench/wb/$name/.wingbox/index.sexp" "out/$name.files.json" "out/$name.graph.json"
done < out/commits.txt

step "questions"
"$PY" -I "$T/questions.py" "$W" sample

step "wingbox impact and the edge audit"
"$PY" -I "$T/verify.py" "$W"

step "versions"
version=$("$WINGBOX" --version)
case "$version" in
  *0+unknown*)
    if [ -d "$WINGBOX_SPEC" ] && [ -f "$WINGBOX_SPEC/pyproject.toml" ]; then
      version=$(cd "$WINGBOX_SPEC" && "$PY" -c 'import tomllib; print("wingbox " + tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])')
    fi
    ;;
esac
php_version=$(php -r 'echo PHP_VERSION;')
printf '{"wingbox": "%s", "date": "%s", "php": "%s", "spec": "%s"}\n' \
  "$version" "$(date -u +%F)" "$php_version" "$WINGBOX_SOURCE" > out/versions.json
cat out/versions.json

step "results"
"$PY" -I "$T/questions.py" "$W" results "$W/out/results"
if [ "${VERIFY:-1}" = 1 ] && [ -f "$HERE/results/questions.json" ]; then
  if grep -q '"synthetic": true' "$HERE/results/questions.json"; then
    echo "the committed results are the synthetic fixture: nothing compared"
    echo "replace benchmarks/php/results with out/results before enabling the comparison"
  else
    "$PY" -I "$T/verify.py" --compare "$HERE/results" "$W/out/results"
  fi
else
  echo "VERIFY=0 or no committed results: nothing compared"
fi