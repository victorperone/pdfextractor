"""Guard: false table detection guards for V9 form pages.

V9-FT-01: A grid whose y-extent covers more than 85% of the page height is a
    form frame, not a data table, and must be rejected by
    _detect_rectangular_grid.  This prevents V9 form pages (p042, p047, p057,
    p068, p077, p090, p094) from being wrapped in a full-page false table.

V9-FT-02: A grid covering 70% of the page height (a large but legitimate
    table) must still be detected.

V9-FT-03: A grid matching the page width/height thresholds but covering only
    40% of the height is always accepted.

V9-FT-04: A borderless 2-column block where all first-column cells end with ":"
    (key-value form block) must be rejected by ProseVsTableClassifier.

V9-FT-05: A borderless 2-column block with no colon-terminated first-column
    cells (real data table) must be accepted.

V9-FT-06: A borderless 2-column block with exactly 80% colon labels (threshold
    boundary) must be rejected.

V9-FT-07: A borderless 2-column block with only 60% colon labels (below
    threshold) must still be accepted.

V9-FT-08 (Fix-F-V9-06): A 6-column block where col0 contains long prose
    phrases (paragraph text that OCR split into spurious columns) must be
    rejected via prose_dominant_label_column.

V9-FT-09 (Fix-F-V9-06): A table where non-label cells contain pipe characters
    at ≥10% rate (OCR artifact from rotated/mirrored text) must be rejected
    via pipe_chars_in_nolabel_cells.

V9-FT-10 (Fix-F-V9-06): A legitimate supply table (Rubrica|Descrição|Qtde|
    Valor|Total|Situação) with short label codes and no pipe artefacts must
    still be accepted — no regression for real V9 tables.

V9-FT-11 (Fix-F-V9-06): A 2-column table with long col0 prose but ≥60%
    threshold exactly met must be rejected.

V9-FT-12 (Fix-F-V9-06): A table with ~7% pipe cells (below 10% threshold)
    must NOT be rejected by pipe_chars_in_nolabel_cells.

V9-FT-13 (Fix-F-V9-07): A table with 11.1% pipe cells (1 of 9 non-label cells,
    matching the p003 body-only scenario) must be rejected at the 10% threshold.
"""
from __future__ import annotations

from structured_pdf_text.geometry import BBox
from structured_pdf_text.tables.detector import _detect_rectangular_grid
from structured_pdf_text.tables.text_tracks import ProseVsTableClassifier, _CellGroup


def _make_cell(text: str, x0: float, x1: float, y0: float = 0.0, y1: float = 12.0) -> _CellGroup:
    return _CellGroup(tokens=(), bbox=BBox(x0=x0, y0=y0, x1=x1, y1=y1), text=text)


def _rect(x0: float, y0: float, x1: float, y1: float) -> BBox:
    return BBox(x0=x0, y0=y0, x1=x1, y1=y1)


def _form_frame_paths(page_w: float = 595.0, page_h: float = 842.0) -> list[BBox]:
    """Simulate V9 form page: outer border + field rectangles.

    Two thin horizontal lines spanning the full page (top/bottom border) plus
    ~20 small field rectangles inside. Together they create a spurious
    rectangular grid that covers ~89 % of the page height.
    """
    paths: list[BBox] = []
    # Outer horizontal borders (top and bottom of the form)
    paths.append(_rect(54, 47, 541, 48))    # bottom border (~y=47)
    paths.append(_rect(55, 794, 540, 795))  # top border (~y=794)
    # Form field rectangles distributed across the interior
    field_xs = [(54, 180), (180, 310), (310, 440), (440, 541)]
    field_ys = [(100, 150), (155, 205), (210, 350), (355, 400),
                (405, 500), (505, 600), (605, 700), (705, 750)]
    for y0, y1 in field_ys:
        for x0, x1 in field_xs:
            paths.append(_rect(x0, y0, x1, y1))
    return paths


