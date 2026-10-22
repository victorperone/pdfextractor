from types import SimpleNamespace

import pytest

from structured_pdf_text.document import (
    EvidenceRef,
    OcrToken,
    RegionKind,
    SourceKind,
    TableCell,
    TableMethod,
    TextToken,
)
from structured_pdf_text.fusion.integrator import (
    IntegrationRejected,
    NativeOCRIntegrator,
    cell_evidence_snapshot,
    merge_ocr_region_tokens_into_table_cells,
    ocr_tokens_snapshot,
)
from structured_pdf_text.geometry import BBox
from structured_pdf_text.tables.text_join import join_table_tokens


def _old_token(text, x0=0.0):
    return TextToken(
        text=text,
        bbox=BBox(x0, 0, x0 + 30, 10),
        sources=[EvidenceRef(SourceKind.NATIVE_PDF, 0, f"native:{text}")],
        confidence=0.9,
        normalized_text=text,
        evidence_id=f"native:{text}",
    )


def _ocr_token(text, x0=0.0, confidence=0.95):
    return OcrToken(
        text=text,
        bbox=BBox(x0, 0, x0 + 30, 10),
        confidence=confidence,
        language="pt-BR",
        source=SourceKind.OCR_REGION,
    )


def _cell(text, row=0, col=0, tokens=()):
    return TableCell(row, col, 1, 1, BBox(col * 40, 0, col * 40 + 35, 12), text, list(tokens), 0.4)


def test_cell_replacement_is_conservative_and_preserves_lineage_without_touching_neighbor():
    target = _cell("Saldo 350", tokens=[_old_token("Saldo 350")])
    neighbor = _cell("Vencimento 12/05/2026", col=1, tokens=[_old_token("Vencimento", 40)])
    before = cell_evidence_snapshot(target)
    integrator = NativeOCRIntegrator(join_table_tokens)

    applied = integrator.apply_cell_replacement(
        request_id="request-cell-1",
        page_index=0,
        table_id="table-a",
        cell=target,
        expected_snapshot=before,
        evidence=(_ocr_token("Saldo 350,00"),),
        proposed_text="Saldo 350,00",
    )

    assert applied is True
    assert target.text == "Saldo 350,00"
    assert target.tokens[0].derived_from_ids == ("native:Saldo 350",)
    assert target.tokens[0].transformation_type == "table_cell_ocr"
    assert neighbor.text == "Vencimento 12/05/2026"


def test_cell_replacement_rejects_lost_negation_and_signed_value_without_mutation():
    target = _cell("Não aprovado - R$ 350,00", tokens=[_old_token("Não aprovado - R$ 350,00")])
    before = cell_evidence_snapshot(target)
    integrator = NativeOCRIntegrator(join_table_tokens)

    with pytest.raises(IntegrationRejected, match="conserve"):
        integrator.apply_cell_replacement(
            request_id="request-cell-bad",
            page_index=0,
            table_id="table-a",
            cell=target,
            expected_snapshot=before,
            evidence=(_ocr_token("Aprovado R$ 350,00"),),
            proposed_text="Aprovado R$ 350,00",
        )

    assert cell_evidence_snapshot(target) == before


def test_cell_replacement_rejects_stale_snapshot_and_replay_is_idempotent():
    target = _cell("Código 0012", tokens=[_old_token("Código 0012")])
    before = cell_evidence_snapshot(target)
    evidence = (_ocr_token("Código 0012"),)
    integrator = NativeOCRIntegrator(join_table_tokens)

    assert integrator.apply_cell_replacement(
        request_id="request-cell-replay",
        page_index=0,
        table_id="table-a",
        cell=target,
        expected_snapshot=before,
        evidence=evidence,
        proposed_text="Código 0012",
    ) is True
    committed = cell_evidence_snapshot(target)
    assert integrator.apply_cell_replacement(
        request_id="request-cell-replay",
        page_index=0,
        table_id="table-a",
        cell=target,
        expected_snapshot=before,
        evidence=evidence,
        proposed_text="Código 0012",
    ) is False
    assert cell_evidence_snapshot(target) == committed

    stale = _cell("Antes", tokens=[_old_token("Antes")])
    stale_snapshot = cell_evidence_snapshot(stale)
    stale.text = "Mudou durante OCR"
    with pytest.raises(IntegrationRejected, match="stale"):
        integrator.apply_cell_replacement(
            request_id="request-cell-stale",
            page_index=0,
            table_id="table-a",
            cell=stale,
            expected_snapshot=stale_snapshot,
            evidence=(_ocr_token("Depois"),),
            proposed_text="Depois",
        )

    confidence_stale = _cell("Valor", tokens=[_old_token("Valor")])
    confidence_snapshot = cell_evidence_snapshot(confidence_stale)
    confidence_stale.confidence = 0.8
    with pytest.raises(IntegrationRejected, match="stale"):
        integrator.apply_cell_replacement(
            request_id="request-cell-confidence-stale",
            page_index=0,
            table_id="table-a",
            cell=confidence_stale,
            expected_snapshot=confidence_snapshot,
            evidence=(_ocr_token("Valor"),),
            proposed_text="Valor",
        )


