#!/bin/sh
# OntoIndex on tests B, C and E of benchmarks/graphify/README.md, after run.sh.
#
# Usage: benchmarks/graphify/tools/oi_run.sh <work dir> [rounds]     (SKIP_TIMING=1 stops before test E)
# <work dir> is the one run.sh prepared (corpus, reference edges, bench/gold). Needs: docker, curl.
# OntoIndex 3.1.9 runs only on Linux (its analyze takes a native file lock that exists only there),
# so it runs in a container built from tools/oi.Dockerfile: the GitHub release asset, checked against
# its sha256, installed without lifecycle scripts. Every OntoIndex step runs with --network none.
# The build-time test runs Wingbox and Graphify in the same container, interleaved with OntoIndex.
set -eu

HERE=$(cd "$(dirname "$0")" && pwd)
BENCHMARKS=$(cd "$HERE/../.." && pwd)
W=${1:?usage: oi_run.sh <work dir> [rounds]}
ROUNDS=${2:-3}
W=$(cd "$W" && pwd)
VERSION=3.1.9
SHA256=8a6a0714f44d5ee330a3a2326c41db21bb14f4e8ad6bad11d24d6905443b76dd
IMAGE=wingbox-bench-ontoindex:$VERSION
cd "$W"
step() { printf '\n== %s\n' "$1"; }

step "image"
mkdir -p oi_build oi_home oi_home_work out bench/log
[ -f "oi_build/ontoindex-$VERSION.tgz" ] || curl -fsSL -o "oi_build/ontoindex-$VERSION.tgz" \
  "https://github.com/ontograph/ontoindex/releases/download/github-release%2F$VERSION/ontoindex-$VERSION.tgz"