def _data_table_paths(
    x0: float = 54, y0: float = 300, x1: float = 541, y1: float = 700,
    cols: int = 4, rows: int = 5,
) -> list[BBox]:
    """Simulate a real bordered data table occupying a subsection of the page.

    Each cell is drawn as a filled rectangle (the producer draws cell outlines,
    not separate H/V segments). _detect_rectangular_grid is designed for this.
    """
    paths: list[BBox] = []
    col_w = (x1 - x0) / cols
    row_h = (y1 - y0) / rows
    for r in range(rows):
        for c in range(cols):
            cx0 = x0 + c * col_w
            cy0 = y0 + r * row_h
            paths.append(_rect(cx0, cy0, cx0 + col_w, cy0 + row_h))
    return paths


PAGE = BBox(x0=0, y0=0, x1=595, y1=842)


# ---------------------------------------------------------------------------
# V9-FT-01: Full-page form frame must be rejected
# ---------------------------------------------------------------------------

def test_v9_ft01_full_page_form_frame_rejected() -> None:
    """Grid covering 89% of page height must not be detected as a table."""
    paths = _form_frame_paths()
    grid = _detect_rectangular_grid(paths, PAGE)
    assert grid is None, (
        "A rectangular grid spanning 89% of page height is a form frame, "
        "not a data table — _detect_rectangular_grid must reject it"
    )


# ---------------------------------------------------------------------------
# V9-FT-02: Large (70%) table must still be detected
# ---------------------------------------------------------------------------

def test_v9_ft02_large_legitimate_table_detected() -> None:
    """A data table covering 70% of the page (y=126 to y=715) must be detected."""
    # 70% of 842 = 589 pts, starting at y=126 to y=715
    paths = _data_table_paths(y0=126, y1=715)
    grid = _detect_rectangular_grid(paths, PAGE)
    assert grid is not None, (
        "A bordered table covering 70% of page height is a valid data table "
        "and must be detected by _detect_rectangular_grid"
    )


# ---------------------------------------------------------------------------
# V9-FT-03: Small (40%) table is always accepted
# ---------------------------------------------------------------------------

def test_v9_ft03_small_table_always_accepted() -> None:
    """A data table covering 40% of the page must be detected."""
    # 40% of 842 = 337 pts, e.g. y=252 to y=589
    paths = _data_table_paths(y0=252, y1=589)
    grid = _detect_rectangular_grid(paths, PAGE)
    assert grid is not None, (
        "A data table covering 40% of page height must always be accepted"
    )


# ---------------------------------------------------------------------------
# V9-FT-04 to V9-FT-07: Form-label colon pattern (Fix F-V9-02)
# A 2-column borderless block where ≥80 % of first-column cells end with ":"
# is a key-value form block, not a data table, and must be rejected.
# ---------------------------------------------------------------------------

_ANCHORS_2COL = (50.0, 220.0)


def test_v9_ft04_form_block_all_colon_labels_rejected() -> None:
    """2-column block with 100% colon labels must be rejected (form block)."""
    rows = tuple(
        (
            _make_cell(label, 50, 180, float(y), float(y + 12)),
            _make_cell(value, 220, 400, float(y), float(y + 12)),
        )
        for y, (label, value) in enumerate(
            [
                ("Processo:", "V9-2026-003822"),
                ("Interessado:", "Instituto Modelo de Serviços Administrativos"),
                ("Documento:", "REG-0047/2026"),
                ("Data:", "15/10/2026"),
                ("Responsável:", "Pedro Reis"),
            ],
            start=0,
        )
    )
    assessment = ProseVsTableClassifier().assess(rows, _ANCHORS_2COL)
    assert not assessment.accepted, "A 2-column key-value form block must NOT be accepted as a table"
    assert "form_label_colon_pattern" in assessment.reasons


