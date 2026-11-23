#!/bin/sh
# The tests of benchmarks/cpp/README.md, from nothing.
#
# Usage: benchmarks/cpp/run.sh <work dir>
# Needs: git, uv (https://docs.astral.sh/uv/), clang and doxygen. Network: GitHub and PyPI. No model, no key.
# Everything is written under <work dir>; the result files are copied to <work dir>/out/results.
#
# WINGBOX_SPEC   the Wingbox under test (default: the release named below), e.g. a path to a checkout.
set -eu

HERE=$(cd "$(dirname "$0")" && pwd)
T=$HERE/tools
W=${1:?usage: run.sh <work dir>}
mkdir -p "$W"
W=$(cd "$W" && pwd)
WINGBOX_SPEC=${WINGBOX_SPEC:-wingbox==0.17.0}
cd "$W"
mkdir -p out/results corpus bench

step() { printf '\n== %s\n' "$1"; }

# The Wingbox under test with every language parser: the extra `all` (see benchmarks/impact/run.sh).
with_all() {
  case "$1" in
    wingbox==*) printf 'wingbox[all]==%s' "${1#wingbox==}" ;;
    *) printf '%s[all]' "$1" ;;
  esac
}

step "environment"
[ -d venv_wb ] || uv venv --quiet --python 3.12 venv_wb
uv pip install --quiet --python venv_wb/bin/python "$(with_all "$WINGBOX_SPEC")"
WINGBOX=$W/venv_wb/bin/wingbox
"$WINGBOX" --version

step "corpus at pinned tags"
fetch() { # <dir> <url> <tag>
  if [ ! -d "corpus/$1/.git" ]; then
    mkdir -p "corpus/$1"
    git -C "corpus/$1" init --quiet
    git -C "corpus/$1" remote add origin "$2"
    git -C "corpus/$1" fetch --quiet --depth 1 origin "refs/tags/$3"
    git -C "corpus/$1" checkout --quiet FETCH_HEAD
  fi
  echo "$1 $3 $(git -C "corpus/$1" rev-parse HEAD)"
}
{
  fetch fmt https://github.com/fmtlib/fmt 11.0.2
  fetch leveldb https://github.com/google/leveldb 1.23
  fetch libuv https://github.com/libuv/libuv v1.48.0
} | tee out/commits.txt

# The include roots the CMake files of each project give the compiler.
roots() {
  case "$1" in
    fmt) echo "include test test/gtest" ;;
    leveldb) echo ". include third_party/googletest/googletest/include third_party/googletest/googlemock/include third_party/benchmark/include" ;;
    libuv) echo "include src" ;;
  esac
}

for repo in fmt leveldb libuv; do
  step "$repo"
  if [ ! -d "bench/$repo" ]; then
    mkdir -p "bench/$repo"
    git -C "corpus/$repo" archive --format=tar HEAD | tar -x -C "bench/$repo"
  fi
  ( cd "bench/$repo" && WINGBOX_JOBS=1 "$WINGBOX" build . ) > "out/$repo.build.log" 2>&1
  # shellcheck disable=SC2046
  python3 -I "$T/cpp_reference.py" "corpus/$repo" "out/$repo.ref.json" $(roots "$repo")
  # shellcheck disable=SC2046
  python3 -I "$T/clang_check.py" "corpus/$repo" "out/$repo.ref.json" "out/results/$repo.clang.json" $(roots "$repo") > /dev/null
  python3 -I "$HERE/../impact/tools/wb_edges.py" "bench/$repo" "out/$repo.wb.json" --ext .py > /dev/null
  python3 -I "$HERE/questions.py" "$WINGBOX" "bench/$repo" "out/$repo.ref.json" "out/$repo.wb.json" "out/results/$repo.questions.json"
  # shellcheck disable=SC2046
  python3 -I "$T/doxygen_check.py" "corpus/$repo" "out/$repo.wb.json" "out/results/$repo.doxygen.json" "out/doxygen-$repo" $(roots "$repo")
done

step "results"
python3 -I - "$("$WINGBOX" --version)" out/commits.txt out/results/run.json <<'PY'
import datetime, json, sys
version, commits_path, out_path = sys.argv[1:4]
rows = [line.split() for line in open(commits_path, encoding="utf-8") if line.strip()]
json.dump({"wingbox": version.replace("wingbox ", "Wingbox "), "date": datetime.date.today().isoformat(),
           "commits": {name: commit for name, _tag, commit in rows}, "tags": {name: tag for name, tag, _commit in rows}},
          open(out_path, "w", encoding="utf-8"), indent=1)
PY
echo "results in $W/out/results; write the page with: python3 -I $HERE/make_report.py $W/out/results $HERE/README.md"
