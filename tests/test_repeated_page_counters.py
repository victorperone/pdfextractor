"""T3-P43: running headers/footers whose page counter has no "Página" label.

A stand-alone number makes each page's furniture signature unique, so exact
repetition never confirms it. The counter rule must suppress such lines only
with converging evidence (edge position, same text form, a number that follows
the pagination with a constant offset on every occurrence, a run of
consecutive pages, stable position and style) and must preserve every
legitimate line that merely resembles a footer.
"""
from __future__ import annotations

from structured_pdf_text.assemble.document import _apply_repeated_suppression
from structured_pdf_text.assemble.repeated_regions import (
    detect_repeated_headers_footers,
    repeated_line_keys,
)
from structured_pdf_text.document import (
    Baseline,
    ContentKind,
    EvidenceRef,
    LayoutRegion,
    PageContentBlock,
    PageDiagnostics,
    PageStrategy,
    RegionDecision,
    RegionKind,
    RegionQuality,
    SourceKind,
    StructuredPage,
    TextLine,
    TextToken,
    WritingDirection,
)
from structured_pdf_text.geometry import BBox

WIDTH, HEIGHT = 595.0, 842.0
HEADER_Y = 20.0          # inside the top edge band (≤ 8% of the page height)
FOOTER_Y = 812.0         # inside the bottom edge band
BODY_Y = 400.0


def _line(text: str, line_id: str, y: float, *, ocr: bool = False, size: float = 6.5,
          font: str | None = "DejaVuSans") -> TextLine:
    bbox = BBox(45.0, y, 300.0, y + 7.0)
    token = TextToken(
        text=text, bbox=bbox,
        sources=[EvidenceRef(SourceKind.OCR_PAGE if ocr else SourceKind.NATIVE_PDF, 0, line_id)],
        confidence=0.95 if ocr else 1.0, normalized_text=text,
        font_name=None if ocr else font, font_size=None if ocr else size,
    )
    return TextLine(tokens=[token], bbox=bbox, baseline=Baseline(y=bbox.y1),
                    direction=WritingDirection.LEFT_TO_RIGHT, native_order_min=None,
                    native_order_max=None, line_id=line_id)


def _page(index: int, lines: list[TextLine], *, ocr: bool = False) -> StructuredPage:
    region = LayoutRegion(
        region_id=f"r{index}", kind=RegionKind.TEXT, bbox=BBox(0.0, 0.0, WIDTH, HEIGHT),
        layout_confidence=1.0,
        native_lines=[] if ocr else lines, ocr_lines=lines if ocr else [], ocr_tokens=[],
        quality=RegionQuality(RegionDecision.KEEP_NATIVE),
    )
    return StructuredPage(
        page_index=index, bbox=BBox(0.0, 0.0, WIDTH, HEIGHT), regions=[region], tables=[],
        raw_text="", reading_text="",
        diagnostics=PageDiagnostics(page_index=index, strategy=PageStrategy.NATIVE, reasons=[],
                                    native_chars=0, native_text_length=0),
    )


def _document(edge_text, *, y: float = FOOTER_Y, pages=range(0, 6), ocr: bool = False,
              body=lambda i: f"Corpo único da página {i + 1} com texto narrativo próprio.") -> list[StructuredPage]:
    """One edge line per page (``edge_text(i)`` or None) plus a unique body line."""
    result = []
    for i in pages:
        prefix = "ocr" if ocr else "native"
        lines = [_line(body(i), f"{prefix}:p{i}:body", BODY_Y, ocr=ocr, size=10.0)]
        text = edge_text(i)
        if text is not None:
            lines.append(_line(text, f"{prefix}:p{i}:edge", y, ocr=ocr))
        result.append(_page(i, lines, ocr=ocr))
    return result


def _suppressed_edges(pages: list[StructuredPage]) -> list[int]:
    """Pages whose edge line would be suppressed by the assembly."""
    repeated = detect_repeated_headers_footers(pages)
    hits = []
    for page in pages:
        keys = repeated_line_keys(page)
        for line_id, key in keys.items():
            if line_id.endswith(":edge") and page.page_index in repeated.get(key, ()):
                hits.append(page.page_index)
    return hits


