from __future__ import annotations

from dataclasses import replace
import math

from structured_pdf_text.document import (
    Baseline,
    EvidenceRef,
    NativeCharacter,
    SourceKind,
    TextLine,
    TextToken,
    TokenFlag,
    WritingDirection,
)
from structured_pdf_text.text.normalize import normalize_text
from structured_pdf_text.geometry import BBox
from structured_pdf_text.text.line_detector import (
    _is_ghost_punctuation_line,
    _line_center,
    _mark_ghost_punctuation_candidates,
    _merge_script_lines,
    _reconcile_with_textpage,
    _collapse_spaced_capital_runs,
    reconstruct_native_lines,
)


def test_line_center_uses_shared_baseline_for_different_glyph_heights() -> None:
    normal = _char(index=0, text="a", bbox=BBox(0.0, 5.0, 5.0, 10.0), angle=0.0)
    ascender = _char(index=1, text="i", bbox=BBox(6.0, 2.0, 8.0, 10.0), angle=0.0)

    assert _line_center(normal, median_height=5.0) == _line_center(ascender, median_height=5.0)


def _char(
    *,
    index: int,
    text: str,
    bbox: BBox,
    angle: float,
) -> NativeCharacter:
    return NativeCharacter(
        page_index=0,
        char_index=index,
        text=text,
        unicode_codepoint=ord(text),
        bbox=bbox,
        angle=angle,
    )


def test_vertical_reverse_inferred_gap_has_valid_bbox():
    characters = (
        _char(
            index=0,
            text="A",
            bbox=BBox(
                100.0,
                100.0,
                110.0,
                110.0,
            ),
            angle=3 * math.pi / 2,
        ),
        _char(
            index=1,
            text="B",
            bbox=BBox(
                100.0,
                75.0,
                110.0,
                85.0,
            ),
            angle=3 * math.pi / 2,
        ),
    )

    lines = reconstruct_native_lines(characters)

    assert len(lines) == 1

    whitespace_tokens = [
        token
        for token in lines[0].tokens
        if TokenFlag.WHITESPACE_INFERRED in token.flags
    ]

    assert len(whitespace_tokens) == 1

    gap = whitespace_tokens[0].bbox

    assert gap.x0 <= gap.x1
    assert gap.y0 <= gap.y1

    assert gap.y0 == 85.0
    assert gap.y1 == 100.0


def test_horizontal_reverse_inferred_gap_has_valid_bbox():
    characters = (
        _char(
            index=0,
            text="A",
            bbox=BBox(
                100.0,
                100.0,
                110.0,
                110.0,
            ),
            angle=math.pi,
        ),
        _char(
            index=1,
            text="B",
            bbox=BBox(
                75.0,
                100.0,
                85.0,
                110.0,
            ),
            angle=math.pi,
        ),
    )

    lines = reconstruct_native_lines(characters)

    assert len(lines) == 1

    whitespace_tokens = [
        token
        for token in lines[0].tokens
        if TokenFlag.WHITESPACE_INFERRED in token.flags
    ]

    assert len(whitespace_tokens) == 1

    gap = whitespace_tokens[0].bbox

    assert gap.x0 <= gap.x1
    assert gap.y0 <= gap.y1

    assert gap.x0 == 85.0
    assert gap.x1 == 100.0


# ── P1-1: ghost punctuation preserved pre-ledger ──────────────────────────────

def _text_line(text: str, width: float = 8.0, height: float = 10.0) -> TextLine:
    bbox = BBox(0.0, 0.0, width, height)
    token = TextToken(
        text=text,
        bbox=bbox,
        sources=[EvidenceRef(SourceKind.NATIVE_PDF, 0, f"ghost-test:{text}")],
        confidence=1.0,
        normalized_text=normalize_text(text),
    )
    return TextLine(
        tokens=[token],
        bbox=bbox,
        baseline=Baseline(y=height),
        direction=WritingDirection.LEFT_TO_RIGHT,
        native_order_min=0,
        native_order_max=0,
    )


def test_ghost_punctuation_line_is_marked_not_filtered() -> None:
    """A comma in a narrow box is marked as a candidate, never removed."""
    comma = _text_line(",", width=6.0, height=8.0)
    result = _mark_ghost_punctuation_candidates([comma])

    assert len(result) == 1
    assert result[0].ghost_punctuation_candidate is True


def test_ghost_punctuation_survives_reconstruct_native_lines() -> None:
    """After full reconstruction, ghost-candidate lines must be present in output."""
    characters = (
        _char(index=0, text="A", bbox=BBox(0.0, 0.0, 10.0, 10.0), angle=0.0),
        _char(index=1, text=",", bbox=BBox(12.0, 3.0, 17.0, 8.0), angle=0.0),
    )
    lines = reconstruct_native_lines(characters)

    texts = [ln.text.strip() for ln in lines]
    assert "," in " ".join(texts) or any("," in t for t in texts), (
        "Comma must not be silently removed before the conservation ledger"
    )


