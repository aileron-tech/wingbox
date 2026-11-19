"""The layer that the role of a file suggests is worked out when the graph is published and read by the queries
(issue #106): .wingbox/cache.layer-guess.json holds it for every distinct purpose of the graph, under a key of
the keyword table and of the matching. It is a cache of a pure function: a query gives the same answer without it."""
from __future__ import annotations

import contextlib
import io
import json
import os
import random
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import build_code_graph, code_graph_search, graph_update, layer_guess
from scripts.architecture.words import levenshtein

REPO = Path(__file__).resolve().parents[1]
FILE_NAME = "cache.layer-guess.json"

# One part in the layered style whose folders no rule names: every layer here is a guess from the role of a file.
LAYERED = {
    ".wingbox/config.yaml": "parts:\n  .:\n    style: layered\n",
    "shop/__init__.py": "",
    "shop/billing/__init__.py": "",
    "shop/billing/invoice_model.py": "class Invoice:\n    def __init__(self, order):\n        self.amount = 1\n",
    "shop/billing/invoice_repository.py": "from shop.billing.invoice_model import Invoice\n\n\ndef load(order):\n    return [Invoice(order).amount]\n",
    "shop/billing/invoice_service.py": "from shop.billing.invoice_repository import load\n\n\ndef total(order):\n    return sum(load(order))\n",
    "shop/billing/invoice_handler.py": "from shop.billing.invoice_service import total\n\n\ndef handle(order):\n    return total(order)\n",
    "shop/billing/helpers.py": "def cents(value):\n    return int(value * 100)\n",
    "shop/billing/invoice_router.py": "from shop.billing.invoice_handler import handle\n\n\ndef route(order):\n    return handle(order)\n",
}
NEW_FILE = "shop/billing/invoice_gateway.py"
NEW_PURPOSE = "gateway"
QUERIES = (
    ("impact", "shop/billing/invoice_model.py"),
    ("impact", "shop/billing/invoice_model.py", "--sexp"),
    ("impact", "shop/billing/helpers.py", "--sexp"),
    ("layers",),
    ("boundaries",),
)


def _silently(call, *arguments):
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return call(*arguments)


def _build(root: Path, *extra: str) -> int:
    return _silently(build_code_graph.main, ["--root", str(root), *extra])


def _query(root: Path, *argv: str) -> tuple[int, str, str]:
    """(exit status, stdout, stderr) of a query command, run as `wingbox <command>` runs it."""
    output, errors = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
        status = code_graph_search.main([*argv, "--map", str(root / ".wingbox" / "index.sexp"), "--root", str(root)])
    return status, output.getvalue(), errors.getvalue()


def _answers(root: Path) -> list[tuple[int, str, str]]:
    return [_query(root, *argv) for argv in QUERIES]


def _graph_purposes(root: Path) -> set[str]:
    """The distinct purposes of the published graph, read as the queries read them."""
    graph = code_graph_search.CodeGraph()
    loaded = graph.load(str(root / ".wingbox" / "index.sexp"), str(root))
    assert isinstance(loaded, code_graph_search.MapLoadOk), loaded
    return {node.purpose for node in graph.nodes.values()} - {""}