# ----------------------------------------------------------------- positives

def test_footer_counter_without_page_label_is_furniture() -> None:
    pages = _document(lambda i: f"Ensaio V11-R1 · Folha {i + 1:03d}")
    assert _suppressed_edges(pages) == list(range(6))


def test_header_counter_without_any_label_is_furniture() -> None:
    pages = _document(lambda i: f"Coleção sintética · {i + 1:03d}", y=HEADER_Y)
    assert _suppressed_edges(pages) == list(range(6))


def test_counter_with_prefix_and_total_suffix_is_furniture() -> None:
    pages = _document(lambda i: f"Documento sintético para teste | Folha {i + 1:02d}/84")
    assert _suppressed_edges(pages) == list(range(6))


def test_counter_with_constant_offset_is_furniture() -> None:
    # Printed numbering starts after an unnumbered cover: printed = physical - 1.
    pages = _document(lambda i: None if i == 0 else f"Relatório anual — {i}", pages=range(0, 7))
    assert _suppressed_edges(pages) == list(range(1, 7))


def test_ocr_punctuation_variants_share_one_counter_group() -> None:
    separators = ["·", "-", ":", "-", "·", ":"]
    pages = _document(lambda i: f"Ensaio V11-R1 {separators[i]} Folha {i + 1:03d}", ocr=True)
    assert _suppressed_edges(pages) == list(range(6))


def test_suppression_removes_only_the_counter_block() -> None:
    pages = _document(lambda i: f"Ensaio V11-R1 · Folha {i + 1:03d}")
    repeated = detect_repeated_headers_footers(pages)
    page = pages[2]
    body = PageContentBlock(block_id="b0", page_index=2, kind=ContentKind.TEXT,
                            bbox=BBox(45.0, BODY_Y, 300.0, BODY_Y + 7.0), order_index=0,
                            text="Corpo", line_ids=["native:p2:body"])
    edge = PageContentBlock(block_id="b1", page_index=2, kind=ContentKind.TEXT,
                            bbox=BBox(45.0, FOOTER_Y, 300.0, FOOTER_Y + 7.0), order_index=1,
                            text="Ensaio V11-R1 · Folha 003", line_ids=["native:p2:edge"])
    blocks = [body, edge]
    _apply_repeated_suppression(blocks, page, repeated, preserve_headers_footers=False)
    assert not body.suppressed
    assert edge.suppressed and edge.suppression_reason == "repeated_footer"


# ----------------------------------------------------------------- negatives

def test_numbers_in_body_are_never_candidates() -> None:
    pages = _document(lambda i: None, body=lambda i: f"Item {i + 1}: conferência da etapa {i + 1}")
    assert detect_repeated_headers_footers(pages) == {}


def test_hyphenated_identifiers_are_not_counters() -> None:
    pages = _document(lambda i: f"Lote TOKEN-{i + 1:03d}-06 conferido")
    assert _suppressed_edges(pages) == []


def test_dates_and_money_values_are_not_counters() -> None:
    dates = _document(lambda i: f"Emitido em {i + 1:02d}/03/2025")
    money = _document(lambda i: f"Saldo do dia: R$ {i + 1}.245,90")
    assert _suppressed_edges(dates) == []
    assert _suppressed_edges(money) == []


def test_scattered_continuation_lines_are_preserved() -> None:
    # Same text form, number equal to the page, edge position and stable
    # style, but only on scattered pages: a label placed where content needs it.
    pages = _document(lambda i: f"Continuação do registro técnico · folha sintética {i + 1:03d}"
                      if i in (2, 5, 8, 11) else None, y=HEADER_Y, pages=range(0, 12))
    assert _suppressed_edges(pages) == []


def test_text_mentioning_a_page_is_preserved() -> None:
    # Notes citing other pages: the number does not follow the pagination.
    cited = [9, 4, 12, 2, 7, 15]
    pages = _document(lambda i: f"Ver também a folha {cited[i]}")
    assert _suppressed_edges(pages) == []
    # A page reference inside the body is never an edge candidate.
    body_refs = _document(lambda i: None, body=lambda i: f"Este quadro continua na folha {i + 2}.")
    assert detect_repeated_headers_footers(body_refs) == {}


