#!/bin/sh
# Wingbox on the Theory of Code Space benchmark (ToCS, arXiv 2603.00601), from nothing.
#
# Usage: benchmarks/tocs/run.sh <work dir>
# Needs: git, uv (https://docs.astral.sh/uv/). Network: GitHub and PyPI. No model, no key.
# Everything is written under <work dir>; the results go to <work dir>/out/results.
#
# WINGBOX_BEFORE  the Wingbox to compare with (default: the release named below)
# WINGBOX_SPEC    the Wingbox under test (default: this checkout)
# VERIFY=0        do not compare the results with the committed ones
set -eu

HERE=$(cd "$(dirname "$0")" && pwd)
W=${1:?usage: run.sh <work dir>}
mkdir -p "$W"
W=$(cd "$W" && pwd)
WINGBOX_BEFORE=${WINGBOX_BEFORE:-wingbox==0.2.4}
WINGBOX_SPEC=${WINGBOX_SPEC:-$(cd "$HERE/../.." && pwd)}
TOCS_URL=https://github.com/che-shr-cat/tocs
TOCS_COMMIT=e617928f612c839c4b40debb875249472ab4d765
# Codebases made with the ToCS generator in addition to the six shipped in its data/ folder.
GENERATED="medium:1 medium:2 medium:3 medium:4 medium:5 large:1 large:2 large:3"
cd "$W"
mkdir -p out/results

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

step "environments"
for venv in venv_before venv_after venv_tocs; do
  [ -d "$venv" ] || uv venv --quiet --python 3.12 "$venv"
done
uv pip install --quiet --python venv_before/bin/python "$(with_all "$WINGBOX_BEFORE")"
uv pip install --quiet --python venv_after/bin/python "$(with_all "$WINGBOX_SPEC")"
uv pip install --quiet --python venv_tocs/bin/python "pydantic==2.12.5" "typer==0.20.0"
TOCS_PY="$W/venv_tocs/bin/python -I"

step "ToCS at a pinned commit"
if [ ! -d tocs/.git ]; then
  git init --quiet tocs
  git -C tocs remote add origin "$TOCS_URL"
  git -C tocs fetch --quiet --depth 1 origin "$TOCS_COMMIT"
  git -C tocs checkout --quiet FETCH_HEAD
fi
echo "tocs $(git -C tocs rev-parse HEAD)" | tee out/results/commits.txt

step "codebases: shipped and generated"
rm -rf codebases truth
mkdir -p codebases truth
for dir in tocs/data/codebase_* tocs/data/large_*; do
  cp -R "$dir" "codebases/$(basename "$dir")"
done
for item in $GENERATED; do
  tier=${item%:*}; seed=${item#*:}
  (cd tocs && "$W/venv_tocs/bin/python" -m generator --pattern pipeline --complexity "$tier" --seed "$seed" \
     --output "$W/codebases/gen_${tier}_$seed" > /dev/null)
done
# The ground truth is moved out of the tree that Wingbox reads.
for dir in codebases/*; do
  mv "$dir/ground_truth.json" "truth/$(basename "$dir").json"
done
ls codebases | tr '\n' ' '; echo

for label in before after; do
  step "graphs and scores: $label"
  rm -rf "bench_$label"
  cp -R codebases "bench_$label"
  mkdir -p "log/$label"
  for dir in "bench_$label"/*; do
    (cd "$dir" && "$W/venv_$label/bin/wingbox" build) > "log/$label/$(basename "$dir").build.log" 2>&1
  done
  "$W/venv_$label/bin/wingbox" --version > "out/results/version.$label.txt"
  $TOCS_PY "$HERE/tocs_score.py" "$W/tocs" "$W/bench_$label" "$label" "$W/out/results"
done

step "report"
$TOCS_PY "$HERE/make_report.py" "$W/out/results" "$W/out/README.md"
if [ "${VERIFY:-1}" = 1 ] && [ -d "$HERE/results" ]; then
  $TOCS_PY "$HERE/verify.py" "$HERE/results" "$W/out/results"
else
  echo "VERIFY=0 or no committed results: nothing compared"
fi
