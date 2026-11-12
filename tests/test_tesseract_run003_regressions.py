"""Regressões da RUN 003 (output/tesseract/003).

T3-P10: linhas OCR do Tesseract fundidas entre colunas de texto corrido.
T3-P18: palavras de linhas inclinadas misturadas com linhas vizinhas (tokens
        Tesseract não têm polígono, então a inclinação nunca era estimada).

A identidade de linha do Tesseract (``layout_line_id``) só é preenchida pelo
backend Tesseract; tokens sem ela seguem o caminho de reconstrução anterior.
"""
from __future__ import annotations

from structured_pdf_text.document import OcrToken, SourceKind
from structured_pdf_text.geometry import BBox
from structured_pdf_text.ocr.coordinates import map_tokens_to_page, offset_tokens
from structured_pdf_text.ocr.pipeline.converters import from_domain, to_domain
from structured_pdf_text.ocr.reconstruct import reconstruct_ocr_lines

PAGE = BBox(0, 0, 595, 842)
WORD_W, WORD_GAP, LINE_H = 30.0, 5.0, 10.0


def _words(x0: float, y0: float, count: int, line_id: str | None, *, slope: float = 0.0,
           prefix: str = "w") -> list[OcrToken]:
    tokens = []
    for index in range(count):
        x = x0 + index * (WORD_W + WORD_GAP)
        y = y0 + slope * x
        tokens.append(OcrToken(f"{prefix}{index}", BBox(x, y, x + WORD_W, y + LINE_H), 0.9, "pt-BR",
                               SourceKind.OCR_PAGE, layout_line_id=line_id))
    return tokens


def _columns(starts: list[float], rows: int, words: int = 5, *, with_ids: bool = True,
             y0: float = 100.0) -> list[OcrToken]:
    tokens = []
    for column, x0 in enumerate(starts):
        for row in range(rows):
            line_id = f"call:1:{column + 1}:1:{row + 1}" if with_ids else None
            tokens += _words(x0, y0 + row * 14.0, words, line_id, prefix=f"c{column}r{row}w")
    return tokens


def _crosses(line, boundary: float) -> bool:
    return line.bbox.x0 < boundary < line.bbox.x1


# --- T3-P10: colunas de texto corrido -------------------------------------

def test_two_prose_columns_are_not_merged_into_one_line():
    tokens = _columns([40.0, 320.0], rows=8)

    lines = reconstruct_ocr_lines(tokens, 0, PAGE)

    assert len(lines) == 16
    assert not any(_crosses(line, 230.0) for line in lines)
    left = [line.text for line in lines if line.bbox.x1 < 230.0]
    assert left[0] == "c0r0w0 c0r0w1 c0r0w2 c0r0w3 c0r0w4"


def test_three_prose_columns_split_at_both_gutters():
    tokens = _columns([20.0, 215.0, 410.0], rows=6)

    lines = reconstruct_ocr_lines(tokens, 0, PAGE)

    assert len(lines) == 18
    assert not any(_crosses(line, 200.0) or _crosses(line, 395.0) for line in lines)


def test_short_last_line_of_a_column_is_split_inside_a_validated_gutter():
    tokens = _columns([40.0, 320.0], rows=6)
    # Linha final curta (1 palavra) na coluna esquerda, ao lado de uma linha longa.
    tokens += _words(40.0, 100.0 + 6 * 14.0, 1, "call:1:1:1:7", prefix="fim")
    tokens += _words(320.0, 100.0 + 6 * 14.0, 5, "call:1:2:1:7", prefix="dir")

    lines = reconstruct_ocr_lines(tokens, 0, PAGE)

    assert "fim0" in [line.text for line in lines]
    assert not any(_crosses(line, 230.0) for line in lines)


def test_table_rows_with_short_cells_in_distinct_blocks_stay_whole():
    tokens = []
    for row in range(5):
        for column, x0 in enumerate((40.0, 160.0, 280.0, 400.0)):
            tokens += _words(x0, 100.0 + row * 14.0, 2, f"call:1:{column + 1}:{row + 1}:1",
                             prefix=f"r{row}c{column}w")

    lines = reconstruct_ocr_lines(tokens, 0, PAGE)

    assert len(lines) == 5
    assert lines[0].text == "r0c0w0 r0c0w1 r0c1w0 r0c1w1 r0c2w0 r0c2w1 r0c3w0 r0c3w1"


