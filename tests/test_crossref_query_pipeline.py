import unittest

from citation_pipeline.crossref_query_pipeline import (
    CrossrefQueryHit,
    _dedupe_by_doi_then_title,
    build_keyword_queries,
    run_crossref_query_pipeline_with_events,
    score_and_rank_hits,
)


class DedupeTests(unittest.TestCase):
    def test_dedupe_prefers_first_doi(self):
        a = {"DOI": "10.1/one", "title": ["Same"]}
        b = {"DOI": "10.1/one", "title": ["Dup"]}
        pairs = [(a, "q1"), (b, "q2")]
        out = _dedupe_by_doi_then_title(pairs)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0][0]["DOI"], "10.1/one")


class ScoreTests(unittest.TestCase):
    def test_rank_prefers_higher_overlap(self):
        hits = [
            CrossrefQueryHit(
                doi="10.1/a",
                title="machine learning polymer property",
                authors="",
                journal="",
                year="2023",
                volume="",
                crossref_score=50.0,
                search_subquery="q",
            ),
            CrossrefQueryHit(
                doi="10.1/b",
                title="unrelated chemistry topic",
                authors="",
                journal="",
                year="2020",
                volume="",
                crossref_score=80.0,
                search_subquery="q",
            ),
        ]
        ranked = score_and_rank_hits("machine learning polymer", hits, threshold_recommended=0.99, threshold_consider=0.1)
        self.assertEqual(ranked[0].doi, "10.1/a")

    def test_rank_prefers_acronym_and_author_signals(self):
        hits = [
            CrossrefQueryHit(
                doi="10.1/a",
                title="evolutionary computation survey",
                authors="Someone Else",
                journal="",
                year="2023",
                volume="",
                crossref_score=95.0,
                search_subquery="q",
            ),
            CrossrefQueryHit(
                doi="10.1/b",
                title="NSGA-II multi-objective Deb algorithm",
                authors="Kalyanmoy Deb",
                journal="",
                year="2002",
                volume="",
                crossref_score=40.0,
                search_subquery="q",
            ),
        ]
        ranked = score_and_rank_hits(
            "NSGA-II Kalyanmoy Deb multi-objective optimization",
            hits,
            threshold_recommended=0.99,
            threshold_consider=0.01,
        )
        self.assertEqual(ranked[0].doi, "10.1/b")


class QueryBuilderTests(unittest.TestCase):
    def test_long_query_should_keep_author_and_acronym_subquery(self):
        query = (
            "The Non-dominated Sorting Genetic Algorithm II (NSGA-II) is an efficient method proposed "
            "by Kalyanmoy Deb and co-workers for multi-objective optimization."
        )
        subqueries, keywords = build_keyword_queries(query)
        merged = " || ".join(subqueries).lower()
        self.assertIn("deb", merged)
        self.assertIn("nsga-ii", merged)
        self.assertTrue(any(k in {"nsga-ii", "algorithm", "optimization"} for k in keywords))

    def test_events_emit_keywords_first(self):
        events = list(
            run_crossref_query_pipeline_with_events(
                "polymer property prediction",
                mailto="test@example.com",
                top_k_per_query=1,
                delay_seconds=0.0,
                llm_reranker=None,
                fetch_works_fn=lambda *args, **kwargs: [],
            )
        )
        self.assertGreaterEqual(len(events), 2)
        self.assertEqual(events[0]["event"], "keywords_ready")
        self.assertEqual(events[-1]["event"], "final")


if __name__ == "__main__":
    unittest.main()