def test_non_ghost_punctuation_is_not_marked() -> None:
    """A colon in a wide box (e.g. form label) is not flagged as ghost."""
    wide_colon = _text_line(":", width=30.0, height=10.0)
    result = _mark_ghost_punctuation_candidates([wide_colon])

    assert result[0].ghost_punctuation_candidate is False


def test_ghost_candidate_flag_false_by_default() -> None:
    """TextLine.ghost_punctuation_candidate defaults to False."""
    line = _text_line("texto normal")
    assert line.ghost_punctuation_candidate is False


def test_is_ghost_punctuation_line_detects_narrow_comma() -> None:
    """_is_ghost_punctuation_line correctly identifies a narrow comma."""
    narrow = _text_line(",", width=6.0, height=8.0)
    wide = _text_line(",", width=30.0, height=8.0)

    assert _is_ghost_punctuation_line(narrow) is True
    assert _is_ghost_punctuation_line(wide) is False


# ── P1-2: script merge lineage ────────────────────────────────────────────────

def _script_candidate(text: str, x: float, y: float, line_id: str) -> TextLine:
    """Single-character line positioned for script-merge detection."""
    bbox = BBox(x, y, x + 6.0, y + 6.0)
    token = TextToken(
        text=text,
        bbox=bbox,
        sources=[EvidenceRef(SourceKind.NATIVE_PDF, 0, f"script:{line_id}")],
        confidence=1.0,
        normalized_text=normalize_text(text),
    )
    return TextLine(
        tokens=[token],
        bbox=bbox,
        baseline=Baseline(y=y + 6.0),
        direction=WritingDirection.LEFT_TO_RIGHT,
        native_order_min=0,
        native_order_max=0,
        line_id=line_id,
    )


def _body_line(text: str, x: float, y: float, line_id: str, height: float = 12.0) -> TextLine:
    """Standard body line taller than a script candidate."""
    bbox = BBox(x, y, x + len(text) * 8.0, y + height)
    token = TextToken(
        text=text,
        bbox=bbox,
        sources=[EvidenceRef(SourceKind.NATIVE_PDF, 0, f"body:{line_id}")],
        confidence=1.0,
        normalized_text=normalize_text(text),
    )
    return TextLine(
        tokens=[token],
        bbox=bbox,
        baseline=Baseline(y=y + height),
        direction=WritingDirection.LEFT_TO_RIGHT,
        native_order_min=1,
        native_order_max=1,
        line_id=line_id,
    )


def test_script_merge_records_source_lineage() -> None:
    """Consumed candidate line_id must appear in target.merged_source_line_ids."""
    body = _body_line("E", x=0.0, y=0.0, line_id="body-line")
    # '2' sits above the body line (subscript check: cy < body.cy → superscript)
    exponent = _script_candidate("2", x=4.0, y=-4.0, line_id="exponent-line")

    result = _merge_script_lines([body, exponent])

    # The exponent must have been consumed into the body line.
    surviving_ids = [ln.line_id for ln in result]
    assert "exponent-line" not in surviving_ids

    merged = next((ln for ln in result if ln.line_id == "body-line"), None)
    assert merged is not None, "body line must survive as the merge target"
    assert "exponent-line" in merged.merged_source_line_ids


def test_script_merge_does_not_consume_baseline_hyphen() -> None:
    body = _body_line("2 controles", x=0.0, y=0.0, line_id="body-line")
    hyphen = _script_candidate("-", x=8.0, y=6.0, line_id="hyphen-line")

    result = _merge_script_lines([body, hyphen])

    assert [line.text for line in result] == ["2 controles", "-"]


def test_no_merge_when_candidate_has_no_tall_neighbor() -> None:
    """An isolated single-char line with no matching neighbor keeps its lineage clean."""
    isolated = _script_candidate("2", x=0.0, y=0.0, line_id="isolated")

    result = _merge_script_lines([isolated])

    assert len(result) == 1
    assert result[0].line_id == "isolated"
    assert result[0].merged_source_line_ids == ()


def test_body_line_merged_source_ids_empty_by_default() -> None:
    """A line that consumes nothing has an empty merged_source_line_ids tuple."""
    body = _body_line("texto", x=0.0, y=0.0, line_id="plain-body")

    result = _merge_script_lines([body])

    assert result[0].merged_source_line_ids == ()


# ── P1-3: textpage reconciliation order-aware ─────────────────────────────────

def _recon_line(text: str, line_id: str) -> TextLine:
    """Minimal TextLine for reconciliation tests."""
    bbox = BBox(0.0, 0.0, len(text) * 8.0, 10.0)
    token = TextToken(
        text=text,
        bbox=bbox,
        sources=[EvidenceRef(SourceKind.NATIVE_PDF, 0, line_id)],
        confidence=1.0,
        normalized_text=normalize_text(text),
    )
    return TextLine(
        tokens=[token],
        bbox=bbox,
        baseline=Baseline(y=10.0),
        direction=WritingDirection.LEFT_TO_RIGHT,
        native_order_min=0,
        native_order_max=0,
        line_id=line_id,
    )


