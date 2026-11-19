"""Regressões tratadas depois da RUN 011 (output/tesseract/011).

T3-P06: títulos de regiões só-OCR viravam parágrafos. ``assign_heading_levels``
        escolhia o critério por documento: com qualquer página de fonte nativa
        (V4, V9 e V10 têm), toda região TITLE era julgada pelo critério nativo,
        que lê só ``native_lines``; a região só-OCR recebia score 0 e era
        rebaixada. O critério agora segue a proveniência da região. No layout
        OCR (texto nivelado), linhas de corpo embutidas num parágrafo não viram
        título, linhas de título distantes formam regiões separadas e uma linha
        em tamanho de título não é legenda.

As geometrias abaixo foram medidas nas extrações reais (caixas de linha em pt,
como chegam a ``reconstruct_ocr_layout``).
"""
from __future__ import annotations

from structured_pdf_text.document import (
    Baseline, EvidenceRef, LayoutRegion, OcrToken, RegionDecision, RegionKind, RegionQuality,
    SourceKind, StructuredPage, TextLine, TextToken, WritingDirection,
)
from structured_pdf_text.geometry import BBox

PAGE = BBox(0, 0, 595.276, 841.89)

# V10 p52 (L05, raster nivelado), coluna 1: a linha "com o protocolo…" tem
# caixa de 8.5 pt (corpo 6.25) e vãos de 1.25/1.0 pt para as vizinhas.
_V10_P52_COLUMN = [
    (57.23, 265.22, 193.43, 270.21, "decisão administrativa no sistema. A cadeia"),
    (57.23, 273.46, 197.43, 279.96, "de custódia dos registros foi preservada para"),
    (57.48, 282.46, 185.43, 288.46, "permitir futuras consultas e auditorias de"),
    (57.48, 290.96, 76.72, 295.71, "rotina."),
    (56.98, 308.21, 183.93, 314.46, "A análise técnica recomenda preservar a"),
    (57.23, 316.71, 191.43, 322.96, "cronologia dos registros e manter o vínculo"),
    (57.23, 324.21, 196.18, 332.71, "com o protocolo eletrônico. A documentação"),
    (57.23, 333.71, 173.18, 339.46, "contém referências cruzadas, valores"),
    (57.23, 341.46, 184.43, 349.70, "estimados e prazos sujeitos à verificação"),
]
# V10 p6, topo: cabeçalho institucional, título da página e linha de metadados.
_V10_P6_TOP = [
    (54.48, 23.50, 355.37, 31.50, "INSTITUTO SINTÉTICO DE GESTÃO E CONTROLE | DOCUMENTAÇÃO INTERNA"),
    (55.98, 65.99, 424.09, 80.24, "Aviso administrativo: atualização de licenças"),
    (55.73, 118.48, 471.32, 125.98,
     "Publicação: V10-2026-001815 Competência: 27/03/2026 Unidade: Assessoria de Monitoramento de Processos"),
]
# V9 p2: título da página (16 pt, corpo 9.75) que começa com uma palavra de legenda.
_V9_P2_TOP = [
    (54.73, 69.24, 504.81, 85.24, "Quadro de acompanhamento | apuração de pendências"),
    (54.48, 87.49, 138.70, 99.49, "cadastrais"),
    (54.98, 120.48, 386.10, 127.98, "Processo: V9-2026-000267 Competência: 15/10/2026 Unidade: Unidade de Fiscalização"),
    (57.73, 154.73, 530.80, 164.73,
     "Foram comparados os prazos previstos, as notas de recebimento e os saldos informados. A divergência"),
    (57.23, 167.23, 323.88, 176.98, "encontrada exige justificativa antes da homologação final."),
]


def _ocr_line(x0: float, y0: float, x1: float, y1: float, text: str) -> TextLine:
    token = OcrToken(text, BBox(x0, y0, x1, y1), 0.9, "pt-BR", SourceKind.OCR_PAGE)
    return TextLine(tokens=[token], bbox=token.bbox, baseline=Baseline(y=y1),
                    direction=WritingDirection.LEFT_TO_RIGHT, native_order_min=None, native_order_max=None)


def _body(y0: float, count: int, height: float, x0: float = 57.0) -> list[tuple]:
    return [(x0, y0 + row * height * 1.4, x0 + 200.0, y0 + row * height * 1.4 + height,
             f"linha de corpo {row:02d} do texto corrido da página") for row in range(count)]


