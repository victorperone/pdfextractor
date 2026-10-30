"""Run 010 review: running headers/footers that leaked on OCR, rotated and skewed pages."""
import pytest

from structured_pdf_text.assemble.repeated_regions import detect_repeated_headers_footers
from structured_pdf_text.document import (EvidenceRef, LayoutRegion, PageDiagnostics, PageStrategy, RegionDecision,
                                          RegionKind, RegionQuality, SourceKind, StructuredPage, TextLine, TextToken,
                                          WritingDirection)
from structured_pdf_text.geometry import BBox

WIDTH, HEIGHT = 595.0, 842.0
FOOTER = 'RODAPÉ DECORATIVO OMITIR NO GABARITO'


def _line(text, box, *, page, native=False, rotation=0, n=0):
    kind = SourceKind.NATIVE_PDF if native else SourceKind.OCR_PAGE
    line_id = f"{'native' if native else 'ocr'}:{page}:{n}"
    token = TextToken(text, BBox(*box), [EvidenceRef(kind, page, line_id)], 1.0, text, rotation=rotation)
    return TextLine([token], BBox(*box), None, WritingDirection.LEFT_TO_RIGHT, None, None, line_id=line_id)


def _page(index, lines, *, kind=RegionKind.TEXT, rotation=0, native=False):
    region = LayoutRegion('r', kind, BBox(0, 0, WIDTH, HEIGHT), 1.0, lines if native else [], [],
                          RegionQuality(RegionDecision.KEEP_NATIVE), ocr_lines=[] if native else lines)
    diagnostics = PageDiagnostics(page_index=index, strategy=PageStrategy.NATIVE, reasons=[], native_chars=0,
                                  native_text_length=0)
    return StructuredPage(page_index=index, bbox=BBox(0, 0, WIDTH, HEIGHT), regions=[region], tables=[],
                          raw_text='', reading_text='', diagnostics=diagnostics, page_rotation=rotation)


def _footer(index, y=820.0, text=FOOTER, **kwargs):
    return _page(index, [_line(text, (60, y, 330, y + 9), page=index)], **kwargs)


def _pages(confirmed):
    return {page for pages in confirmed.values() for page in pages}


def test_one_shifted_scan_no_longer_vetoes_the_footer_on_every_page():
    # Run 009, V4: p107/p108 read the footer at 0.941 against 0.98 elsewhere,
    # and the same signature leaked on p92/p116/p124/p128.
    pages = [_footer(n) for n in range(5)] + [_footer(5, y=788.0), _footer(6, y=788.0)]
    assert _pages(detect_repeated_headers_footers(pages)) == set(range(7))


def test_body_line_far_from_the_band_is_not_dragged_into_furniture():
    pages = [_footer(n) for n in range(4)] + [_footer(4, y=700.0)]
    assert _pages(detect_repeated_headers_footers(pages)) == {0, 1, 2, 3}


def test_a_band_of_two_pages_cannot_overrule_an_unstable_signature():
    pages = [_footer(0), _footer(1), _footer(2, y=790.0), _footer(3, y=760.0)]
    assert detect_repeated_headers_footers(pages) == {}


def test_native_furniture_keeps_the_strict_stability_rule():
    pages = [_page(n, [_line(FOOTER, (60, y, 330, y + 9), page=n, native=True)], native=True)
             for n, y in enumerate([820.0] * 5 + [788.0])]
    assert detect_repeated_headers_footers(pages) == {}


def test_footer_inside_a_region_labelled_header_is_still_a_footer():
    # Run 009, V4 p213/p214: OCR layout labelled the bottom band "header".
    pages = [_footer(n) for n in range(3)] + [_footer(3, kind=RegionKind.HEADER)]
    assert _pages(detect_repeated_headers_footers(pages)) == {0, 1, 2, 3}


@pytest.mark.parametrize('text', ['RODAPE DECORATIVO OMITIR NO GABARITO Pagina 91 Ge 224',
                                  'RODAPÉ DECORATIVO OHITIA KO GABARITO Paglna B5 de 224'])
def test_misread_page_counter_joins_the_running_footer(text):
    pages = [_footer(n) for n in range(3)] + [_footer(3, text=text)]
    assert _pages(detect_repeated_headers_footers(pages)) == {0, 1, 2, 3}