def test_region_to_cell_merge_stages_all_cells_before_mutation():
    cells = [
        _cell("A", row=0, col=0, tokens=[_old_token("A")]),
        _cell("B", row=0, col=1, tokens=[_old_token("B", 40)]),
    ]
    table = SimpleNamespace(method=TableMethod.STRICT_GRID, table_id="table-a", cells=cells)
    regions = [
        SimpleNamespace(
            kind=RegionKind.FIGURE,
            bbox=cell.bbox,
            ocr_tokens=[_ocr_token(f"OCR-{index}", x0=index * 40)],
        )
        for index, cell in enumerate(cells)
    ]
    before = [(cell.text, list(cell.tokens)) for cell in cells]
    calls = 0

    def fail_second_join(tokens):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("synthetic join failure")
        return join_table_tokens(tokens)

    with pytest.raises(RuntimeError, match="synthetic"):
        merge_ocr_region_tokens_into_table_cells(
            tables=[table], regions=regions, page_index=0, join_tokens=fail_second_join,
        )

    assert [(cell.text, cell.tokens) for cell in cells] == before


def test_token_replacement_is_conservative_atomic_and_idempotent():
    original = [_ocr_token("R$ -350,00"), _ocr_token("fora", x0=60)]
    box = BBox(0, 0, 40, 12)
    before = ocr_tokens_snapshot(original)
    integrator = NativeOCRIntegrator()

    result, applied = integrator.apply_token_replacement(
        request_id="weak-region:p0:1",
        page_index=0,
        target_id="page-0:ocr-tokens",
        tokens=original,
        expected_snapshot=before,
        box=box,
        replacement=[_ocr_token("R$ -350,00 corrigido")],
    )

    assert applied
    assert [token.text for token in result] == ["fora", "R$ -350,00 corrigido"]
    replay, replay_applied = integrator.apply_token_replacement(
        request_id="weak-region:p0:1",
        page_index=0,
        target_id="page-0:ocr-tokens",
        tokens=result,
        expected_snapshot=before,
        box=box,
        replacement=[_ocr_token("R$ -350,00 corrigido")],
    )
    assert not replay_applied
    assert replay == result

    stale_after_commit = result + [_ocr_token("concorrente", x0=100)]
    with pytest.raises(IntegrationRejected, match="changed after"):
        integrator.apply_token_replacement(
            request_id="weak-region:p0:1",
            page_index=0,
            target_id="page-0:ocr-tokens",
            tokens=stale_after_commit,
            expected_snapshot=before,
            box=box,
            replacement=[_ocr_token("R$ -350,00 corrigido")],
        )


def test_token_replacement_rejects_stale_and_content_erasing_proposals():
    original = [_ocr_token("Não aprovado - R$ 350,00")]
    box = BBox(0, 0, 40, 12)
    before = ocr_tokens_snapshot(original)
    integrator = NativeOCRIntegrator()

    with pytest.raises(IntegrationRejected, match="conserve"):
        integrator.apply_token_replacement(
            request_id="erase",
            page_index=0,
            target_id="page-0:ocr-tokens",
            tokens=original,
            expected_snapshot=before,
            box=box,
            replacement=[_ocr_token("Aprovado R$ 350,00")],
        )
    assert ocr_tokens_snapshot(original) == before

    changed = [_ocr_token("Mudou depois do OCR")]
    with pytest.raises(IntegrationRejected, match="stale"):
        integrator.apply_token_replacement(
            request_id="stale",
            page_index=0,
            target_id="page-0:ocr-tokens",
            tokens=changed,
            expected_snapshot=before,
            box=box,
            replacement=[_ocr_token("Novo valor")],
        )


def test_token_replacement_can_target_one_repeated_evidence_occurrence():
    original = [_ocr_token("linha repetida"), _ocr_token("linha repetida")]
    box = BBox(0, 0, 40, 12)
    integrator = NativeOCRIntegrator()

    result, applied = integrator.apply_token_replacement(
        request_id="replace-second-repeat",
        page_index=0,
        target_id="page-0:ocr-tokens",
        tokens=original,
        expected_snapshot=ocr_tokens_snapshot(original),
        box=box,
        selection_indices=(1,),
        replacement=[_ocr_token("linha repetida corrigida")],
    )

    assert applied
    assert [token.text for token in result] == ["linha repetida", "linha repetida corrigida"]


def test_figure_integration_keeps_region_evidence_when_token_replacement_is_rejected():
    box = BBox(0, 0, 40, 12)
    previous_token = _ocr_token("Não aprovado - R$ 350,00")
    region = SimpleNamespace(
        kind=RegionKind.FIGURE,
        bbox=box,
        ocr_lines=["previous line"],
        ocr_tokens=[previous_token],
    )

    result, applied = NativeOCRIntegrator().apply_figure_refinement(
        tokens=[previous_token],
        box=box,
        replacement_tokens=[_ocr_token("Aprovado R$ 350,00")],
        regions=[region],
        figure_lines=["new line"],
        figure_tokens=[_ocr_token("Aprovado R$ 350,00")],
    )

    assert not applied
    assert result == [previous_token]
    assert region.ocr_lines == ["previous line"]
    assert region.ocr_tokens == [previous_token]


def test_figure_integration_stages_token_and_region_updates_together():
    box = BBox(0, 0, 40, 12)
    previous_token = _ocr_token("R$ -350,00")
    replacement_token = _ocr_token("R$ -350,00 corrigido")
    region = SimpleNamespace(
        kind=RegionKind.FIGURE,
        bbox=box,
        ocr_lines=["previous line"],
        ocr_tokens=[previous_token],
    )

    result, applied = NativeOCRIntegrator().apply_figure_refinement(
        tokens=[previous_token],
        box=box,
        replacement_tokens=[replacement_token],
        regions=[region],
        figure_lines=["new line"],
        figure_tokens=[replacement_token],
    )

    assert applied
    assert result == [replacement_token]
    assert region.ocr_lines == ["new line"]
    assert region.ocr_tokens == [replacement_token]