def test_single_wide_metadata_line_is_not_split_without_vertical_continuity():
    tokens = (_words(40.0, 100.0, 4, "call:1:1:1:1", prefix="a")
              + _words(240.0, 100.0, 4, "call:1:2:1:1", prefix="b")
              + _words(440.0, 100.0, 4, "call:1:3:1:1", prefix="c"))

    lines = reconstruct_ocr_lines(tokens, 0, PAGE)

    assert len(lines) == 1


def test_gutter_is_only_applied_inside_the_vertical_extent_of_the_columns():
    tokens = _columns([40.0, 320.0], rows=6)
    # Linha de largura total muito abaixo das colunas, com dois blocos e vão largo.
    tokens += _words(40.0, 400.0, 5, "call:1:9:1:1", prefix="x")
    tokens += _words(320.0, 400.0, 5, "call:1:10:1:1", prefix="y")

    lines = reconstruct_ocr_lines(tokens, 0, PAGE)

    assert "x0 x1 x2 x3 x4 y0 y1 y2 y3 y4" in [line.text for line in lines]


def test_tokens_without_tesseract_identity_keep_previous_behaviour():
    tokens = _columns([40.0, 320.0], rows=8, with_ids=False)

    lines = reconstruct_ocr_lines(tokens, 0, PAGE)

    # Caminho anterior (outros motores): tokens-palavra não são separados.
    assert len(lines) == 8
    assert all(_crosses(line, 230.0) for line in lines)


# --- T3-P18: inclinação -----------------------------------------------------

def test_skewed_tesseract_lines_are_not_mixed_with_neighbour_lines():
    slope = 0.06  # ~3.4°: ao longo de 470 pt a linha desce 28 pt (> espaçamento de 14 pt)
    tokens = []
    for row in range(6):
        tokens += _words(40.0, 100.0 + row * 14.0, 14, f"call:1:1:1:{row + 1}", slope=slope,
                         prefix=f"r{row}w")

    lines = reconstruct_ocr_lines(tokens, 0, PAGE)

    assert [line.text.split()[0] for line in lines] == [f"r{row}w0" for row in range(6)]
    assert all(len(line.text.split()) == 14 for line in lines)
    assert all({word[:2] for word in line.text.split()} == {line.text[:2]} for line in lines)


# --- Transporte da identidade de linha ---------------------------------------

def test_layout_line_id_survives_pipeline_conversion_and_coordinate_mapping():
    token = OcrToken("palavra", BBox(10, 10, 50, 20), 0.9, "pt-BR", SourceKind.OCR_PAGE,
                     layout_line_id="call:1:2:1:3")

    evidence = from_domain(token, token_id="a:token:0", authorization_id="auth", attempt_id="a")
    assert to_domain(evidence).layout_line_id == "call:1:2:1:3"
    for rotation in (0, 90, 180, 270):
        (mapped,) = map_tokens_to_page([token], PAGE, 1000, 1000, rotation)
        assert mapped.layout_line_id == "call:1:2:1:3"
    (moved,) = offset_tokens([token], 5.0, 5.0)
    assert moved.layout_line_id == "call:1:2:1:3"


def test_tesseract_tsv_tokens_carry_scoped_line_identity():
    from structured_pdf_text.ocr.backends.tesseract import _parse_tsv, _tsv_to_pipeline_tokens

    header = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"
    rows = [
        "5\t1\t1\t1\t1\t1\t10\t10\t40\t12\t95.0\tUm",
        "5\t1\t1\t1\t1\t2\t60\t10\t40\t12\t95.0\tdois",
        "5\t1\t2\t1\t1\t1\t300\t10\t40\t12\t95.0\ttrês",
    ]
    parsed = _parse_tsv("\n".join([header, *rows]))

    first = _tsv_to_pipeline_tokens(parsed, 0, "pt-BR", layout_scope="s1")
    second = _tsv_to_pipeline_tokens(parsed, 0, "pt-BR", layout_scope="s2")
    legacy = _tsv_to_pipeline_tokens(parsed, 0, "pt-BR")

    assert first[0].layout_line_id == first[1].layout_line_id != first[2].layout_line_id
    assert first[0].layout_line_id != second[0].layout_line_id
    assert all(token.layout_line_id is None for token in legacy)


# --- T3-P16: página só com elementos gráficos não é falha de OCR ------------

