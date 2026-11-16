# The PHP benchmark in a clean container: nothing from the machine that runs it takes part.
#
# Build from the root of the repository (the context has to hold benchmarks/php and
# benchmarks/impact/stats.py):
#
#   docker build -f benchmarks/php/Dockerfile.php -t wingbox-php-benchmark .
#   docker run --rm wingbox-php-benchmark
#
# The container installs the checkout of this commit (the build context), fetches the
# PHP/Laravel corpora at pinned commits, builds the graphs, computes the reference lists
# with a resolver that shares no code with Wingbox (benchmarks/php/tools/php_reference.php,
# PHP standard library only), runs `wingbox impact` on the sampled questions, audits every
# IMPORTS edge of every graph and compares every result file with the committed one. It
# ends with "REPRODUCED" and exit code 0, or lists the files that differ and exits 1.
#
#   docker run --rm -e WINGBOX_SPEC=wingbox==X.Y.Z -e VERIFY=0 -v "$PWD/out":/work/out wingbox-php-benchmark
#       the same tests on a released version, results written to ./out/results, nothing compared
#
# Needs the network (GitHub and PyPI). No model and no key.
FROM php:8.4-cli

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates python3 \
    && rm -rf /var/lib/apt/lists/*

# The checkout under test: this commit. The build context is the repository root; only
# the files hatchling packages are copied, so the image holds no repository state.
COPY pyproject.toml README.md LICENSE CHANGELOG.md SKILL.md /src/
COPY src /src/src
COPY scripts /src/scripts
COPY references /src/references
# The benchmark scripts use the interval functions of benchmarks/impact/stats.py; the
# relative path they import is ../impact from the tools directory.
COPY benchmarks/php /bench
COPY benchmarks/impact/stats.py /impact/stats.py

COPY --from=ghcr.io/astral-sh/uv:0.6.6 /uv /uvx /usr/local/bin/

ENV PYTHONDONTWRITEBYTECODE=1 HOME=/work UV_LINK_MODE=copy WINGBOX_SPEC=/src
WORKDIR /work
ENTRYPOINT ["/bench/run.sh", "/work"]