def test_numbered_section_titles_one_per_page_are_preserved() -> None:
    # "Seção N" on page N in the top band, with stable position and style:
    # the number follows the pagination, but the line is set larger than the
    # body text, as titles are and furniture is not.
    pages = []
    for i in range(6):
        lines = [_line(f"Corpo narrativo {i}", f"native:p{i}:body", BODY_Y, size=10.0),
                 _line(f"Seção {i + 1} — procedimentos", f"native:p{i}:edge", HEADER_Y, size=14.0)]
        pages.append(_page(i, lines))
    assert _suppressed_edges(pages) == []


def test_item_numbers_at_the_edge_are_preserved() -> None:
    # Numbered records ending pages: unique texts, numbers not following the pages.
    items = [3, 4, 8, 9, 15, 16]
    pages = _document(lambda i: f"Item {items[i]} — registro de conferência")
    assert _suppressed_edges(pages) == []


def test_restarting_counter_is_preserved() -> None:
    # Per-chapter numbering: every text is unique, but the counter restarts.
    labels = [(1, 1), (1, 2), (1, 3), (2, 1), (2, 2), (2, 3)]
    pages = _document(lambda i: f"Capítulo {labels[i][0]} · folha {labels[i][1]}")
    assert _suppressed_edges(pages) == []


def test_non_sequential_numbering_is_preserved() -> None:
    values = [4, 9, 2, 7, 3, 8]
    pages = _document(lambda i: f"Registro de amostra {values[i]}")
    assert _suppressed_edges(pages) == []


def test_legitimate_line_with_number_equal_to_page_on_two_pages_is_preserved() -> None:
    pages = _document(lambda i: f"Quadro {i + 1} — resumo" if i in (3, 4) else None)
    assert _suppressed_edges(pages) == []


def test_semantic_control_identifier_is_never_furniture() -> None:
    pages = _document(lambda i: f"Controle de página: V5-P{i + 1:03d}")
    assert _suppressed_edges(pages) == []


def test_unstable_position_is_preserved() -> None:
    pages = []
    for i in range(6):
        y = HEADER_Y if i % 2 == 0 else 60.0   # alternates between 2% and 7% of the height
        lines = [_line(f"Corpo {i}", f"native:p{i}:body", BODY_Y, size=10.0),
                 _line(f"Coleção · {i + 1:03d}", f"native:p{i}:edge", y)]
        pages.append(_page(i, lines))
    assert _suppressed_edges(pages) == []


def test_displaced_occurrence_is_preserved_while_stable_core_is_furniture() -> None:
    # One occurrence sits lower (e.g. the inflated box of a tilted scan line):
    # it is not confirmed; the occurrences sharing one position still are.
    pages = []
    for i in range(6):
        y = 60.0 if i == 3 else HEADER_Y
        lines = [_line(f"Corpo {i}", f"native:p{i}:body", BODY_Y, size=10.0),
                 _line(f"Coleção · {i + 1:03d}", f"native:p{i}:edge", y)]
        pages.append(_page(i, lines))
    assert _suppressed_edges(pages) == [0, 1, 2, 4, 5]


def test_inconsistent_typography_is_preserved() -> None:
    pages = []
    for i in range(6):
        font = "DejaVuSans" if i % 2 == 0 else "DejaVuSerif"
        lines = [_line(f"Corpo {i}", f"native:p{i}:body", BODY_Y, size=10.0),
                 _line(f"Coleção · {i + 1:03d}", f"native:p{i}:edge", FOOTER_Y, font=font)]
        pages.append(_page(i, lines))
    assert _suppressed_edges(pages) == []


def test_exact_repetition_behaviour_is_unchanged() -> None:
    # Furniture without numbers and "Página N de M" keep their previous confirmation.
    plain = _document(lambda i: "INSTITUTO FICTÍCIO DE ENSAIOS DOCUMENTAIS", y=HEADER_Y)
    labelled = _document(lambda i: f"Página {i + 1} de 6")
    assert _suppressed_edges(plain) == list(range(6))
    assert _suppressed_edges(labelled) == list(range(6))