def _page_image():
    from PIL import Image

    return Image.new("RGB", (1000, 1400), "white")


def _text_image(rotate: int = 0):
    from PIL import ImageDraw

    image = _page_image()
    draw = ImageDraw.Draw(image)
    for row in range(6):
        draw.text((80, 200 + row * 40), "Processo administrativo 2026 conferido", fill="black")
    return image.rotate(rotate, expand=True, fillcolor="white") if rotate else image


def test_text_like_ink_is_detected_in_lines_of_text_in_any_orientation():
    from structured_pdf_text.api import _has_text_like_ink

    assert _has_text_like_ink(_text_image())
    assert _has_text_like_ink(_text_image(rotate=90))


def test_graphic_only_page_has_no_text_like_ink():
    from PIL import ImageDraw

    from structured_pdf_text.api import _has_text_like_ink

    image = _page_image()
    draw = ImageDraw.Draw(image)
    draw.ellipse((350, 400, 650, 700), outline="black", width=6)
    draw.rectangle((420, 480, 580, 620), fill="gray")
    draw.line((200, 1000, 800, 1000), fill="black", width=3)
    assert not _has_text_like_ink(image)
    assert not _has_text_like_ink(_page_image())


def test_text_like_ink_check_is_conservative_on_unreadable_input():
    from structured_pdf_text.api import _has_text_like_ink

    assert _has_text_like_ink(object())


# --- T3-P10: ordem de leitura por faixas de colunas ----------------------------

def _band_page() -> list[OcrToken]:
    """Três colunas em cima (6 linhas) e duas embaixo (6 linhas)."""
    tokens = []
    for column, x0 in enumerate((20.0, 215.0, 410.0)):
        for row in range(6):
            tokens += _words(x0, 100.0 + row * 14.0, 5, f"call:1:{column + 1}:1:{row + 1}",
                             prefix=f"T{column}r{row}w")
    for column, x0 in enumerate((20.0, 315.0)):
        for row in range(6):
            tokens += _words(x0, 300.0 + row * 14.0, 7, f"call:1:{column + 4}:1:{row + 1}",
                             prefix=f"B{column}r{row}w")
    return tokens


def test_text_column_bands_are_read_column_by_column_band_by_band():
    from structured_pdf_text.layout.ocr_aware import _order_ocr_lines_by_text_gutters
    from structured_pdf_text.ocr.reconstruct import ocr_text_column_gutters

    tokens = _band_page()
    lines = reconstruct_ocr_lines(tokens, 0, PAGE)
    gutters = ocr_text_column_gutters(tokens, PAGE)

    ordered = [line.text.split()[0][:4] for line in _order_ocr_lines_by_text_gutters(lines, gutters)]

    expected = ([f"T0r{r}" for r in range(6)] + [f"T1r{r}" for r in range(6)] + [f"T2r{r}" for r in range(6)]
                + [f"B0r{r}" for r in range(6)] + [f"B1r{r}" for r in range(6)])
    assert ordered == expected


def test_column_headings_right_above_the_flows_are_split_at_the_gutter():
    tokens = _columns([40.0, 320.0], rows=6, y0=130.0)
    tokens += _words(40.0, 110.0, 2, "call:1:7:1:1", prefix="tituloA")
    tokens += _words(320.0, 110.0, 2, "call:1:8:1:1", prefix="tituloB")

    lines = reconstruct_ocr_lines(tokens, 0, PAGE)

    assert "tituloA0 tituloA1" in [line.text for line in lines]
    assert "tituloB0 tituloB1" in [line.text for line in lines]


def test_text_column_gutters_require_engine_identity_and_upright_tokens():
    from dataclasses import replace

    from structured_pdf_text.ocr.reconstruct import ocr_text_column_gutters

    assert ocr_text_column_gutters(_columns([40.0, 320.0], rows=8, with_ids=False), PAGE) == []
    tokens = _columns([40.0, 320.0], rows=8)
    (gutter,) = ocr_text_column_gutters(tokens, PAGE)
    assert 210.0 < gutter[0] < 320.0
    assert ocr_text_column_gutters([replace(t, rotation=90) for t in tokens], PAGE) == []
    assert ocr_text_column_gutters(tokens, PAGE, page_rotation=90) == []


# --- T3-P17: colunas de texto corrido não viram tabela sem borda ---------------