def _ocr_region(region_id: str, geometry: list[tuple], kind: RegionKind = RegionKind.TEXT) -> LayoutRegion:
    lines = [_ocr_line(*item) for item in geometry]
    return LayoutRegion(
        region_id=region_id, kind=kind, bbox=BBox.union_all([line.bbox for line in lines]),
        layout_confidence=1.0, native_lines=[], ocr_tokens=[t for line in lines for t in line.tokens],
        quality=RegionQuality(RegionDecision.KEEP_NATIVE), ocr_lines=lines,
    )


def _layout(geometry: list[tuple]) -> list[LayoutRegion]:
    from structured_pdf_text.layout.ocr_aware import reconstruct_ocr_layout

    return reconstruct_ocr_layout([_ocr_region("page:ocr", geometry)], PAGE)


def _region_of(regions: list[LayoutRegion], prefix: str) -> LayoutRegion:
    return next(r for r in regions for line in r.ocr_lines if line.text.startswith(prefix))


# --------------------------------------------------------------------------- layout OCR


def test_tall_body_line_inside_a_paragraph_is_not_a_title():
    regions = _layout(_V10_P52_COLUMN)

    assert _region_of(regions, "com o protocolo").kind == RegionKind.TEXT
    assert _region_of(regions, "com o protocolo") is _region_of(regions, "cronologia dos registros")
    words = [w for r in regions for line in r.ocr_lines for w in line.text.split()]
    assert words == [w for *_, text in _V10_P52_COLUMN for w in text.split()]


def test_title_lines_far_apart_are_separate_headings():
    regions = _layout(_V10_P6_TOP + _body(196.0, 8, 6.5))

    title = _region_of(regions, "Aviso administrativo")
    assert title.kind == RegionKind.TITLE
    assert [line.text for line in title.ocr_lines] == ["Aviso administrativo: atualização de licenças"]


def test_title_sized_line_starting_with_a_caption_word_is_a_title():
    regions = _layout(_V9_P2_TOP + _body(190.0, 8, 9.75))

    assert _region_of(regions, "Quadro de acompanhamento").kind == RegionKind.TITLE


# --------------------------------------------------------------------------- heading levels


def _native_line(text: str, y: float, size: float, bold: bool = False) -> TextLine:
    bbox = BBox(57.0, y, 57.0 + len(text) * size * 0.5, y + size * 1.2)
    token = TextToken(text=text, bbox=bbox, sources=[EvidenceRef(SourceKind.NATIVE_PDF, 0, f"n:{y}")],
                      confidence=1.0, normalized_text=text)
    token.font_size = size
    token.font_weight = 700 if bold else 400
    return TextLine(tokens=[token], bbox=bbox, baseline=Baseline(y=bbox.y1),
                    direction=WritingDirection.LEFT_TO_RIGHT, native_order_min=None, native_order_max=None)


def _native_region(region_id: str, lines: list[TextLine], kind: RegionKind) -> LayoutRegion:
    return LayoutRegion(region_id=region_id, kind=kind, bbox=BBox.union_all([line.bbox for line in lines]),
                        layout_confidence=1.0, native_lines=lines, ocr_tokens=[],
                        quality=RegionQuality(RegionDecision.KEEP_NATIVE))


def _page(index: int, regions: list[LayoutRegion]) -> StructuredPage:
    return StructuredPage(page_index=index, bbox=PAGE, regions=regions, tables=[], raw_text="",
                          reading_text="", diagnostics=None)  # type: ignore[arg-type]


def _native_page() -> StructuredPage:
    title = _native_region("p0:title", [_native_line("Relatório nativo de controle", 60.0, 18.0, bold=True)],
                           RegionKind.TITLE)
    body = _native_region("p0:body", [_native_line(f"Texto nativo da linha {i} do corpo.", 100.0 + i * 14, 10.0)
                                      for i in range(12)], RegionKind.TEXT)
    return _page(0, [title, body])


