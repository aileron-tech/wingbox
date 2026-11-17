#!/bin/sh
# Build the SCIP index of one repository with the pinned indexer of its language.
# Usage: sh scip_index.sh <repo> <language> <analysed root> <output .scip> <work dir>
# Environment (from run.sh): RUSTUP_HOME, CARGO_HOME, RUST_TOOLCHAIN.
set -eu
repo=$1; language=$2; root=$(cd "$3" && pwd); output=$4; W=$5
case $language in
  typescript)
    # scip-typescript indexes the projects it is given (paths of folders with a tsconfig.json); every such folder is one.
    projects=$(find "$root" -name tsconfig.json -not -path '*/node_modules/*' -not -path "$root/third_party/*" | sed 's|/tsconfig.json$||' | sort)
    # shellcheck disable=SC2086
    node "$W/tools_node/node_modules/@sourcegraph/scip-typescript/dist/src/main.js" index --cwd "$root" --output "$output" --no-progress-bar $projects ;;
  python)
    # scip-python asks the `python` on PATH for its packages: the environment with the repository's dependencies.
    cd "$root"
    PATH="$W/venv_py/bin:$PATH" node "$W/tools_node/node_modules/@sourcegraph/scip-python/index.js" index \
      --project-name "$repo" --project-version pinned --output "$output" --quiet ;;
  rust)
    # Build scripts and proc macros off; the build-script command is replaced by `true`, so cargo check never runs.
    TC=$(ls -d "$RUSTUP_HOME/toolchains/$RUST_TOOLCHAIN"-*)
    cd "$root"
    env CARGO_HOME="$CARGO_HOME" RUSTUP_HOME="$RUSTUP_HOME" RUSTUP_TOOLCHAIN="$RUST_TOOLCHAIN" PATH="$TC/bin:/usr/bin:/bin" \
      CARGO="$TC/bin/cargo" RUSTC="$TC/bin/rustc" RUST_SRC_PATH="$TC/lib/rustlib/src/rust/library" CARGO_NET_OFFLINE=true \
      "$W/ra/rust-analyzer" scip . --output "$output" --config-path "$W/cfg/ra-scip.json" ;;
esac
test -s "$output"