def test_ocr_track_table_crossed_by_a_text_column_gutter_is_rejected():
    from structured_pdf_text.tables.ocr_tracks import _crosses_text_column_gutter

    gutters = [(265.0, 90.0, 200.0)]
    assert _crosses_text_column_gutter(BBox(40, 100, 500, 190), gutters)
    # Tabela abaixo da faixa de colunas: a calha não a atravessa.
    assert not _crosses_text_column_gutter(BBox(40, 220, 500, 320), gutters)
    # Tabela inteiramente de um lado da calha.
    assert not _crosses_text_column_gutter(BBox(40, 100, 250, 190), gutters)
    assert not _crosses_text_column_gutter(BBox(40, 100, 500, 190), [])


# --- T3-P02: página escaneada a 90°/180°/270° ---------------------------------

_TSV_HEADER = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"


def _tsv(confidence: float, words: int = 5) -> str:
    rows = [f"5\t1\t1\t1\t1\t{i + 1}\t{10 + i * 50}\t10\t40\t12\t{confidence}\tpalavra{i}" for i in range(words)]
    return "\n".join([_TSV_HEADER, *rows])


def _retry_backend(readings: dict[int, float]):
    from PIL import Image

    from structured_pdf_text.ocr.backends.tesseract import TesseractBackend

    backend = TesseractBackend.__new__(TesseractBackend)
    calls = []

    def read(pil, dpi):
        # O retry só relê as três rotações, nesta ordem; 90° e 270° trocam as dimensões.
        rotation = (90, 180, 270)[len(calls)]
        assert pil.size == ((200, 300) if rotation in (90, 270) else (300, 200))
        calls.append(rotation)
        return _tsv(readings.get(rotation, 20.0))

    backend._read_tsv = read
    return backend, calls, Image.new("RGB", (300, 200), "white")


def test_low_confidence_page_is_reread_rotated_and_better_reading_is_kept():
    backend, calls, image = _retry_backend({90: 92.0})

    tsv, rotation = backend._retry_quarter_turns(image, 300, _tsv(40.0))

    assert rotation == 90
    assert "\t92.0\t" in tsv
    assert len(calls) == 3


def test_confident_or_degraded_upright_page_is_not_retried():
    backend, calls, image = _retry_backend({90: 95.0})

    assert backend._retry_quarter_turns(image, 300, _tsv(65.0)) == (_tsv(65.0), 0)
    assert backend._retry_quarter_turns(image, 300, _tsv(30.0, words=2)) == (_tsv(30.0, words=2), 0)
    assert calls == []


def test_rotated_reading_needs_a_clear_confidence_gain():
    backend, _, image = _retry_backend({180: 62.0})

    assert backend._retry_quarter_turns(image, 300, _tsv(45.0)) == (_tsv(45.0), 0)


def test_rotated_page_tokens_carry_the_inverse_orientation():
    from PIL import Image

    from structured_pdf_text.config import ExtractorConfig
    from structured_pdf_text.ocr.backends.tesseract import TesseractBackend

    backend = TesseractBackend.__new__(TesseractBackend)
    backend._config = ExtractorConfig(ocr_engine="tesseract")
    backend._language = "pt-BR"
    backend._conf_min = 0.0
    backend._dpi = 300
    backend._run_effective_tesseract = lambda image, dpi: (_tsv(90.0), 90, 300, 200)

    tokens = backend.recognize_page(Image.new("RGB", (300, 200)), 0)

    assert tokens and all(token.rotation == 270 for token in tokens)


# --- Parser TSV: aspas ASCII não podem engolir linhas -------------------------

def test_tsv_word_starting_with_a_quote_does_not_swallow_following_rows():
    from structured_pdf_text.ocr.backends.tesseract import _parse_tsv, _tsv_to_pipeline_tokens

    rows = [
        _TSV_HEADER,
        '5\t1\t12\t1\t4\t4\t535\t1606\t107\t30\t60.87\t"aspas"',
        "5\t1\t12\t1\t4\t5\t652\t1616\t17\t18\t95.97\te",
        '5\t1\t12\t1\t4\t6\t679\t1614\t123\t25\t95.88\t"caminho',
        "5\t1\t12\t1\t4\t7\t812\t1617\t145\t29\t67.00\tC:\\QA\\lote",
    ]

    tokens = _tsv_to_pipeline_tokens(_parse_tsv("\n".join(rows)), 0, "pt-BR")

    assert [token.text for token in tokens] == ['"aspas"', "e", '"caminho', "C:\\QA\\lote"]


