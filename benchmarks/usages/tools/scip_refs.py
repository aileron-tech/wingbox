"""Usages of a symbol read from a SCIP index (the format of Sourcegraph's precise code navigation).

Usage: python3 -I scip_refs.py <index.scip> <questions.json> <out.json>

For each question (file, name, line, column of the name in its definition) the definition occurrence is the
occurrence in that document, on that line, with the Definition role, whose range starts at the column, or
else whose symbol ends with the name. Its usages are all occurrences of the same symbol in other documents.
A question whose definition is not in the index gets "found": false and no files.

The protobuf is decoded here with the standard library, following scip.proto: Index.documents = 2;
Document.relative_path = 1, occurrences = 2; Occurrence.range = 1 (packed int32), symbol = 2, symbol_roles = 3.
"""
from __future__ import annotations

import collections
import json
import re
import sys
import time

DEFINITION = 1


def varint(data: bytes, at: int) -> tuple[int, int]:
    value, shift = 0, 0
    while True:
        byte = data[at]
        at += 1
        value |= (byte & 0x7F) << shift
        if byte < 0x80:
            return value, at
        shift += 7


def fields(data: bytes):
    """(field number, wire type, value) of one message; value is an int or a bytes slice."""
    at, end = 0, len(data)
    while at < end:
        key, at = varint(data, at)
        number, wire = key >> 3, key & 7
        if wire == 0:
            value, at = varint(data, at)
        elif wire == 2:
            length, at = varint(data, at)
            value, at = data[at:at + length], at + length
        elif wire == 1:
            value, at = data[at:at + 8], at + 8
        elif wire == 5:
            value, at = data[at:at + 4], at + 4
        else:
            raise ValueError(f"wire type {wire}")
        yield number, wire, value


def packed(data: bytes) -> list[int]:
    values, at = [], 0
    while at < len(data):
        value, at = varint(data, at)
        values.append(value)
    return values


def read_index(path: str) -> dict[str, list[tuple[int, int, str, int]]]:
    """relative path -> [(start line, start column, symbol, roles)]."""
    documents: dict[str, list[tuple[int, int, str, int]]] = {}
    data = open(path, "rb").read()
    for number, _wire, value in fields(data):
        if number != 2:
            continue
        rel, occurrences = "", []
        for d_number, _d_wire, d_value in fields(value):
            if d_number == 1:
                rel = bytes(d_value).decode()
            elif d_number == 2:
                span, symbol, roles = [], "", 0
                for o_number, o_wire, o_value in fields(d_value):
                    if o_number == 1:
                        span = packed(o_value) if o_wire == 2 else span + [o_value]
                    elif o_number == 2:
                        symbol = bytes(o_value).decode()
                    elif o_number == 3:
                        roles = o_value
                if span and symbol:
                    occurrences.append((span[0], span[1], symbol, roles))
        documents.setdefault(rel, []).extend(occurrences)
    return documents


def ends_with_name(symbol: str, name: str) -> bool:
    return re.search(r"[/.#:`]" + re.escape(name) + r"`?[.#:()]*$", symbol) is not None or symbol.endswith(f"/{name}.")


def main() -> None:
    index_path, questions_path, out_path = sys.argv[1:4]
    started = time.perf_counter()
    documents = read_index(index_path)
    by_symbol: dict[str, set[str]] = collections.defaultdict(set)
    for rel, occurrences in documents.items():
        for _line, _col, symbol, _roles in occurrences:
            if not symbol.startswith("local "):
                by_symbol[symbol].add(rel)
    load_seconds = time.perf_counter() - started
    rows = []
    for question in json.load(open(questions_path)):
        began = time.perf_counter()
        on_line = [(col, symbol) for line, col, symbol, roles in documents.get(question["file"], [])
                   if line == question["line"] - 1 and roles & DEFINITION]
        chosen = [symbol for col, symbol in on_line if col == question["col"]]
        chosen = chosen or [symbol for _col, symbol in on_line if ends_with_name(symbol, question["name"])]
        files: set[str] = set()
        for symbol in chosen[:1]:
            files = by_symbol[symbol] - {question["file"]}
        rows.append({"id": question["id"], "found": bool(chosen), "symbol": chosen[0] if chosen else None,
                     "files": sorted(files), "seconds": time.perf_counter() - began})
    json.dump({"documents": len(documents), "load_seconds": load_seconds, "rows": rows}, open(out_path, "w"), indent=1)
    print(f"{len(documents)} documents, {sum(r['found'] for r in rows)} of {len(rows)} definitions found")


if __name__ == "__main__":
    main()
