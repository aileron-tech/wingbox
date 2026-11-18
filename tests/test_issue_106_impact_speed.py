"""An `impact` call does no work twice: issue #106 (most of a call went into repeated fuzzy matching, a
character-by-character reader of the graph file and the same folder resolved once per file)."""
from __future__ import annotations

import os
import random
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import code_graph_search, portable
from tests.test_impact_evidence import RUST_FILES, TS_FILES, impact, loaded_graph, row

ALPHABET = ['a', 'B', '7', ':', '/', '.', '-', '_', ' ', ' ', '"', '(', ')', '\\', '*', ',', ';', "'", '\t']


def random_line(source: random.Random) -> str:
    """A fact line as the graph file holds them, with everything a reader can meet inside: quoted values,
    escapes, lists, lists in lists, brackets inside quotes, stray brackets, unfinished quotes."""
    body = "".join(source.choice(ALPHABET) for _ in range(source.randint(0, 40)))
    return f"({body})"


class TokenizerTest(unittest.TestCase):
    def test_reads_every_line_as_the_reference_reader(self) -> None:
        """/* REQ-CS-078: the reader of a graph line returns exactly the tokens of the reference reader (the
        character-by-character one) for every line of a built graph and for 20,000 generated lines with quotes,
        escapes, nested lists, brackets inside quotes, stray brackets and unfinished quotes. */"""
        with tempfile.TemporaryDirectory() as folder:
            _graph, rust_text = loaded_graph(Path(folder) / "rust", RUST_FILES, "rust")
            _graph, ts_text = loaded_graph(Path(folder) / "ts", TS_FILES, "typescript")
        source = random.Random(106)
        lines = rust_text.splitlines() + ts_text.splitlines() + [random_line(source) for _ in range(20000)]
        lines += ['(edge imports a b (:use A B))', '(edge imports a b (:glob "super" A B :of 5))', '(node a :purpose "say \\"hi\\" (now)")',
                  '(x "unfinished', '(x "unfinished)', '(x (y "z)" w) v)', '(x (y (z)) w)', '(x y) z)', '(a"b c"d "e")', '(a" "b)', '()', '( )']
        different = [line for line in lines if code_graph_search.tokenize_reference(line) != code_graph_search.tokenize(line)]
        self.assertEqual([], different[:5], f"{len(different)} of {len(lines)} lines read differently")

    def test_takes_the_short_way_for_the_lines_of_a_graph(self) -> None:
        """/* REQ-CS-079: reading a built graph, the character-by-character reader runs only for the lines that
        need it (a backslash, a list holding a quote or another list): fewer than 1 line in 10 of a graph whose
        import edges carry name lists. */"""
        with tempfile.TemporaryDirectory() as folder:
            _graph, text = loaded_graph(Path(folder), RUST_FILES, "rust")
        facts = [line for line in text.splitlines() if line.startswith("(")]
        with mock.patch.object(code_graph_search, "_tokens_one_by_one", wraps=code_graph_search._tokens_one_by_one) as slow:
            for line in facts:
                code_graph_search.tokenize(line)
        self.assertGreater(len(facts), 30)
        self.assertLess(slow.call_count * 10, len(facts), f"{slow.call_count} of {len(facts)} lines took the slow reader")


class LayerGuessTest(unittest.TestCase):
    PURPOSES = ("request handlar", "contoller of things", "repositry", "servise worker", "data mappr", "general module",
                "utility helpers", "state store", "nothing like a layer word here", "midleware; routing", "validaton")

    def test_guess_is_the_same_and_counted_once(self) -> None:
        """/* REQ-CS-080: the layer guessed from a purpose is the one the full comparison gives (every word against
        every keyword, no shortcut), and guessing the same purpose again compares no words. */"""
        from scripts.architecture.words import levenshtein

        def full_guess(purpose: str) -> str:
            # The comparison as written before the shortcuts: no cache, no length check.
            with mock.patch.object(code_graph_search, "_within", lambda word, keyword, threshold: levenshtein(word, keyword)):
                return code_graph_search._classify_layer_by_purpose.__wrapped__(purpose)

        for purpose in self.PURPOSES + tuple(code_graph_search._PURPOSE_TO_LAYER):
            with self.subTest(purpose=purpose):
                self.assertEqual(full_guess(purpose), code_graph_search._classify_layer_by_purpose(purpose))
        with mock.patch.object(code_graph_search, "_levenshtein", wraps=code_graph_search._levenshtein) as compared:
            for purpose in self.PURPOSES:
                code_graph_search._classify_layer_by_purpose(purpose)
        self.assertEqual(0, compared.call_count)

    def test_words_of_too_different_length_are_not_compared(self) -> None:
        """/* REQ-CS-081: two words whose lengths differ by more than the allowed distance are never compared
        letter by letter: their distance is already above it. */"""
        with mock.patch.object(code_graph_search, "_levenshtein", wraps=code_graph_search._levenshtein) as compared:
            self.assertGreater(code_graph_search._within("ab", "abcdefgh", 2), 2)
            self.assertEqual(0, compared.call_count)
            self.assertEqual(1, code_graph_search._within("handlar", "handler", 2))
            self.assertEqual(1, compared.call_count)


class ImpactWorkTest(unittest.TestCase):
    def test_one_call_resolves_the_root_once_and_each_file_once(self) -> None:
        """/* REQ-CS-082: one `impact` call resolves the links of the project folder once and of each file at most
        once, however many rows, read-first lines and advice items name the file; its text is unchanged. */"""
        with tempfile.TemporaryDirectory() as folder:
            graph, _text = loaded_graph(Path(folder), TS_FILES, "typescript")
            before = impact(graph, "src/core/util.ts")
            portable.forget_resolved_roots()
            fresh, _text = loaded_graph(Path(folder), TS_FILES, "typescript")
            with mock.patch.object(portable.os.path, "realpath", wraps=os.path.realpath) as resolved:
                after = impact(fresh, "src/core/util.ts")
                again = impact(fresh, "src/core/util.ts")
        self.assertEqual(before, after)
        self.assertEqual(before, again)
        self.assertEqual("through src/billing/invoice.ts (or 1 other file): imports it", row(after, "src/api/handler.ts"))
        self.assertLessEqual(resolved.call_count, len(fresh.nodes) + 1, f"{resolved.call_count} link resolutions for {len(fresh.nodes)} files")


if __name__ == "__main__":
    unittest.main()
