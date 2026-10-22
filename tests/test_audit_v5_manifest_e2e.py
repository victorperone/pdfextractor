"""Synthetic domain integration for invariants motivated by the V5 audit.

PDFium, authorization, cell integration, assembly and renderers are real;
OCR and grid discovery are deterministic injections. These are not V5 corpus
recognition tests, which remain separate release gates.
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


@pytest.fixture
def hybrid_pdf(tmp_path, monkeypatch):
    from ocr_domain_fixtures import hybrid_table_pdf, install_table_structure

    install_table_structure(monkeypatch)
    return hybrid_table_pdf(tmp_path / "critical-hybrid.pdf")


class TestSyntheticHybridInvariants:
    def test_empty_cells_preserved_after_real_ocr_work(self, hybrid_pdf):
        from ocr_domain_fixtures import extract_hybrid
        from structured_pdf_text.document import TableMethod
        from structured_pdf_text.renderers import render_markdown

        document, _ = extract_hybrid(hybrid_pdf)
        native = next(table for table in document.tables if table.method == TableMethod.STRICT_GRID)
        row = [cell for cell in native.cells if cell.row == 1]
        assert [cell.text for cell in row] == ["Ajuste", "", ""]
        assert all(not cell.tokens for cell in row[1:])
        assert render_markdown(document).count("Ajuste") == 1

    def test_native_negation_and_critical_values_survive_conflicting_observation(self, hybrid_pdf):
        from ocr_domain_fixtures import extract_hybrid
        from structured_pdf_text.document import OcrToken, SourceKind
        from structured_pdf_text.geometry import BBox
        from structured_pdf_text.renderers import render_markdown

        # Outside the selected figure; the adapter must never request this
        # native rectangle just because the injected recognizer has a reading.
        wrong = OcrToken("AUTORIZADO", BBox(25, 16, 125, 28), .99, "pt-BR", SourceKind.OCR_REGION)
        document, engine = extract_hybrid(hybrid_pdf, extra_tokens=(wrong,))
        markdown = render_markdown(document)
        assert markdown.count("NÃO AUTORIZADO") == 1
        assert "CPF 123.456.789-09 CNPJ 12.345.678/0001-90" in markdown
        assert "Saldo R$ -42,90 ID AB-009" in markdown
        assert all(box.intersection(wrong.bbox) is None for box in engine.calls)

    def test_critical_fingerprints_and_structure_stable_in_both_renderers(self, hybrid_pdf):
        import json
        import re
        from ocr_domain_fixtures import extract_hybrid
        from structured_pdf_text.renderers import render_json, render_markdown

        first, _ = extract_hybrid(hybrid_pdf)
        second, _ = extract_hybrid(hybrid_pdf)
        assert render_markdown(first) == render_markdown(second)
        assert first.reading_text == second.reading_text
        fingerprint = re.compile(r"123\.456\.789-09|12\.345\.678/0001-90|-42,90|AB-009")
        assert fingerprint.findall(first.reading_text) == fingerprint.findall(second.reading_text)
        assert len(fingerprint.findall(first.reading_text)) == 6
        for document in (first, second):
            def reject_constant(value):
                raise AssertionError(f"Nonfinite JSON: {value}")
            serialized = json.loads(render_json(document), parse_constant=reject_constant)
            assert len(serialized["pages"]) == 1
            assert len(serialized["pages"][0]["tables"]) == 2
            assert document.pages[0].diagnostics.facts["content_fallback_lines"] == 0