def test_v9_ft05_real_data_table_no_colons_accepted() -> None:
    """2-column table with no colon labels must be accepted as a data table."""
    rows = tuple(
        (
            _make_cell(name, 50, 200, float(y), float(y + 12)),
            _make_cell(value, 220, 400, float(y), float(y + 12)),
        )
        for y, (name, value) in enumerate(
            [
                ("João Silva", "25"),
                ("Maria Costa", "30"),
                ("Pedro Reis", "28"),
                ("Ana Lima", "35"),
                ("Carlos Souza", "22"),
            ],
            start=0,
        )
    )
    assessment = ProseVsTableClassifier().assess(rows, _ANCHORS_2COL)
    assert assessment.accepted, "A 2-column data table with no colon labels must be accepted"
    assert "form_label_colon_pattern" not in assessment.reasons


def test_v9_ft06_form_block_80pct_colon_rejected() -> None:
    """2-column block with exactly 80% colon labels (threshold boundary) must be rejected."""
    rows = tuple(
        (
            _make_cell(label, 50, 180, float(y), float(y + 12)),
            _make_cell(value, 220, 400, float(y), float(y + 12)),
        )
        for y, (label, value) in enumerate(
            [
                ("Processo:", "V9-001"),
                ("Interessado:", "Instituto"),
                ("Documento:", "REG-01"),
                ("Resposta", "30/10"),   # no colon
                ("Responsável:", "Pedro"),
            ],
            start=0,
        )
    )
    assessment = ProseVsTableClassifier().assess(rows, _ANCHORS_2COL)
    assert not assessment.accepted, "80% colon labels must trigger form_label_colon_pattern rejection"
    assert "form_label_colon_pattern" in assessment.reasons


def test_v9_ft07_60pct_colon_not_rejected() -> None:
    """2-column block with only 60% colon labels must still be accepted."""
    rows = tuple(
        (
            _make_cell(label, 50, 180, float(y), float(y + 12)),
            _make_cell(value, 220, 400, float(y), float(y + 12)),
        )
        for y, (label, value) in enumerate(
            [
                ("Processo:", "V9-001"),
                ("Interessado:", "Instituto"),
                ("Documento:", "REG-01"),
                ("Resultado", "30/10"),   # no colon
                ("Total", "5"),           # no colon
            ],
            start=0,
        )
    )
    assessment = ProseVsTableClassifier().assess(rows, _ANCHORS_2COL)
    assert assessment.accepted, "60% colon labels is below threshold and must still be accepted"
    assert "form_label_colon_pattern" not in assessment.reasons


# ---------------------------------------------------------------------------
# V9-FT-08 to V9-FT-12: Fix-F-V9-06 — prose label column + pipe artefacts
# ---------------------------------------------------------------------------

_ANCHORS_6COL = (50.0, 150.0, 250.0, 320.0, 400.0, 480.0)
_ANCHORS_3COL = (50.0, 220.0, 380.0)


def test_v9_ft08_prose_dominant_label_column_rejected() -> None:
    """6-column block: col0 has long prose sentences → prose_dominant_label_column."""
    # Simulates V9 p003/p036: paragraph text in col0 paired with OCR garbage
    # cols (diluting long_cell_ratio so the old prose_score test passes)
    rows = tuple(
        (
            _make_cell(col0, 50, 148, float(y * 15), float(y * 15 + 12)),
            _make_cell(col1, 150, 248, float(y * 15), float(y * 15 + 12)),
            _make_cell(col2, 250, 318, float(y * 15), float(y * 15 + 12)),
            _make_cell(col3, 320, 398, float(y * 15), float(y * 15 + 12)),
            _make_cell(col4, 400, 478, float(y * 15), float(y * 15 + 12)),
            _make_cell(col5, 480, 540, float(y * 15), float(y * 15 + 12)),
        )
        for y, (col0, col1, col2, col3, col4, col5) in enumerate(
            [
                ("A proposta foi elaborada considerando os critérios", "Je", "9'9", "ã", "T", "Z"),
                ("técnicos e orçamentários vigentes para o exercício", "nb", "0'0", "é", "S", "U"),
                ("fiscal em curso, respeitando o disposto na Lei", "ay", "1'1", "ô", "X", "W"),
                ("Orçamentária Anual aprovada pelo Poder Legislativo", "zz", "2'2", "ã", "V", "Q"),
                ("municipal e demais normas aplicáveis ao processo", "ww", "3'3", "î", "Y", "P"),
            ],
            start=0,
        )
    )
    assessment = ProseVsTableClassifier().assess(rows, _ANCHORS_6COL)
    assert not assessment.accepted, (
        "6-col block with prose sentences in col0 must be rejected (prose_dominant_label_column)"
    )
    assert "prose_dominant_label_column" in assessment.reasons


