# OntoIndex 3.1.9 (GitHub release asset, checked by oi_run.sh) next to Wingbox and Graphify, Linux.
# OntoIndex's analyze takes a native file lock that exists only on Linux, so the three tools are timed here.
FROM node:22-bookworm
ENV DEBIAN_FRONTEND=noninteractive SCARF_ANALYTICS=false DO_NOT_TRACK=1 NO_COLOR=1
RUN apt-get update -qq && apt-get install -y -qq python3 make g++ git curl ca-certificates time >/dev/null && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.9.7 /uv /usr/local/bin/uv
COPY ontoindex-3.1.9.tgz /opt/
RUN echo "8a6a0714f44d5ee330a3a2326c41db21bb14f4e8ad6bad11d24d6905443b76dd  /opt/ontoindex-3.1.9.tgz" | sha256sum -c -
# No lifecycle scripts. The two steps the postinstall would do are done by hand:
# the LadybugDB prebuilt binary is copied in place, the vendored tree-sitter runtime is compiled.
RUN mkdir -p /opt/oi && cd /opt/oi && npm init -y >/dev/null \
 && npm install --ignore-scripts --no-audit --no-fund /opt/ontoindex-3.1.9.tgz \
 && cp node_modules/@ladybugdb/core-linux-x64/lbugjs.node node_modules/@ladybugdb/core/lbugjs.node \
 && cd node_modules/ontoindex/vendor/tree-sitter && node /usr/local/lib/node_modules/npm/node_modules/node-gyp/bin/node-gyp.js rebuild --jobs=4 --nodedir=/usr/local >/dev/null \
 && ls build/Release/tree_sitter_runtime_binding.node
RUN printf '#!/bin/sh\nexec node /opt/oi/node_modules/ontoindex/dist/cli/index.js "$@"\n' > /usr/local/bin/ontoindex && chmod +x /usr/local/bin/ontoindex
RUN uv venv -q --python 3.12 /opt/venv_wb && uv pip install -q --python /opt/venv_wb/bin/python wingbox==0.2.0 \
 && uv venv -q --python 3.12 /opt/venv_gf && uv pip install -q --python /opt/venv_gf/bin/python graphifyy==0.9.110
ENV HOME=/home/bench GRAPHIFY_BACKEND=none
RUN mkdir -p /home/bench && ontoindex --version && /opt/venv_wb/bin/wingbox --version && /opt/venv_gf/bin/graphify --version
