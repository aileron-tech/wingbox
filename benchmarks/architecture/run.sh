#!/bin/sh
# The architecture benchmark of benchmarks/architecture/README.md: role mapping of folder names
# (dictionaries, + Levenshtein, + word vectors) and style detection. No network, no model.
#
# Usage: benchmarks/architecture/run.sh
# Writes results/ and compares them with the committed files (git diff --exit-code).
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
python3 -I "$HERE/evaluate.py" --trees "$HERE/trees"
git -C "$HERE" diff --exit-code -- results