def test_numbers_that_are_not_a_page_counter_stay_semantic():
    pages = [_footer(n, text='Relatório de obra repetição 1') for n in range(3)]
    pages.append(_footer(3, text='Relatório de obra repetição 2'))
    assert 3 not in _pages(detect_repeated_headers_footers(pages))


@pytest.mark.parametrize('rotation', [90, 180, 270])
def test_footer_of_an_image_rotated_inside_an_upright_scan_is_found(rotation):
    # V3 p96-p99: the scan holds the page turned by a quarter turn; OCR reads
    # the footer rotated, on another physical edge of the upright page.
    angle = (-rotation) % 360
    width, height = (HEIGHT, WIDTH) if rotation % 180 else (WIDTH, HEIGHT)  # reading frame
    reading = BBox(60, height - 22, 330, height - 13)
    # The page box whose reading-frame image is ``reading``.
    page_box = reading.rotate_to_visual((360 - angle) % 360, width, height)
    assert page_box.rotate_to_visual(angle, WIDTH, HEIGHT) == reading
    rotated = _page(3, [_line(FOOTER, (page_box.x0, page_box.y0, page_box.x1, page_box.y1), page=3,
                              rotation=rotation)])
    pages = [_footer(n, y=HEIGHT - 22) for n in range(3)] + [rotated]
    assert _pages(detect_repeated_headers_footers(pages)) == {0, 1, 2, 3}


def test_furniture_drawn_in_user_space_of_a_rotate_page_is_found():
    # V6 p31/p47/p75: /Rotate 90 page whose running header was drawn in the
    # unrotated user space, vertical along a side of the visual page.
    header = 'IRINF / DOSSIÊ DE INFRAESTRUTURA'
    upright = [_page(n, [_line(header, (48, 18, 230, 27), page=n, native=True)], native=True) for n in range(3)]
    turned = _page(3, [_line(header, (48, 18, 230, 27), page=3, native=True)], native=True, rotation=90)
    assert _pages(detect_repeated_headers_footers(upright + [turned])) == {0, 1, 2, 3}


def test_vertical_body_text_of_a_rotate_page_is_not_furniture():
    upright = [_page(n, [_line('Texto lateral de corpo', (48, 18, 230, 27), page=n, native=True)], native=True)
               for n in range(3)]
    # On the turned page the same words run across the page body, not along an edge.
    body = _page(3, [_line('Texto lateral de corpo', (300, 200, 309, 600), page=3, native=True)], native=True,
                 rotation=90)
    assert 3 not in _pages(detect_repeated_headers_footers(upright + [body]))


def test_rotated_scan_at_another_proportion_does_not_unsettle_the_upright_pages():
    # V3 p116/p117: the rotated image is scaled to fit the page; its header is
    # at the edge of its reading frame, yet at another relative height. It
    # joined the group and made p117's header leak in the first candidate.
    header = 'CORPUS INTEGRADO V3 LABORATÓRIO SINTÉTICO OCR TABELAS SCAN'
    pages = [_page(n, [_line(header, (60, 12, 400, 21), page=n)]) for n in range(3)]
    reading = BBox(60, 35, 400, 44)  # edge band of the 595 pt reading height, centre 0.066 vs 0.020
    page_box = reading.rotate_to_visual(90, HEIGHT, WIDTH)
    turned = _page(3, [_line(header, (page_box.x0, page_box.y0, page_box.x1, page_box.y1), page=3, rotation=90)])
    assert _pages(detect_repeated_headers_footers(pages + [turned])) == {0, 1, 2, 3}
    assert _pages(detect_repeated_headers_footers(pages[:2] + [turned])) == {0, 1, 3}


def test_rotate_pages_whose_native_furniture_shares_one_frame_are_compared_together():
    # V4 p213/p214: the only native occurrences of a header are on /Rotate
    # pages, read in user space; with no page-frame position left to measure,
    # the second candidate lost them (and the OCR page sharing the header).
    header = 'CORPUS STRESS OCR MARKDOWN V4 / LABORATÓRIO SINTÉTICO'
    scan = _page(0, [_line(header, (48, 12, 330, 20), page=0)])
    turned = [_page(n, [_line(header, (48, 12, 330, 20), page=n, native=True)], native=True, rotation=90)
              for n in (1, 2)]
    assert _pages(detect_repeated_headers_footers([scan] + turned)) == {0, 1, 2}
