"""`wingbox place`: where new code goes, by the placement rules of the style."""
import unittest

from scripts.architecture.library import load_library
from scripts.architecture.place import choose_placement

LIBRARY = load_library()


class PlaceTest(unittest.TestCase):
    def test_the_words_of_the_request_choose_the_placement_of_the_style(self) -> None:
        """/* REQ-ARCH-031: a request is placed by the [[placement]] entry of the part's style whose words it shares most (whole words, plural folded), with the layers that layer may import; unrelated words place nothing. */"""
        layered = LIBRARY.style("layered")
        self.assertEqual("infra", choose_placement(layered, "a client for the payment database").layer)
        self.assertEqual("api", choose_placement(layered, "new HTTP endpoint for orders").layer)
        self.assertEqual("domain", choose_placement(layered, "an Order entity with a schema").layer)
        self.assertEqual(("infra", "domain", "shared", "config"), choose_placement(layered, "background job").may_import)
        hexagonal = LIBRARY.style("hexagonal")
        self.assertEqual("driven-adapter", choose_placement(hexagonal, "repository that stores accounts").layer)
        self.assertIsNone(choose_placement(layered, "banana giraffe"))


    def test_layer_words_and_file_patterns_count_and_ties_go_to_the_richer_match(self) -> None:
        """/* REQ-ARCH-040: besides the placement texts, a layer's name, folder words and file-name patterns count (plural -ies folded, a word may be the start of a folder word: utility/utils): NestJS repository class, DTO, domain entity, notification service, background job; hexagonal domain entity and REST endpoint; route-tree utility and shared React component. A placement without may_import says not stated. */"""
        nest = LIBRARY.style("nestjs")
        for request, layer in (("a repository class for albums", "infra"), ("a DTO for the album request", "domain"),
                               ("a domain entity Album", "domain"), ("a notification service", "service"),
                               ("a background job that cleans files", "service"), ("several entities", "domain")):
            self.assertEqual(layer, choose_placement(nest, request).layer, request)
        hexagonal = LIBRARY.style("hexagonal")
        self.assertEqual("domain", choose_placement(hexagonal, "a domain entity").layer)
        self.assertEqual("driving-adapter", choose_placement(hexagonal, "a REST endpoint").layer)
        route = LIBRARY.style("route-tree")
        self.assertEqual("shared", choose_placement(route, "a date utility").layer)
        self.assertEqual("ui", choose_placement(route, "a shared React component").layer)
        self.assertIsNone(choose_placement(route, "a date utility").may_import)


if __name__ == "__main__":
    unittest.main()
