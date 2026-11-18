# Wingbox in a container: nothing to install on the machine that holds the code.
#
#   docker build -t wingbox .
#   docker run --rm --network none -v "$PWD":/repo wingbox
#       builds the code graph of the mounted repository and writes
#       .wingbox/ (index.sexp, REPORT.md, map.html) into it
#   docker run --rm --network none -v "$PWD":/repo wingbox impact src/app.ts
#       any other wingbox command
#
# The container needs no network: --network none proves that nothing leaves the machine.
# On Linux add --user "$(id -u):$(id -g)" so that the written files belong to you.
FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/* \
    && git config --system --add safe.directory '*'

COPY pyproject.toml README.md LICENSE SKILL.md /src/
COPY references /src/references
COPY src /src/src
COPY scripts /src/scripts
# Every language parser is in the image (the extra `all`): a container without network cannot add one.
RUN pip install --no-cache-dir "/src[all]" && rm -rf /src

ENV PYTHONDONTWRITEBYTECODE=1 HOME=/tmp
WORKDIR /repo
ENTRYPOINT ["wingbox"]
CMD ["build", "."]
