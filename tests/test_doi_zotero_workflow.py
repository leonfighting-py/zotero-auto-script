import unittest

from citation_pipeline.doi_lookup import parse_citation_text
from citation_pipeline.doi_zotero_workflow import import_rows_with_events, process_citations, process_citations_with_events


class _FakeLookupResult:
    def __init__(self, doi: str, score: float | None = 55.0, warning: str | None = None):
        self.doi = doi
        self.score = score
        self.warning = warning
        self.title = "Crossref Title"
        self.authors = "Alice Smith, Bob Lee"
        self.journal = "Crossref Journal"
        self.year = "2024"
        self.volume = "8"


class _FakeSlots:
    def __init__(self):
        self.author = "Xu"
        self.title = "Sample"
        self.journal = "Journal"
        self.year = "2024"
        self.volume = "2"
        self.query = "Xu Sample Journal 2024 2"


class _FakeParsed:
    def __init__(self):
        self.author = ""
        self.title = "Sample"
        self.journal = ""
        self.year = ""
        self.volume = ""


class _FakeImporter:
    def __init__(self, exists: bool = False):
        self.exists = exists

    def import_doi_entry(self, order, parsed, doi_result, collection_key, skip_existing=True):
        if self.exists and skip_existing:
            return "skipped_duplicate", ""
        return "imported", f"ITEM-{order}"


class DoiZoteroWorkflowTests(unittest.TestCase):
    def test_should_import_to_zotero_when_doi_found(self):
        rows, summary = process_citations(
            citations=["[1] sample"],
            lookup_doi_func=lambda **_: _FakeLookupResult("10.1000/xyz"),
            parse_citation_func=lambda _: _FakeParsed(),
            build_slots_func=lambda _: _FakeSlots(),
            importer=_FakeImporter(exists=False),
            collection_key="COLL-1",
            enable_zotero_import=True,
            skip_existing_doi=True,
        )
        self.assertEqual(rows[0]["status"], "zotero_imported")
        self.assertEqual(summary["imported"], 1)

    def test_should_mark_not_found_when_no_doi(self):
        rows, summary = process_citations(
            citations=["[1] sample"],
            lookup_doi_func=lambda **_: _FakeLookupResult(""),
            parse_citation_func=lambda _: _FakeParsed(),
            build_slots_func=lambda _: _FakeSlots(),
            importer=None,
            collection_key="",
            enable_zotero_import=False,
            skip_existing_doi=True,
        )
        self.assertEqual(rows[0]["status"], "doi_not_found")
        self.assertEqual(summary["doi_found"], 0)

    def test_should_skip_duplicate_when_existing_doi(self):
        rows, summary = process_citations(
            citations=["[1] sample"],
            lookup_doi_func=lambda **_: _FakeLookupResult("10.1000/xyz"),
            parse_citation_func=lambda _: _FakeParsed(),
            build_slots_func=lambda _: _FakeSlots(),
            importer=_FakeImporter(exists=True),
            collection_key="COLL-1",
            enable_zotero_import=True,
            skip_existing_doi=True,
        )
        self.assertEqual(rows[0]["status"], "zotero_skipped_duplicate")
        self.assertEqual(summary["skipped_duplicate"], 1)

    def test_should_enrich_missing_slots_from_crossref(self):
        rows, _summary = process_citations(
            citations=["[1] sample"],
            lookup_doi_func=lambda **_: _FakeLookupResult("10.1000/xyz"),
            parse_citation_func=lambda _: _FakeParsed(),
            build_slots_func=lambda _: _FakeSlots(),
            importer=None,
            collection_key="",
            enable_zotero_import=False,
            skip_existing_doi=True,
        )
        self.assertEqual(rows[0]["parsed_author"], "Alice Smith, Bob Lee")
        self.assertEqual(rows[0]["parsed_journal"], "Crossref Journal")
        self.assertEqual(rows[0]["parsed_year"], "2024")

    def test_should_start_step2_only_after_step1_completed(self):
        events = list(
            process_citations_with_events(
                citations=["[1] sample"],
                lookup_doi_func=lambda **_: _FakeLookupResult("10.1000/xyz"),
                parse_citation_func=lambda _: _FakeParsed(),
                build_slots_func=lambda _: _FakeSlots(),
                importer=_FakeImporter(exists=False),
                collection_key="COLL-1",
                enable_zotero_import=True,
                skip_existing_doi=True,
            )
        )
        step1_done = next(i for i, event in enumerate(events) if event.get("event") == "step_completed" and event.get("step") == "crossref")
        step2_start = next(i for i, event in enumerate(events) if event.get("event") == "step_started" and event.get("step") == "zotero")
        self.assertLess(step1_done, step2_start)

    def test_should_parse_bibtex_entry(self):
        bib_entry = """@article{Rajaee2024,
          title={{XGBoost} machine learning assisted prediction of the mechanical and fracture properties},
          author={Rajaee, Pouya and Rabiee, Amir Hossein},
          journal={Polymer Composites},
          volume={45},
          year={2024},
          doi={10.1002/pc.28801}
        }"""
        parsed = parse_citation_text(bib_entry)
        self.assertIn("XGBoost machine learning assisted", parsed.title)
        self.assertEqual(parsed.journal, "Polymer Composites")
        self.assertEqual(parsed.year, "2024")
        self.assertEqual(parsed.volume, "45")

    def test_import_rows_events_should_emit_step_final(self):
        rows = [{
            "idx": "1",
            "stage": "lookup_done",
            "status": "doi_found",
            "parsed_author": "Alice Smith",
            "parsed_title": "Paper",
            "parsed_journal": "Journal",
            "parsed_year": "2024",
            "parsed_volume": "8",
            "search_query": "Paper",
            "doi": "10.1000/xyz",
            "score": "55.0",
            "zotero_item_key": "",
            "error": "",
        }]
        events = list(import_rows_with_events(rows=rows, importer=_FakeImporter(exists=False), collection_key="COLL-1", skip_existing_doi=True))
        self.assertEqual(events[-1]["event"], "step_final")


if __name__ == "__main__":
    unittest.main()
