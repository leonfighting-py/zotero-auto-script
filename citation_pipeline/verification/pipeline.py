from __future__ import annotations

from pathlib import Path

from citation_pipeline.common.config import VerificationConfig
from citation_pipeline.common.models import VerifiedReference
from citation_pipeline.verification.bibtex_parser import BibtexReferenceParser
from citation_pipeline.verification.crossref_verifier import CrossrefVerifier


class VerificationPipeline:
    def __init__(self, config: VerificationConfig):
        self.config = config
        self.parser = BibtexReferenceParser()
        self.verifier = CrossrefVerifier(config)

    def run(self, bib_path: Path | None = None) -> list[VerifiedReference]:
        source = bib_path or self.config.input_bib
        references = self.parser.parse(source)
        return self.verifier.verify_all(references)
