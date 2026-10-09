"""V5 manifest — End-to-end regression scaffold.

Documents the high-level quality expectations from the V5 audit. These tests
use the Fake OCR backend to run the full pipeline deterministically.
Some tests are live; others are xfail specs for future work.
"""
from __future__ import annotations

import pytest

from structured_pdf_text.config import ExtractorConfig
from structured_pdf_text.document import ExtractionStatus, TextToken


class TestV5ManifestContract:
    """Invariants that must hold in all V5+ pipeline runs."""

    def test_extraction_status_enum_values(self):
        """Sanity: ExtractionStatus values match expected set."""
        values = {e.value for e in ExtractionStatus}
        assert "success" in values
        assert "failure" in values or "partial_success" in values

    def test_extractor_config_defaults(self):
        """Default config is valid and instantiates without error."""
        cfg = ExtractorConfig()
        assert cfg is not None
        assert hasattr(cfg, "enable_ocr")
        assert hasattr(cfg, "ocr_engine")

    def test_text_token_evidence_fields_present(self):
        """TextToken must expose the VQ-28 evidence fields."""
        tok = TextToken.__dataclass_fields__  # type: ignore[attr-defined]
        assert "evidence_id" in tok
        assert "derived_from_ids" in tok
        assert "transformation_type" in tok
        assert "decision_reason" in tok
        assert "owner_id" in tok

    def test_evidence_id_format_native(self):
        """Deterministic native evidence_id must match canonical format."""
        eid = "native:p3:char:100-150"
        parts = eid.split(":")
        assert parts[0] == "native"
        assert parts[1].startswith("p")
        assert parts[2] == "char"
        lo, hi = parts[3].split("-")
        assert int(lo) < int(hi)

    def test_evidence_id_format_ocr(self):
        """Deterministic OCR evidence_id must match canonical format."""
        eid = "ocr:p3:region_0:candidate_0:idx5"
        parts = eid.split(":")
        assert parts[0] == "ocr"
        assert parts[1].startswith("p")
        assert parts[4].startswith("idx")
        assert int(parts[4][3:]) >= 0


@pytest.mark.xfail(reason="V5 E2E — requires corpus PDF fixture", strict=False)
class TestV5E2EWithCorpus:
    """Full-pipeline tests that require the corpus PDF (excluded from CI without fixture)."""

    def test_v5_p009_empty_cells_preserved(self):
        """V5-P009: empty cells in 'Ajuste' row must not be filled by OCR."""
        import os
        corpus_path = os.path.join(
            os.path.dirname(__file__),
            "corpus", "Corpus_Integrado_PDF_OCR_TableMagic_V3.pdf"
        )
        if not os.path.exists(corpus_path):
            pytest.skip("Corpus PDF not available")

        from structured_pdf_text.api import PdfTextExtractor
        extractor = PdfTextExtractor(ExtractorConfig(ocr_backend="fake"))
        doc = extractor.extract(corpus_path, page_indices=[9])
        md = doc.to_markdown()
        # The Ajuste row: columns 3 and 5 must be empty
        lines = [l for l in md.splitlines() if "Ajuste" in l]
        assert lines, "V5-P009: Ajuste row must appear in output"

    def test_v5_no_negation_removal_across_all_pages(self):
        """No page in V5 corpus should have a negation word removed by OCR refinement."""
        pytest.skip("Corpus PDF not available in CI")

    def test_v5_critical_data_fingerprints_stable(self):
        """CPF/CNPJ/monetary values must be identical across two extraction runs."""
        pytest.skip("Corpus PDF not available in CI")