def test_reconciliation_does_not_swap_consecutive_similar_ids() -> None:
    """Lines like ABC-001 and ABC-002 must not swap due to a global score match."""
    lines = [
        _recon_line("ABC-001", "id-001"),
        _recon_line("ABC-002", "id-002"),
    ]
    extracted = "ABC-001\nABC-002"

    result = _reconcile_with_textpage(lines, extracted)

    assert result[0].text == "ABC-001"
    assert result[1].text == "ABC-002"


def test_reconciliation_monotonic_second_occurrence_not_stolen() -> None:
    """A repeated phrase in two positions keeps its local candidate, not the first."""
    lines = [
        _recon_line("Referência", "ref-1"),
        _recon_line("Outro texto", "other"),
        _recon_line("Referência", "ref-2"),
    ]
    extracted = "Referência\nOutro texto\nReferência"

    result = _reconcile_with_textpage(lines, extracted)

    texts = [ln.text for ln in result]
    assert texts == ["Referência", "Outro texto", "Referência"]


def test_reconciliation_spacing_recovery_still_works() -> None:
    """Tracked text missing spaces is corrected by the textpage candidate.

    When the native stream has no space between words (e.g. 'entradaFundos'),
    _needs_textpage_spacing_recovery detects the lowercase→uppercase transition
    and allows the textpage version ('entrada Fundos') to replace it.
    """
    tracked = _recon_line("entradaFundos", "tracked-line")
    extracted = "entrada Fundos"

    result = _reconcile_with_textpage([tracked], extracted)

    assert result[0].text == "entrada Fundos"


def test_reconciliation_uses_native_order_for_rotated_lines() -> None:
    body = replace(
        _recon_line("Corpo horizontal", "body"),
        native_order_min=20,
        native_order_max=35,
    )
    rotated = replace(
        _recon_line("RÓTULO019 • LEITURA INVERTIDA", "rotated"),
        native_order_min=1,
        native_order_max=19,
    )

    result = _reconcile_with_textpage(
        [body, rotated],
        "RÓTULO 019 • LEITURA INVERTIDA\nCorpo horizontal",
    )

    assert [line.text for line in result] == [
        "Corpo horizontal",
        "RÓTULO 019 • LEITURA INVERTIDA",
    ]


def test_reconciliation_recovers_spaces_between_uppercase_words() -> None:
    tracked = _recon_line("RÓTULO019 • LEITURAINVERTIDA", "tracked-label")
    extracted = "RÓTULO 019 • LEITURA INVERTIDA"

    result = _reconcile_with_textpage([tracked], extracted)

    assert result[0].text == extracted


def test_reconciliation_advances_past_exact_matches_before_spacing_recovery() -> None:
    """Exact matches consume candidates so later lines remain in the local window."""
    native_lines = [
        _recon_line(f"Linha {index}", f"line-{index}")
        for index in range(1, 9)
    ]
    native_lines.append(_recon_line("entradaFundos", "tracked-line"))
    extracted = "\n".join(
        [*(f"Linha {index}" for index in range(1, 9)), "entrada Fundos"]
    )

    result = _reconcile_with_textpage(native_lines, extracted)

    assert result[8].text == "entrada Fundos"


def test_reconciliation_advances_past_more_than_twelve_exact_matches() -> None:
    """A correction remains reachable after a longer run of exact matches."""
    exact_count = 13
    native_lines = [
        _recon_line(f"Item {index}", f"item-{index}")
        for index in range(exact_count)
    ]
    native_lines.append(_recon_line("custoTotal", "tracked-total"))
    extracted = "\n".join(
        [*(f"Item {index}" for index in range(exact_count)), "custo Total"]
    )

    result = _reconcile_with_textpage(native_lines, extracted)

    assert result[-1].text == "custo Total"


def test_reconciliation_does_not_replace_when_no_good_match() -> None:
    """A native line with no high-similarity candidate keeps its original text."""
    line = _recon_line("Conteúdo original", "orig")
    extracted = "Completamente diferente"

    result = _reconcile_with_textpage([line], extracted)

    assert result[0].text == "Conteúdo original"
    assert result[0].text_override is None


def test_wide_standalone_spaced_capital_stamp_is_rejoined() -> None:
    stamp = _text_line("R A S C U N H O", width=240.0, height=24.0)

    result = _collapse_spaced_capital_runs([stamp])

    assert result[0].text == "RASCUNHO"


def test_compact_acronym_sequence_is_not_rejoined() -> None:
    acronym = _text_line("U S A I D", width=42.0, height=10.0)

    result = _collapse_spaced_capital_runs([acronym])

    assert result[0].text == "U S A I D"