def test_v9_ft09_pipe_chars_in_nolabel_cells_rejected() -> None:
    """Table where ≥10% of non-label cells contain '|' (OCR artifact) must be rejected."""
    rows = tuple(
        (
            _make_cell(col0, 50, 148, float(y * 15), float(y * 15 + 12)),
            _make_cell(col1, 150, 248, float(y * 15), float(y * 15 + 12)),
            _make_cell(col2, 250, 318, float(y * 15), float(y * 15 + 12)),
        )
        for y, (col0, col1, col2) in enumerate(
            [
                ("Rubrica", "Descrição", "Valor"),
                ("001", "Je|nbay", "150,00"),
                ("002", "09'9ã'T'Z| SU", "200,00"),
                ("003", "normal text", "300,00"),
                ("004", "another|item", "400,00"),
            ],
            start=0,
        )
    )
    assessment = ProseVsTableClassifier().assess(rows, _ANCHORS_3COL)
    assert not assessment.accepted, (
        "Table with ≥15% pipe chars in non-label cells must be rejected (pipe_chars_in_nolabel_cells)"
    )
    assert "pipe_chars_in_nolabel_cells" in assessment.reasons


def test_v9_ft10_real_supply_table_accepted() -> None:
    """V9 supply table (Rubrica|Descrição|Qtde|Valor|Total|Situação) must be accepted."""
    _ANCHORS_SUPPLY = (50.0, 130.0, 240.0, 320.0, 390.0, 460.0)
    rows = tuple(
        (
            _make_cell(rub, 50, 128, float(y * 15), float(y * 15 + 12)),
            _make_cell(desc, 130, 238, float(y * 15), float(y * 15 + 12)),
            _make_cell(qtde, 240, 318, float(y * 15), float(y * 15 + 12)),
            _make_cell(valor, 320, 388, float(y * 15), float(y * 15 + 12)),
            _make_cell(total, 390, 458, float(y * 15), float(y * 15 + 12)),
            _make_cell(sit, 460, 530, float(y * 15), float(y * 15 + 12)),
        )
        for y, (rub, desc, qtde, valor, total, sit) in enumerate(
            [
                ("Rubrica", "Descrição", "Qtde", "Valor Unit.", "Total", "Situação"),
                ("3.3.90.30", "Material de expediente", "10", "15,00", "150,00", "Atendido"),
                ("3.3.90.36", "Outros serviços PF", "2", "500,00", "1.000,00", "Parcial"),
                ("3.3.90.39", "Outros serviços PJ", "1", "2.000,00", "2.000,00", "Pendente"),
            ],
            start=0,
        )
    )
    assessment = ProseVsTableClassifier().assess(rows, _ANCHORS_SUPPLY)
    assert assessment.accepted, (
        "Real V9 supply table (Rubrica|Descrição|Qtde|Valor|Total|Situação) must be accepted"
    )
    assert "prose_dominant_label_column" not in assessment.reasons
    assert "pipe_chars_in_nolabel_cells" not in assessment.reasons