class LayerGuessFileTest(unittest.TestCase):
    def setUp(self) -> None:
        self._folder = tempfile.TemporaryDirectory()
        self.root = Path(os.path.realpath(self._folder.name)) / "project"
        self.root.mkdir()
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        for name, text in LAYERED.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        self.file = self.root / ".wingbox" / FILE_NAME
        code_graph_search._classify_layer_by_purpose.cache_clear()
        self.assertEqual(0, _build(self.root))

    def tearDown(self) -> None:
        self._folder.cleanup()

    def stored(self) -> dict:
        return json.loads(self.file.read_text(encoding="utf-8"))

    def test_a_build_stores_the_guess_of_every_purpose_of_the_graph(self) -> None:
        """/* REQ-CS-088: a build writes .wingbox/cache.layer-guess.json, by an atomic replacement, with the key
        of the keyword table and the matching and the layer of every distinct purpose of the published graph, each
        the layer the matcher gives; git ignores the file with the other caches and no temporary file is left. */"""
        stored = self.stored()
        self.assertEqual(["guesses", "key"], sorted(stored))
        self.assertEqual(layer_guess.guess_key(), stored["key"])
        purposes = _graph_purposes(self.root)
        self.assertEqual(purposes, set(stored["guesses"]))
        self.assertGreaterEqual(len(purposes), 6, purposes)
        for purpose, layer in stored["guesses"].items():
            with self.subTest(purpose=purpose):
                self.assertEqual(code_graph_search._classify_layer_by_purpose.__wrapped__(purpose), layer)
        self.assertNotEqual({"other"}, set(stored["guesses"].values()))
        ignored = subprocess.run(["git", "-C", str(self.root), "check-ignore", "-q", f".wingbox/{FILE_NAME}"])
        self.assertEqual(0, ignored.returncode)
        self.assertEqual([], [path.name for path in self.file.parent.iterdir() if path.name.endswith(".tmp")])
        with mock.patch.object(build_code_graph, "_atomic_write_text", wraps=build_code_graph._atomic_write_text) as written:
            self.file.unlink()
            self.assertEqual(0, _build(self.root))
        replaced = [os.path.normcase(os.path.realpath(call.args[0])) for call in written.call_args_list]
        self.assertIn(os.path.normcase(os.path.realpath(self.file)), replaced)
        self.assertEqual(stored, self.stored())

    def test_a_partial_update_adds_the_new_purpose_and_works_out_nothing_else(self) -> None:
        """/* REQ-CS-089: after a file with a purpose the graph did not have is added, the partial update leaves
        the file complete (every purpose of the new graph, the new one among them), the same bytes a full build
        writes, and it runs the matcher for the new purpose only. */"""
        self.assertNotIn(NEW_PURPOSE, self.stored()["guesses"])
        (self.root / NEW_FILE).write_text("def send(order):\n    return order\n", encoding="utf-8")
        with mock.patch.object(layer_guess, "classify", wraps=layer_guess.classify) as matcher:
            updated = _silently(
                graph_update.update_graph, str(self.root), [NEW_FILE], str(self.root / ".wingbox" / "graph-fragments.json"))
        self.assertIsInstance(updated, graph_update.Updated, updated)
        self.assertEqual([NEW_PURPOSE], [call.args[0] for call in matcher.call_args_list])
        stored = self.stored()
        self.assertEqual(_graph_purposes(self.root), set(stored["guesses"]))
        self.assertEqual("infra", stored["guesses"][NEW_PURPOSE])
        patched = self.file.read_bytes()
        self.assertEqual(0, _build(self.root, "--full"))
        self.assertEqual(patched, self.file.read_bytes())

    def test_a_rebuild_keeps_what_is_stored_and_a_full_build_works_it_out_again(self) -> None:
        """/* REQ-CS-090: a build of an unchanged tree runs the matcher for no purpose the file already holds
        under the current key; `--full` works out every purpose again, so it also repairs a stored layer. */"""
        with mock.patch.object(layer_guess, "classify", wraps=layer_guess.classify) as matcher:
            self.assertEqual(0, _build(self.root))
        self.assertEqual(0, matcher.call_count)
        good = self.stored()
        spoiled = {"key": good["key"], "guesses": {**good["guesses"], "repository": "ui"}}
        self.file.write_text(json.dumps(spoiled), encoding="utf-8")
        with mock.patch.object(layer_guess, "classify", wraps=layer_guess.classify) as matcher:
            self.assertEqual(0, _build(self.root, "--full"))
        self.assertEqual(sorted(good["guesses"]), sorted(call.args[0] for call in matcher.call_args_list))
        self.assertEqual(good, self.stored())

    def test_queries_answer_the_same_from_the_file_without_matching(self) -> None:
        """/* REQ-CS-091: with the file present, `impact` (text and --sexp), `layers` and `boundaries` of a layered
        project print byte for byte what they print with the file deleted, and the fuzzy matcher is not called for
        any purpose; without the file it is called, so the answers do rest on the guess. */"""
        with mock.patch.object(
            code_graph_search, "_classify_layer_by_purpose", side_effect=AssertionError("the matcher ran"),
        ) as matcher:
            with_file = _answers(self.root)
        self.assertEqual(0, matcher.call_count)
        self.file.unlink()
        with mock.patch.object(
            code_graph_search, "_classify_layer_by_purpose", wraps=code_graph_search._classify_layer_by_purpose,
        ) as matcher:
            without_file = _answers(self.root)
        self.assertGreater(matcher.call_count, 0)
        self.assertEqual(without_file, with_file)
        self.assertEqual([(0, "")] * len(QUERIES), [(status, errors) for status, _output, errors in with_file])
        layers = with_file[QUERIES.index(("layers",))][1]
        self.assertIn(':rule "role repository -> infra (guess)" :match role', layers)
        self.assertIn(':rule "role data model -> domain (guess)" :match role', layers)

    def test_a_file_that_cannot_be_used_is_ignored_in_silence(self) -> None:
        """/* REQ-CS-092: a file under another key (even one whose layers are all wrong), a truncated file, text
        that is not JSON, JSON of another shape, a layer no purpose can have, bytes that are not UTF-8 and a folder
        in place of the file are ignored: no line on stderr, exit status 0, the same answers; a file with the
        current key that lacks some purposes is used for the ones it holds and the others are matched. */"""
        good_text = self.file.read_text(encoding="utf-8")
        good = json.loads(good_text)
        self.file.unlink()
        expected = _answers(self.root)
        self.assertEqual([(0, "")] * len(QUERIES), [(status, errors) for status, _output, errors in expected])
        wrong = {purpose: "ui" for purpose in good["guesses"]}
        unusable = {
            "another key": json.dumps({"key": "0" * len(good["key"]), "guesses": wrong}).encode(),
            "truncated": good_text[: len(good_text) // 2].encode(),
            "empty": b"",
            "not JSON": b"key: value\n",
            "a list": b"[]\n",
            "guesses as a list": json.dumps({"key": good["key"], "guesses": sorted(good["guesses"])}).encode(),
            "no key": json.dumps({"guesses": wrong}).encode(),
            "a layer of no purpose": json.dumps({"key": good["key"], "guesses": {p: "kitchen" for p in wrong}}).encode(),
            "a number for a layer": json.dumps({"key": good["key"], "guesses": {p: 7 for p in wrong}}).encode(),
            "not UTF-8": b"\xff\xfe{\x00",
        }
        for name, content in unusable.items():
            with self.subTest(file=name):
                self.file.write_bytes(content)
                self.assertEqual(expected, _answers(self.root))
                self.assertEqual(content, self.file.read_bytes())
        self.file.unlink()
        self.file.mkdir()
        self.assertEqual(expected, _answers(self.root))
        self.file.rmdir()

        held = {purpose: layer for purpose, layer in good["guesses"].items() if purpose != "repository"}
        self.file.write_text(json.dumps({"key": good["key"], "guesses": held}), encoding="utf-8")
        code_graph_search._classify_layer_by_purpose.cache_clear()
        with mock.patch.object(
            code_graph_search, "_classify_layer_by_purpose", wraps=code_graph_search._classify_layer_by_purpose,
        ) as matcher:
            self.assertEqual(expected, _answers(self.root))
        self.assertEqual({"repository"}, {call.args[0] for call in matcher.call_args_list})

    def test_a_query_never_writes_the_file(self) -> None:
        """/* REQ-CS-093: no query command creates, completes or repairs the file: after `impact`, `layers`,
        `boundaries`, `analyze` and `quick` a deleted file is still absent, a file under another key keeps its
        bytes, and .wingbox/ holds the same files as before. */"""
        commands = QUERIES + (("analyze", "shop/billing/invoice_service.py"), ("quick", "invoice"))

        def folder() -> list[str]:
            return sorted(path.name for path in self.file.parent.iterdir())

        self.file.unlink()
        before = folder()
        for argv in commands:
            _query(self.root, *argv)
        self.assertFalse(self.file.exists())
        self.assertEqual(before, folder())
        foreign = b'{"key":"another","guesses":{"service":"ui"}}\n'
        self.file.write_bytes(foreign)
        for argv in commands:
            _query(self.root, *argv)
        self.assertEqual(foreign, self.file.read_bytes())
        self.assertEqual(sorted(before + [FILE_NAME]), folder())

    def test_the_key_names_the_table_and_the_matching(self) -> None:
        """/* REQ-CS-094: the key is the same for the same keyword table and algorithm version and differs when a
        keyword changes layer, a keyword is added, the order of the table changes or the version changes; after
        such a change the stored file is not read (the matcher runs) and the next build writes it under the new
        key. */"""
        table = dict(layer_guess.PURPOSE_TO_LAYER)
        version = layer_guess.ALGORITHM_VERSION
        key = layer_guess.guess_key()
        self.assertEqual(key, layer_guess.guess_key(table, version))
        self.assertEqual(key, self.stored()["key"])
        moved = {**table, "repository": "service"}
        added = {**table, "ledger": "infra"}
        reordered = dict(reversed(list(table.items())))
        others = {
            layer_guess.guess_key(moved, version), layer_guess.guess_key(added, version),
            layer_guess.guess_key(reordered, version), layer_guess.guess_key(table, version + 1),
        }
        self.assertEqual(4, len(others))
        self.assertNotIn(key, others)

        expected = _answers(self.root)
        with mock.patch.object(layer_guess, "ALGORITHM_VERSION", version + 1):
            code_graph_search._classify_layer_by_purpose.cache_clear()
            with mock.patch.object(
                code_graph_search, "_classify_layer_by_purpose", wraps=code_graph_search._classify_layer_by_purpose,
            ) as matcher:
                self.assertEqual(expected, _answers(self.root))
            self.assertGreater(matcher.call_count, 0)
            self.assertEqual(0, _build(self.root))
            self.assertEqual(layer_guess.guess_key(table, version + 1), self.stored()["key"])
        self.assertEqual(_graph_purposes(self.root), set(self.stored()["guesses"]))

    def test_the_pure_part_is_shared_without_the_search_module(self) -> None:
        """/* REQ-CS-095: the table, the matching, the key and the file format live in scripts/layer_guess.py,
        which the builder imports without the search module (no cycle, nothing more at the start of a build);
        the search module keeps its names for them, and its guess is the guess of that module. */"""
        self.assertIs(layer_guess.PURPOSE_TO_LAYER, code_graph_search._PURPOSE_TO_LAYER)
        self.assertEqual(FILE_NAME, layer_guess.FILE_NAME)
        for purpose in ("request handlar", "repositry", "state store", "general module", "", "midleware; routing"):
            with self.subTest(purpose=purpose):
                self.assertEqual(layer_guess.classify(purpose), code_graph_search._classify_layer_by_purpose(purpose))
        code = (
            "import sys; sys.path.insert(0, sys.argv[1]); import build_code_graph, graph_update, layer_guess; "
            "print(sorted(name for name in sys.modules if 'code_graph_search' in name))"
        )
        done = subprocess.run(
            [sys.executable, "-c", code, str(REPO / "scripts")], capture_output=True, text=True, check=False,
        )
        self.assertEqual((0, "[]"), (done.returncode, done.stdout.strip()), done.stderr)


def reference_guess(purpose: str) -> str:
    """The guess as it was written before any shortcut: every word of the purpose against every word of every
    keyword, letter by letter, the first closest pair deciding."""
    table = layer_guess.PURPOSE_TO_LAYER
    if not purpose or purpose == "general module":
        return "other"
    lowered = purpose.lower()
    for keyword, layer in table.items():
        if keyword.lower() == lowered:
            return layer
    for keyword, layer in table.items():
        if keyword.lower() in lowered:
            return layer
    purpose_words = re.split(r"[\s;,/]+", lowered)
    best_layer, best_distance = "other", 999
    for keyword, layer in table.items():
        for keyword_word in keyword.lower().split():
            if len(keyword_word) < 4:
                continue
            threshold = 2 if len(keyword_word) >= 5 else 1
            for word in purpose_words:
                if len(word) < 4:
                    continue
                distance = levenshtein(word, keyword_word)
                if distance <= threshold and distance < best_distance:
                    best_distance, best_layer = distance, layer
    return best_layer


def generated_purposes(count: int) -> list[str]:
    """Purposes as a build writes them (roles and sentences joined by "; ") with what the fuzzy step is for:
    keyword words with one to three letters changed, dropped or added, among ordinary words."""
    source = random.Random(106)
    keyword_words = sorted({word for keyword in layer_guess.PURPOSE_TO_LAYER for word in keyword.lower().split()})
    plain = ["the", "reads", "files", "order", "total", "of", "every", "invoice", "and", "writes", "graph", "node", "cart"]
    letters = "abcdefghijklmnopqrstuvwxyz"

    def changed(word: str) -> str:
        for _ in range(source.randint(0, 3)):
            at = source.randrange(len(word) + 1)
            action = source.choice(("drop", "add", "swap"))
            if action == "drop" and len(word) > 1:
                word = word[:at] + word[at + 1:]
            elif action == "add":
                word = word[:at] + source.choice(letters) + word[at:]
            elif at < len(word):
                word = word[:at] + source.choice(letters) + word[at + 1:]
        return word

    purposes = []
    for _ in range(count):
        words = [
            changed(source.choice(keyword_words)) if source.random() < 0.4 else source.choice(plain)
            for _ in range(source.randint(1, 9))
        ]
        text = words[0]
        for word in words[1:]:
            text += source.choice((" ", " ", " ", "; ", ", ", "/")) + word
        purposes.append(text.capitalize() if source.random() < 0.2 else text)
    return purposes


class MatcherTest(unittest.TestCase):
    def test_the_guess_is_the_one_of_the_full_comparison(self) -> None:
        """/* REQ-CS-096: for 600 generated purposes (keyword words with up to three letters changed among
        ordinary words, every separator a purpose has), for every keyword and for the purposes of the earlier
        tests, the guess is the one of the comparison written out in full (every word against every keyword
        word, no length check, nothing remembered), asked in any order and asked twice. */"""
        purposes = generated_purposes(600) + list(layer_guess.PURPOSE_TO_LAYER) + [
            "request handlar", "contoller of things", "repositry", "servise worker", "data mappr", "general module",
            "utility helpers", "state store", "nothing like a layer word here", "midleware; routing", "validaton", "",
        ]
        expected = {purpose: reference_guess(purpose) for purpose in purposes}
        self.assertGreater(len(set(expected.values())), 8)
        fuzzy = [p for p in purposes if not any(keyword.lower() in p.lower() for keyword in layer_guess.PURPOSE_TO_LAYER)]
        self.assertGreater(len([p for p in fuzzy if expected[p] != "other"]), 100)
        layer_guess._closest.cache_clear()
        different = [p for p in purposes if layer_guess.classify(p) != expected[p]]
        self.assertEqual([], different[:5], f"{len(different)} of {len(purposes)} purposes guessed differently")
        different = [p for p in reversed(purposes) if layer_guess.classify(p) != expected[p]]
        self.assertEqual([], different[:5])
        code_graph_search._classify_layer_by_purpose.cache_clear()
        different = [p for p in purposes if code_graph_search._classify_layer_by_purpose(p) != expected[p]]
        self.assertEqual([], different[:5])
        self.assertEqual(expected, {**expected, **layer_guess.guesses(p for p in purposes if p)})


if __name__ == "__main__":
    unittest.main()
