"""Build the word-vector table of the architecture styles. Run by the maintainer, never by a user.

    python3 -I benchmarks/architecture/build_vectors.py --work /tmp/wingbox-vectors

Source: GloVe, Wikipedia 2014 + Gigaword 5, 6B tokens, uncased, 100 dimensions
(Pennington, Socher and Manning, 2014), published by the Stanford NLP Group under
the Open Data Commons Public Domain Dedication and License (PDDL 1.0), read from
the gensim copy at huggingface.co/fse/glove-wiki-gigaword-100 (revision pinned
below). The script downloads the vocabulary file (21 MB) and, with HTTP range
requests, only the rows of the vectors it keeps (about 13 MB of the 160 MB file).

The gensim vocabulary file is a pickle. It is never unpickled: the script reads
its opcodes with pickletools.genops, which runs no code, and takes each word and
its row index from them.

Kept words: the TOP_WORDS most frequent purely alphabetic words, plus every word
of the style files in scripts/architecture/styles/ and of EXTRA_WORDS that GloVe knows. Each vector is
scaled to unit length and quantised to signed bytes (round(127 * x / max|x|)).
Output in scripts/architecture/vectors/: words.txt (one word per line, row order), glove100.i8 (rows of
100 signed bytes) and manifest.json (source, licence, sizes, digests).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import pickletools
import re
import struct
import sys
import tomllib
import urllib.request

REPOSITORY = "fse/glove-wiki-gigaword-100"
REVISION = "main"
BASE_URL = f"https://huggingface.co/{REPOSITORY}/resolve/{REVISION}/"
MODEL_FILE = "glove-wiki-gigaword-100.model"
VECTORS_FILE = "glove-wiki-gigaword-100.model.vectors.npy"
DIMENSIONS = 100
NPY_HEADER = 128
TOP_WORDS = 30000
# The table ships in the package; this script, which uses the network, stays outside it.
PACKAGE = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, os.pardir, "scripts", "architecture")
STYLES = os.path.join(PACKAGE, "styles")
OUTPUT = os.path.join(PACKAGE, "vectors")
# Architecture words that are not folder names of a style file but are common in code bases.
EXTRA_WORDS = (
    "persistence storage dal adapter adapters gateway gateways handler handlers usecase usecases "
    "interactor interactors presenter presenters viewmodel viewmodels repository repositories "
    "controller controllers middleware endpoint endpoints resolver resolvers serializer serializers "
    "validator validators mapper mappers dto entity entities aggregate aggregates projection projections "
    "subscriber subscribers listener listeners consumer consumers producer producers publisher "
    "scheduler worker workers job jobs task tasks queue queues cache broker messaging orm sql "
    "migration migrations seed seeds fixture fixtures mock mocks stub stubs widget widgets screen screens "
    "page pages layout layouts component components hook hooks store stores reducer reducers "
    "selector selectors saga sagas effect effects action actions router routes route view views "
    "template templates partial partials helper helpers util utils utility utilities common shared "
    "core kernel domain model models schema schemas contract contracts interface interfaces port ports "
    "infrastructure infra platform framework frameworks driver drivers client clients provider providers "
    "plugin plugins extension extensions module modules feature features service services manager "
    "managers facade api rest http rpc grpc graphql web ui frontend backend server cli command commands "
    "query queries event events notification notifications mailer mailers config configuration settings "
    "constants types enums errors exceptions logging logger auth authentication security policy policies"
).split()


def _download(name: str, work: str) -> str:
    path = os.path.join(work, name)
    if not os.path.exists(path):
        print(f"downloading {name}", file=sys.stderr)
        urllib.request.urlretrieve(BASE_URL + name, path)
    return path


def _range(name: str, start: int, length: int) -> bytes:
    request = urllib.request.Request(BASE_URL + name, headers={"Range": f"bytes={start}-{start + length - 1}"})
    with urllib.request.urlopen(request) as response:
        data = response.read()
    if len(data) != length:
        raise RuntimeError(f"range {start}+{length} of {name} returned {len(data)} bytes")
    return data


def read_vocabulary(model_path: str) -> dict[str, int]:
    """Word -> row index, from the opcodes of the gensim pickle (no code is run)."""
    with open(model_path, "rb") as handle:
        data = handle.read()
    words: dict[str, int] = {}
    pending: str | None = None
    ints: list[int] = []
    for opcode, argument, _ in pickletools.genops(data):
        name = opcode.name
        if name in ("BINUNICODE", "SHORT_BINUNICODE", "UNICODE", "BINUNICODE8"):
            if argument in ("count", "index"):
                continue
            pending, ints = argument, []
        elif name in ("BININT", "BININT1", "BININT2", "LONG1") and pending is not None:
            ints.append(int(argument))
            if len(ints) == 2:
                words.setdefault(pending, ints[1])
                pending = None
    return words


def style_words() -> set[str]:
    found: set[str] = set()

    def walk(value: object) -> None:
        if isinstance(value, str):
            for word in re.findall(r"[a-z]+", re.sub(r"([a-z])([A-Z])", r"\1 \2", value).lower()):
                if len(word) >= 2:
                    found.add(word)
        elif isinstance(value, list):
            for item in value:
                walk(item)
        elif isinstance(value, dict):
            for key, item in value.items():
                if key in ("names", "folders", "files", "id", "layer", "from", "to", "under"):
                    walk(item)
                elif isinstance(item, (dict, list)):
                    walk(item)

    for name in sorted(os.listdir(STYLES)):
        if name.endswith(".toml"):
            with open(os.path.join(STYLES, name), "rb") as handle:
                walk(tomllib.load(handle))
    return found | set(EXTRA_WORDS)


def quantise(vector: tuple[float, ...]) -> bytes:
    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    unit = [value / norm for value in vector]
    peak = max(abs(value) for value in unit) or 1.0
    return struct.pack(f"{DIMENSIONS}b", *(max(-127, min(127, round(127 * value / peak))) for value in unit))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--work", required=True, help="folder for the downloaded vocabulary file")
    work = parser.parse_args().work
    os.makedirs(work, exist_ok=True)
    model_path = _download(MODEL_FILE, work)
    vocabulary = read_vocabulary(model_path)
    by_index = sorted(vocabulary.items(), key=lambda item: item[1])
    top = [word for word, _ in by_index if re.fullmatch(r"[a-z]{2,}", word)][:TOP_WORDS]
    extra = sorted(word for word in style_words() if word in vocabulary and word not in set(top))
    missing = sorted(word for word in style_words() if word not in vocabulary)
    kept = top + extra
    row_bytes = DIMENSIONS * 4
    last_top_row = max(vocabulary[word] for word in top)
    block = _range(VECTORS_FILE, NPY_HEADER, (last_top_row + 1) * row_bytes)
    rows: list[bytes] = []
    for word in kept:
        index = vocabulary[word]
        raw = (
            block[index * row_bytes:(index + 1) * row_bytes]
            if index <= last_top_row
            else _range(VECTORS_FILE, NPY_HEADER + index * row_bytes, row_bytes)
        )
        rows.append(quantise(struct.unpack(f"<{DIMENSIONS}f", raw)))
    os.makedirs(OUTPUT, exist_ok=True)
    table = b"".join(rows)
    words_text = "\n".join(kept) + "\n"
    with open(os.path.join(OUTPUT, "glove100.i8"), "wb") as handle:
        handle.write(table)
    with open(os.path.join(OUTPUT, "words.txt"), "w", encoding="ascii") as handle:
        handle.write(words_text)
    with open(model_path, "rb") as handle:
        model_digest = hashlib.sha256(handle.read()).hexdigest()
    manifest = {
        "model": "GloVe 6B (Wikipedia 2014 + Gigaword 5), 100 dimensions, uncased",
        "authors": "Jeffrey Pennington, Richard Socher, Christopher D. Manning (Stanford NLP Group), 2014",
        "licence": "Open Data Commons Public Domain Dedication and License (PDDL) 1.0",
        "licence_url": "https://opendatacommons.org/licenses/pddl/1-0/",
        "source": f"https://huggingface.co/{REPOSITORY} ({MODEL_FILE}, {VECTORS_FILE})",
        "source_model_sha256": model_digest,
        "builder": "benchmarks/architecture/build_vectors.py",
        "dimensions": DIMENSIONS,
        "words": len(kept),
        "top_words": len(top),
        "style_words_added": len(extra),
        "style_words_unknown_to_glove": missing,
        "quantisation": "unit length, then round(127 * x / max|x|) per row, signed bytes",
        "table_sha256": hashlib.sha256(table).hexdigest(),
        "words_sha256": hashlib.sha256(words_text.encode("ascii")).hexdigest(),
    }
    with open(os.path.join(OUTPUT, "manifest.json"), "w", encoding="ascii") as handle:
        json.dump(manifest, handle, indent=2, sort_keys=True)
        handle.write("\n")
    print(f"{len(kept)} words ({len(top)} frequent, {len(extra)} from the styles), {len(table)} bytes", file=sys.stderr)
    print(f"unknown to GloVe: {', '.join(missing)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