# --- Título quebrado com travessão não vira item de lista ----------------------

def _layout_line(text: str, y0: float, x0: float = 40.0):
    token = OcrToken(text, BBox(x0, y0, x0 + 6.0 * len(text), y0 + 10.0), 0.9, "pt-BR", SourceKind.OCR_PAGE)
    (line,) = reconstruct_ocr_lines([token], 0, PAGE)
    return line


def test_em_dash_line_continuing_a_wrapped_title_is_not_a_list_item():
    from structured_pdf_text.document import RegionKind
    from structured_pdf_text.layout.ocr_aware import _is_dash_continuation

    title = _layout_line("Secretaria Regional de Controle", 100.0)
    wrapped = _layout_line("— Extrato de tramitação", 113.0)
    sentence = _layout_line("O procedimento foi concluído.", 100.0)
    far = _layout_line("— Item distante", 160.0)

    assert _is_dash_continuation(wrapped, title, True, [(title, RegionKind.TITLE)], 10.0)
    assert not _is_dash_continuation(wrapped, sentence, True, [(sentence, RegionKind.TEXT)], 10.0)
    assert not _is_dash_continuation(far, title, True, [(title, RegionKind.TITLE)], 10.0)
    assert not _is_dash_continuation(wrapped, title, True, [(title, RegionKind.LIST)], 10.0)
    assert not _is_dash_continuation(wrapped, title, False, [(title, RegionKind.TITLE)], 10.0)
    hyphen = _layout_line("- Item com hífen", 113.0)
    assert not _is_dash_continuation(hyphen, title, True, [(title, RegionKind.TITLE)], 10.0)


def _vertical_tsv(confidence: float, lines: int = 4, words: int = 4) -> str:
    rows = []
    for line in range(lines):
        for word in range(words):
            rows.append(f"5\t1\t1\t1\t{line + 1}\t{word + 1}\t{10 + line * 30}\t{10 + word * 60}\t12\t50\t{confidence}\tpalavra")
    return "\n".join([_TSV_HEADER, *rows])


def test_confident_page_read_as_vertical_lines_is_reread_rotated():
    from PIL import Image

    from structured_pdf_text.ocr.backends.tesseract import TesseractBackend

    backend = TesseractBackend.__new__(TesseractBackend)
    readings = {90: _vertical_tsv(80.0), 180: _vertical_tsv(85.0), 270: _tsv(93.0)}
    calls = []

    def read(pil, dpi):
        rotation = (90, 180, 270)[len(calls)]
        calls.append(rotation)
        return readings[rotation]

    backend._read_tsv = read
    upright = _vertical_tsv(86.0)

    assert backend._retry_quarter_turns(Image.new("RGB", (300, 200)), 300, upright) == (readings[270], 270)


def test_vertical_engine_lines_fall_back_to_word_grouping():
    tokens = []
    for column in range(4):
        for index in range(5):
            x0, y0 = 60.0 + column * 30.0, 100.0 + index * 60.0
            tokens.append(OcrToken(f"c{column}w{index}", BBox(x0, y0, x0 + 12.0, y0 + 50.0), 0.9, "pt-BR",
                                   SourceKind.OCR_PAGE, layout_line_id=f"call:1:1:1:{column + 1}"))
    with_ids = [line.text for line in reconstruct_ocr_lines(tokens, 0, PAGE)]
    from dataclasses import replace
    without_ids = [line.text for line in reconstruct_ocr_lines([replace(t, layout_line_id=None) for t in tokens], 0, PAGE)]
    assert with_ids == without_ids


def test_engine_line_groups_are_emitted_column_by_column_inside_a_band():
    from structured_pdf_text.ocr.reconstruct import _group_engine_lines, text_column_bands

    tokens = _band_page()
    boxes = {id(token): token.bbox for token in tokens}

    firsts = [group[0].text[:4] for group in _group_engine_lines(tokens, boxes, PAGE.width)]

    expected = ([f"T{c}r{r}" for c in range(3) for r in range(6)]
                + [f"B{c}r{r}" for c in range(2) for r in range(6)])
    assert firsts == expected
    assert text_column_bands([]) == []
    assert len(text_column_bands([(200.0, 90.0, 200.0), (395.0, 92.0, 198.0), (300.0, 290.0, 390.0)])) == 2