def test_v9_ft11_exactly_60pct_prose_label_rejected() -> None:
    """2-col table: exactly 60% of col0 cells are long prose → rejected at threshold."""
    rows = tuple(
        (
            _make_cell(col0, 50, 180, float(y * 15), float(y * 15 + 12)),
            _make_cell(col1, 220, 400, float(y * 15), float(y * 15 + 12)),
        )
        for y, (col0, col1) in enumerate(
            [
                ("A proposta foi elaborada considerando os critérios técnicos", "150,00"),
                ("Respeitando as normas vigentes para o exercício fiscal atual", "200,00"),
                ("Conforme disposto na regulamentação aplicável ao processo", "300,00"),
                ("Resumo", "400,00"),          # short — not prose
                ("Total geral", "1.050,00"),   # short — not prose
            ],
            start=0,
        )
    )
    assessment = ProseVsTableClassifier().assess(rows, _ANCHORS_2COL)
    assert not assessment.accepted, (
        "Exactly 60% long prose in col0 must trigger prose_dominant_label_column rejection"
    )
    assert "prose_dominant_label_column" in assessment.reasons


def test_v9_ft12_7pct_pipe_not_rejected() -> None:
    """Table with ~7% pipe cells (below 10% threshold) must NOT be rejected."""
    rows = tuple(
        (
            _make_cell(col0, 50, 148, float(y * 15), float(y * 15 + 12)),
            _make_cell(col1, 150, 248, float(y * 15), float(y * 15 + 12)),
            _make_cell(col2, 250, 348, float(y * 15), float(y * 15 + 12)),
        )
        for y, (col0, col1, col2) in enumerate(
            [
                ("Rubrica", "Descrição", "Valor"),
                ("001", "Item A normal", "100,00"),
                ("002", "Item B normal", "200,00"),
                ("003", "Item C normal", "300,00"),
                ("004", "Item D normal", "400,00"),
                ("005", "Item|E artifact", "500,00"),  # 1 of 14 non-label = 7.1%
                ("006", "Item F normal", "600,00"),
                ("007", "Item G normal", "700,00"),
            ],
            start=0,
        )
    )
    assessment = ProseVsTableClassifier().assess(rows, _ANCHORS_3COL)
    # 8 rows × 2 non-label cols = 16 non-label cells, 1 pipe → 6.25% < 10%
    # so pipe_chars_in_nolabel_cells must NOT trigger
    assert "pipe_chars_in_nolabel_cells" not in assessment.reasons


def test_v9_ft13_11pct_pipe_rejected() -> None:
    """Table with 11.1% pipe cells (1 of 9 non-label, p003 body-only scenario) must be rejected."""
    # Simulates V9 p003 body-only run: 3 rows × 4 cols, only body rows
    # (header with Je|nbay rejected as prefix → not counted).
    # Only 09'9T'Z| SU in row1 col2 has a pipe → 1/9 = 11.1% ≥ 10%.
    _ANCHORS_4COL = (50.0, 150.0, 250.0, 380.0)
    rows = tuple(
        (
            _make_cell(col0, 50, 148, float(y * 15), float(y * 15 + 12)),
            _make_cell(col1, 150, 248, float(y * 15), float(y * 15 + 12)),
            _make_cell(col2, 250, 378, float(y * 15), float(y * 15 + 12)),
            _make_cell(col3, 380, 500, float(y * 15), float(y * 15 + 12)),
        )
        for y, (col0, col1, col2, col3) in enumerate(
            [
                ("1jez0JoL", "TZ'09Z'T. SH", "09'9T'Z| SU", "OS'Z90'Z R$:SU"),
                ("ep30 5 1 3 1 1 3", "LO", "OT", "05"),
                ("", "TOO", "too", "003"),
            ],
            start=0,
        )
    )
    assessment = ProseVsTableClassifier().assess(rows, _ANCHORS_4COL)
    # 3 rows × 3 non-label cols = 9 non-label cells, 1 pipe → 11.1% ≥ 10% → rejected
    assert not assessment.accepted
    assert "pipe_chars_in_nolabel_cells" in assessment.reasons