echo "$SHA256  oi_build/ontoindex-$VERSION.tgz" | shasum -a 256 -c -
cp "$HERE/oi.Dockerfile" oi_build/Dockerfile
docker build -q -t "$IMAGE" oi_build > /dev/null
# The work dir is mounted at the same path, so the paths OntoIndex records are the host paths;
# benchmarks/ is mounted read-only at /bench.
oi() { # <working dir> <command...>
  oi_dir=$1; shift
  docker run --rm --network none -v "$W:$W" -e HOME=/benchhome -v "$W/$(home_of "$oi_dir"):/benchhome" -v "$BENCHMARKS:/bench:ro" -w "$oi_dir" "$IMAGE" "$@"
}
# OntoIndex keeps a registry of indexed repositories in its HOME and refuses two repositories of one name;
# work/oi/fastapi and bench/oi/fastapi are both "fastapi", so the two folders get two HOMEs.
home_of() { case "$1" in "$W"/work/*) echo oi_home_work ;; *) echo oi_home ;; esac; }

step "one clean git checkout of each corpus for OntoIndex"
export_git() { # <git dir> <destination> [<prefix>/]
  mkdir -p "$2"
  git -C "$1" archive --format=tar ${3:+--prefix="$3"} HEAD | tar -x -C "$2"
}
commit_all() {
  git -C "$1" init -q && git -C "$1" add -A && git -C "$1" -c user.name=bench -c user.email=bench@localhost commit -qm corpus
}
if [ ! -d work/oi/karpathy ]; then
  for repo in nanoGPT minGPT micrograd; do export_git "corpus/karpathy/$repo" work/oi/karpathy "$repo/"; done
  commit_all work/oi/karpathy
fi
[ -d work/oi/fastapi ] || { export_git corpus/fastapi work/oi/fastapi; commit_all work/oi/fastapi; }
for repo in chrome-devtools-mcp agent-browser claudes-c-compiler fastapi; do
  [ -d "bench/oi/$repo" ] || { export_git "corpus/$repo" "bench/oi/$repo"; commit_all "bench/oi/$repo"; }
done

step "graphs"
build() { # <graph dir>
  dir=$1
  # Up to three attempts: OntoIndex stops the whole analyze when one database statement takes more than
  # 30 s (a fixed limit), which its full-text index of FastAPI's files reaches on a loaded machine.
  for attempt in 1 2 3; do
    [ -f "$dir/.ontoindex/meta.json" ] && break
    log="bench/log/$(echo "$dir" | tr / _).ontoindex_analyze.$attempt.log"
    oi "$W/$dir" ontoindex analyze . --skip-agents-md > "$log" 2>&1 || echo "$dir: attempt $attempt failed: $(tr '\r' '\n' < "$log" | grep -m1 'Analysis failed')"
  done
  [ -f "$dir/.ontoindex/meta.json" ] || { echo "ontoindex analyze failed in $dir"; exit 1; }
  oi "$W/$dir" ontoindex index . > /dev/null  # registered in its HOME (no new analysis)
  echo "$dir built"
}
for dir in work/oi/karpathy work/oi/fastapi bench/oi/chrome-devtools-mcp bench/oi/agent-browser bench/oi/claudes-c-compiler bench/oi/fastapi; do
  build "$dir"
done

step "test B: edges against the import statements"
for corpus in karpathy fastapi; do
  oi "$W/work/oi/$corpus" python3 -I /bench/graphify/tools/oi_edges.py "$W/work/oi/$corpus" "$W/out/$corpus.oi"
  for kind in imports all; do
    "$W/venv_ref/bin/python" -I "$HERE/score_edges.py" "$corpus: ontoindex $kind" "out/$corpus.ref.json" "out/$corpus.oi.$kind.pairs.json" "out/$corpus.oi.$kind.score.all.json"
  done
done
for kind in imports all; do
  "$W/venv_ref/bin/python" -I "$HERE/score_edges.py" "fastapi/: ontoindex $kind" out/fastapi.ref.json "out/fastapi.oi.$kind.pairs.json" "out/fastapi.oi.$kind.score.fastapi.json" --scope fastapi/
done

step "test C: the impact command without an agent"
oi "$W" python3 -I /bench/graphify/tools/oi_command_check.py "$W" "$W/out/command_check.ontoindex.json"

step "the 100 questions of benchmarks/impact (completeness and answer size)"
if [ ! -d corpus/immich/.git ]; then
  mkdir -p corpus/immich
  git -C corpus/immich init --quiet
  git -C corpus/immich remote add origin https://github.com/immich-app/immich
  git -C corpus/immich sparse-checkout set server
  git -C corpus/immich fetch --quiet --depth 1 --filter=blob:none origin e5d6eb7be67834f59d90e4b7a86f5dec7ac9af86
  git -C corpus/immich checkout --quiet FETCH_HEAD
fi
if [ ! -d bench/oi/immich-server ]; then
  mkdir -p bench/oi/immich-server
  git -C corpus/immich archive --format=tar HEAD:server | tar -x -C bench/oi/immich-server
  commit_all bench/oi/immich-server
fi
build bench/oi/immich-server
oi "$W" python3 -I /bench/impact/tools/oi_impact100.py "$W/bench/oi" "$W/out/command.ontoindex.json"

echo "ontoindex $VERSION" > out/versions.ontoindex.txt
[ "${SKIP_TIMING:-0}" = 1 ] && exit 0

step "test E: build time, cold and after a one-file edit, the three tools in one container"
uptime | sed 's/^/host: /'
docker run --rm --network none -v "$W:$W" -e HOME=/benchhome -v "$W/oi_home:/benchhome" -v "$BENCHMARKS:/bench:ro" -w "$W" "$IMAGE" \
  python3 -I /bench/graphify/tools/time_builds_tools.py "$W" "$W/out/build_times.ontoindex.json" "$ROUNDS" \
  --tool wingbox=/opt/venv_wb/bin/wingbox --tool graphify=/opt/venv_gf/bin/graphify --tool ontoindex=/usr/local/bin/ontoindex
uptime | sed 's/^/host: /'