def _raster_page(extra_titles: list[tuple] = ()) -> StructuredPage:
    """V10 p52 shape: page title (14.7 pt) and a column heading (9.75 pt) over 6.5 pt body."""
    regions = [
        _ocr_region("p1:h1", [(57.0, 66.0, 443.0, 80.7, "Relatório de inspeção: controle de pagamentos")],
                    RegionKind.TITLE),
        _ocr_region("p1:h3", [(58.0, 447.0, 271.0, 456.7, "Encaminhamento e providências: ajuste")],
                    RegionKind.TITLE),
        *[_ocr_region(f"p1:x{i}", [item], RegionKind.TITLE) for i, item in enumerate(extra_titles)],
        _ocr_region("p1:body", _body(470.0, 12, 6.5)),
    ]
    return _page(1, regions)


def _kinds(pages: list[StructuredPage]) -> dict[str, tuple[RegionKind, int | None]]:
    return {r.region_id: (r.kind, r.heading_level) for page in pages for r in page.regions}


def test_ocr_titles_survive_in_a_document_with_native_pages():
    from structured_pdf_text.layout.heading import assign_heading_levels

    kinds = _kinds(assign_heading_levels([_native_page(), _raster_page()]))

    assert kinds["p1:h1"] == (RegionKind.TITLE, 1)
    assert kinds["p1:h3"][0] == RegionKind.TITLE and kinds["p1:h3"][1] > 1
    assert kinds["p0:title"] == (RegionKind.TITLE, 1)


def test_native_heading_decisions_do_not_depend_on_ocr_pages():
    from structured_pdf_text.layout.heading import assign_heading_levels

    alone = _kinds(assign_heading_levels([_native_page()]))
    mixed = _kinds(assign_heading_levels([_native_page(), _raster_page()]))

    assert {key: value for key, value in mixed.items() if key.startswith("p0:")} == alone


def test_body_sized_or_record_lines_are_not_ocr_titles():
    from structured_pdf_text.layout.heading import assign_heading_levels

    extra = [
        # metadata line, 1.15x the body (V10 p52 "Publicação: …")
        (56.0, 118.0, 457.0, 125.5, "Publicação: V10-2026-014833 Competência: 29/07/2026 Unidade: Finanças"),
        # paragraph first line opening with the article "A" (V9 p2)
        (58.0, 494.0, 499.0, 500.7, "A revisão identificou itens regulares e apontou providências"),
        # numbered record of an undetected table (V10 p4)
        (57.0, 560.0, 400.0, 566.7, "001 V10-83617-00 21/09/2026 Diretoria Administr Pendente"),
    ]
    for document in ([_native_page(), _raster_page(extra)], [_raster_page(extra)]):
        kinds = _kinds(assign_heading_levels(document))
        assert [kinds[f"p1:x{i}"][0] for i in range(len(extra))] == [RegionKind.TEXT] * len(extra)
        assert kinds["p1:h1"][0] == RegionKind.TITLE


def test_ocr_title_on_skewed_text_is_left_as_text():
    from dataclasses import replace

    from structured_pdf_text.layout.heading import assign_heading_levels

    page = _raster_page()
    tilted = []
    for region in page.regions:
        tokens = []
        for row, line in enumerate(region.ocr_lines):
            # engine line identity + a 4.5° tilt, as on the L01/L02 scans
            for word in range(4):
                x0 = line.bbox.x0 + word * 40.0
                dy = 0.08 * (x0 + 15.0)
                tokens.append(OcrToken(f"w{word}", BBox(x0, line.bbox.y0 + dy, x0 + 30.0, line.bbox.y1 + dy), 0.9,
                                       "pt-BR", SourceKind.OCR_PAGE,
                                       layout_line_id=f"call:1:1:{region.region_id}:{row}"))
        tilted.append(replace(region, ocr_tokens=tokens))
    kinds = _kinds(assign_heading_levels([_native_page(), _page(1, tilted)]))

    assert kinds["p1:h1"][0] == RegionKind.TEXT


def test_merged_ocr_title_fragments_keep_every_line():
    from structured_pdf_text.layout.heading import assign_heading_levels

    first = _ocr_region("p1:a", [(57.0, 66.0, 300.0, 80.7, "Ofício de encaminhamento: cronograma de")],
                        RegionKind.TITLE)
    second = _ocr_region("p1:b", [(57.0, 83.0, 120.0, 97.0, "serviços")], RegionKind.TITLE)
    page = _page(1, [first, second, _ocr_region("p1:body", _body(470.0, 12, 6.5))])

    result = assign_heading_levels([_native_page(), page])[1]

    words = [w for r in result.regions for line in r.ocr_lines for w in line.text.split()]
    assert "serviços" in words and "Ofício" in words
