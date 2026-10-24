"""Tables must divide surrounding prose even after OCR lines are rehomed."""
from structured_pdf_text.assemble.content import _emit_table_block, _place_visual_tables_between_prose
from structured_pdf_text.document import (Baseline, ContentKind, LayoutRegion, PageContentBlock, RegionDecision,
    RegionKind, RegionQuality, StructuredTable, TableCell, TableFragment, TableMethod,
    TextLine, TextToken, WritingDirection)
from structured_pdf_text.geometry import BBox


def _line(text, box):
    token = TextToken(text, box, [], .99, text, evidence_id=text)
    return TextLine([token], box, Baseline(box.y1), WritingDirection.LEFT_TO_RIGHT,
                    None, None, line_id=text)


def _fixtures(table_box=BBox(10, 60, 190, 100)):
    lines = [_line('Introdução', BBox(10, 20, 190, 30)),
             _line('Conclusão', BBox(10, 120, 190, 130))]
    region = LayoutRegion('prose', RegionKind.TEXT, BBox(10, 20, 190, 130), .9,
                          [], [], RegionQuality(RegionDecision.KEEP_NATIVE), ocr_lines=lines)
    token = TextToken('R$ -1,00', table_box, [], .99, 'R$ -1,00', evidence_id='cell')
    table = StructuredTable('table', [TableFragment(0, table_box, 0, 0)],
        [TableCell(0, 0, 1, 1, table_box, token.text, [token], .99)],
        1, 1, .9, TableMethod.VISUAL_MODEL)
    return region, table, lines


def _prose_blocks(lines):
    return [PageContentBlock(line.line_id, 0, ContentKind.TEXT, line.bbox, index,
                             line.text, source_region_ids=['prose'], line_ids=[line.line_id])
            for index, line in enumerate(lines)]


def test_table_is_emitted_between_straddling_prose_without_source_table_lines():
    region, table, lines = _fixtures()
    blocks = _prose_blocks(lines)
    original_ids = [value for block in blocks for value in block.line_ids]
    blocks.append(_emit_table_block(table, 0, region))
    ordered = _place_visual_tables_between_prose(blocks, [table])
    assert [b.kind for b in ordered] == [ContentKind.TEXT, ContentKind.TABLE, ContentKind.TEXT]
    assert [b.text for b in ordered] == ['Introdução', 'R$ -1,00', 'Conclusão']
    assert [value for block in ordered for value in block.line_ids] == original_ids
    assert _place_visual_tables_between_prose(ordered, [table]) == ordered


def test_table_in_another_column_is_not_inserted_into_neighboring_prose():
    region, table, lines = _fixtures(BBox(180, 60, 350, 100))
    blocks = _prose_blocks(lines)
    blocks.append(_emit_table_block(table, 0, region))
    assert _place_visual_tables_between_prose(blocks, [table]) == blocks


def test_later_paragraph_above_table_prevents_premature_table_placement():
    region, table, lines = _fixtures(BBox(10, 150, 190, 190))
    lines.append(_line('Rodapé', BBox(10, 400, 190, 410)))
    region.bbox = BBox(10, 20, 190, 410)
    blocks = _prose_blocks(lines)
    blocks.insert(2, _emit_table_block(table, 0, region))
    # Introduction and conclusion are above the table; footer is below it.
    assert _place_visual_tables_between_prose(blocks, [table]) == blocks


def test_native_table_order_is_not_changed_by_raster_table_placement():
    region, table, lines = _fixtures()
    table.method = TableMethod.STRICT_GRID
    blocks = _prose_blocks(lines)
    blocks.append(_emit_table_block(table, 0, region))
    assert _place_visual_tables_between_prose(blocks, [table]) == blocks
