#!/usr/bin/env python3
"""Deterministic, synthetic, native-PDF text stress corpus and independent reference.

Needs: reportlab; DejaVu TrueType fonts installed locally (not bundled).
Output: one 200-page PDF + JSONL units + JSON manifest.
This generator is strictly test-only, never imported by extraction runtime.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from hashlib import sha256
import json
import math
from pathlib import Path
import random

from reportlab.lib.pagesizes import A4, letter, legal, landscape
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

SEED = 20260919
FAMILIES = (
    'typography', 'unicode', 'identifiers', 'hierarchy', 'paragraphs',
    'lists', 'two_columns', 'three_columns', 'asymmetric_columns', 'sidebar',
    'header_footer', 'edge_margins', 'grid_table', 'borderless_table',
    'merged_table', 'financial_table', 'continued_table_part1',
    'continued_table_part2', 'rotated_text', 'mixed_enterprise',
)
VARIANTS = 10
PAGES = len(FAMILIES) * VARIANTS
FONTS = {
    'Sans': 'DejaVuSans.ttf',
    'SansBold': 'DejaVuSans-Bold.ttf',
    'SansItalic': 'DejaVuSans-Oblique.ttf',
    'SansBoldItalic': 'DejaVuSans-BoldOblique.ttf',
    'Serif': 'DejaVuSerif.ttf',
    'SerifBold': 'DejaVuSerif-Bold.ttf',
    'Mono': 'DejaVuSansMono.ttf',
}
FONT_DIR = Path('/usr/share/fonts/truetype/dejavu')

PARAGRAPHS = (
    'O parecer de conformidade descreve obrigações contratuais, revisões técnicas, responsáveis e prazos de execução.',
    'A revisão do processo deve considerar documentos assinados, evidências verificáveis e a integridade de cada anexo.',
    'As áreas de finanças, compras e auditoria registraram valores distintos para o mesmo período, sem suprimir justificativas.',
    'Cada seção apresenta um identificador específico que precisa sobreviver à quebra de linhas, à classificação e à serialização.',
    'O plano de continuidade estabelece indicadores mensuráveis e exige rastreabilidade entre a cláusula e sua tabela de apoio.',
    'A reconciliação financeira exige que valores, moedas, sinais negativos e casas decimais permaneçam vinculados à linha correta.',
)
SPECIAL_LINES = (
    'Português: ação, órgão, aviação, coração, útil, amanhã, cobrança, eficiência e investigação.',
    'English: procurement, accountability, billing, branch, requirements and traceability.',
    'Español: revisión, información, gestión, verificación, resolución y contabilidad.',
    'Français: conformité, révision, échéance, année, élève et responsabilité.',
    'Deutsch: Straße, Größe, Fußgänger, Überprüfung, München und Öffentlichkeit.',
    'Ελληνικά: Αθήνα, ανάλυση, αριθμός, μελέτη και πρότυπο.',
    'Кириллица: Москва, документ, проверка, таблица и контроль.',
    'Compostos: A/B/C, alfa-beta, estado–membro, co-operar, pré-processamento e e-mail.',
    'Pontuação: ! ? , . ; : ( ) [ ] { } < > / \\ | ~ ^ * # % & @ + = - _ ` " \'',
    'Símbolos: € £ ¥ R$ § ¶ © ® ™ ° º ª × ÷ ≤ ≥ ≠ ± ∑ ∆ √ ∞ → ← ↔.',
    'Unidades: 0,25 kg; 17,5%; R$ 1.234,56; 30 km/h; 05:06:07; 2026-09-19.',
)


def register_fonts() -> None:
    for family, filename in FONTS.items():
        file = FONT_DIR / filename
        if not file.exists():
            raise RuntimeError(
                f'Missing installed font: {file}. Install DejaVu TrueType fonts '
                '(e.g. sudo apt install fonts-dejavu-core); font files are NOT bundled.'
            )
        pdfmetrics.registerFont(TTFont(f'Stress{family}', str(file)))


def wrap_words(text: str, font: str, size: float, width: float) -> list[str]:
    if not text:
        return ['']
    result, current = [], ''
    for word in text.split(' '):
        candidate = f'{current} {word}' if current else word
        if pdfmetrics.stringWidth(candidate, font, size) <= width:
            current = candidate
            continue
        if current:
            result.append(current)
        current = ''
        while word and pdfmetrics.stringWidth(word, font, size) > width:
            cut = len(word)
            while cut > 1 and pdfmetrics.stringWidth(word[:cut], font, size) > width:
                cut -= 1
            result.append(word[:cut])
            word = word[cut:]
        current = word
    if current:
        result.append(current)
    return result or ['']


class Page:
    def __init__(self, pdf: canvas.Canvas, page: int, family: str, variant: int,
                 width: float, height: float, units: list[dict], tables: list[dict],
                 regions: list[dict], tags: list[str]):
        self.pdf = pdf
        self.index = page
        self.family = family
        self.variant = variant
        self.w = width
        self.h = height
        self.units = units
        self.tables = tables
        self.regions = regions
        self.tags = tags
        self.seq = 0
        self.draw_seq = 0
        self.region_seq = 0
        self.table_seq = 0
        self.unit_in_region = defaultdict(int)
        self.rng = random.Random(SEED + page * 37)
        self.scale = 0.94 if min(width, height) < 520 else 1.0
        self.m = 29 if family != 'edge_margins' else 3.5
        self.top = 38 if family != 'edge_margins' else 19
        self.content_bottom = height - (32 if family != 'edge_margins' else 15)
        self.title_region = self.region('page_title', order=1)

    def region(self, kind: str, order: int, *, parent: str | None = None,
               column: int | None = None, label: str | None = None) -> str:
        self.region_seq += 1
        identifier = f'P{self.index:03d}-R{self.region_seq:03d}'
        self.regions.append(dict(region_id=identifier, page=self.index,
                                 kind=kind, logical_order=order, parent_id=parent,
                                 column=column, label=label))
        return identifier

    def line(self, text: str, x: float, top: float, *, region: str, role: str = 'text',
             size: float = 9.2, font: str = 'Sans', align: str = 'left',
             angle: float = 0.0, cell: str | None = None, table: str | None = None,
             color: tuple[float, float, float] = (0.12, 0.16, 0.23),
             annotation: str | None = None, style: str | None = None) -> float:
        font_name = 'Stress' + font
        size *= self.scale
        width = pdfmetrics.stringWidth(text, font_name, size)
        if align == 'right':
            x -= width
        elif align == 'center':
            x -= width / 2
        ascent, descent = pdfmetrics.getAscentDescent(font_name, size)
        baseline_y = self.h - top - ascent
        self.pdf.saveState()
        self.pdf.setFillColorRGB(*color)
        self.pdf.setFont(font_name, size)
        self.pdf.translate(x, baseline_y)
        if angle:
            self.pdf.rotate(angle)
        self.pdf.drawString(0, 0, text)
        self.pdf.restoreState()
        th = math.radians(angle)
        corners = [(0, descent), (width, descent), (0, ascent), (width, ascent)]
        pdf_points = [
            (x + dx * math.cos(th) - dy * math.sin(th),
             baseline_y + dx * math.sin(th) + dy * math.cos(th))
            for dx, dy in corners
        ]
        bbox = [round(min(pt[0] for pt in pdf_points), 2),
                round(self.h - max(pt[1] for pt in pdf_points), 2),
                round(max(pt[0] for pt in pdf_points), 2),
                round(self.h - min(pt[1] for pt in pdf_points), 2)]
        # This is a font-metric estimate, NOT an independently measured ink bbox.
        if bbox[0] < -0.8 or bbox[1] < -0.8 or bbox[2] > self.w + 0.8 or bbox[3] > self.h + 0.8:
            raise AssertionError(f'PDF text extends beyond page {self.index}: {text!r}, bbox={bbox}, {self.w}x{self.h}')
        self.seq += 1
        self.unit_in_region[region] += 1
        self.units.append(dict(
            unit_id=f'P{self.index:03d}-U{self.seq:04d}', page=self.index,
            exact_text=text, source_kind='native_pdf_text', visible=True,
            region_id=region, role=role, region_line_order=self.unit_in_region[region],
            source_draw_order=self.seq, table_id=table, cell_id=cell,
            bbox_top_origin_pt=bbox, bbox_status='font_metric_estimate',
            font_family=font, font_size_pt=round(size, 2), rotation_deg=angle,
            logical_reading_order=None, annotation=annotation, style=style,
        ))
        return top + size * 1.2

    def paragraph(self, text: str, x: float, top: float, width: float, *, region: str,
                  size: float = 9.3, font: str = 'Sans', role: str = 'body',
                  leading: float | None = None, cell: str | None = None,
                  table: str | None = None, annotation: str | None = None) -> float:
        leading = leading or size * self.scale * 1.42
        for ln in wrap_words(text, 'Stress'+font, size*self.scale, width):
            self.line(ln, x, top, region=region, size=size, font=font,
                      role=role, cell=cell, table=table, annotation=annotation)
            top += leading
        return top

    def title(self, text: str, *, size: float = 15.5) -> None:
        self.line(text, self.m, self.top, region=self.title_region,
                  role='heading', size=size, font='SansBold', style='heading_1')

    def framing(self) -> None:
        head = self.region('header', 0)
        foot = self.region('footer', 999)
        self.line('NATIVE TEXT FIDELITY • CONFIDENTIALIDADE DE TESTE',
                  self.m, 11, region=head, role='repeated_header', size=6.1)
        self.line(f'PROTOCOLO {self.index:03d} / {PAGES:03d} • {self.family.upper()}',
                  self.w-self.m, self.h-12, region=foot, role='repeated_footer',
                  size=6.1, align='right')

    def tag(self, text: str, region: str, x: float | None = None, top: float | None = None) -> None:
        self.line(f'CASE-{self.index:03d} • {text}', x if x is not None else self.m,
                  top if top is not None else self.top+27,
                  region=region, role='case_marker', size=7, font='Mono')

    def text_box(self, text: str, x: float, top: float, width: float, *, region: str,
                 role: str = 'callout', size: float = 8.3) -> float:
        lines = wrap_words(text, 'StressSans', size*self.scale, width-12)
        box_h = max(30, len(lines)*size*self.scale*1.44+12)
        self.pdf.saveState()
        self.pdf.setStrokeColorRGB(0.55,0.63,0.73)
        self.pdf.setFillColorRGB(0.95,0.97,0.99)
        self.pdf.roundRect(x, self.h-top-box_h, width, box_h, 4, stroke=1, fill=1)
        self.pdf.restoreState()
        self.paragraph(text, x+6, top+5, width-12, region=region,
                       role=role, size=size)
        return top+box_h+5

    def table(self, rows: list[list[dict]], x: float, top: float, widths: list[float],
              *, region: str, grid: bool = True, label: str = '',
              table_id: str | None = None, row_offset: int = 0,
              header_rows: int = 1, continued: bool = False) -> float:
        if table_id is None:
            self.table_seq += 1
            table_id = f'P{self.index:03d}-T{self.table_seq:02d}'
        entry = dict(table_id=table_id, page=self.index, region_id=region,
                     description=label, grid=grid, n_columns=len(widths),
                     header_rows=header_rows, continued=continued, cells=[])
        self.tables.append(entry)
        colx = [x]
        for width in widths:
            colx.append(colx[-1] + width)

        # Pre-layout first, so row-spanning cells have correct geometry and
        # don't collide with cells introduced on the following row.
        layout: list[list[tuple]] = []
        row_heights: list[float] = []
        occupied_until = [-1] * len(widths)
        for r, row in enumerate(rows):
            selected: list[tuple] = []
            col = 0
            for item in row:
                while col < len(widths) and occupied_until[col] >= r:
                    col += 1
                colspan = int(item.get('colspan', 1))
                rowspan = int(item.get('rowspan', 1))
                if rowspan < 1 or colspan < 1 or r + rowspan > len(rows):
                    raise AssertionError(f'Invalid table span on page {self.index}')
                if col + colspan > len(widths) or any(occupied_until[c] >= r for c in range(col, col+colspan)):
                    raise AssertionError(f'Table span overlaps occupied cell on page {self.index}')
                cw = colx[col+colspan]-colx[col]
                fs = item.get('size', 7.25)
                font = 'SansBold' if r < header_rows or item.get('bold') else item.get('font', 'Sans')
                lines = wrap_words(item['text'], 'Stress'+font, fs*self.scale, cw-9)
                selected.append((col, colspan, rowspan, item, lines, cw, fs, font))
                for c in range(col, col+colspan):
                    occupied_until[c] = r + rowspan - 1
                col += colspan
            if any(end < r for end in occupied_until):
                raise AssertionError(f'Incomplete table row {r} on page {self.index}')
            layout.append(selected)
            row_heights.append(max(18.0*self.scale,
                                   max((len(lines)*fs*self.scale*1.35+9 for _,_,span,_,lines,_,fs,_ in selected if span == 1), default=18.0*self.scale)))
        # Make sure any spanning cell has enough collective height for its text.
        for r, selected in enumerate(layout):
            for _, _, rowspan, _, lines, _, fs, _ in selected:
                if rowspan > 1:
                    needed = len(lines)*fs*self.scale*1.35 + 9
                    available = sum(row_heights[r:r+rowspan])
                    if available < needed:
                        row_heights[r+rowspan-1] += needed-available
        edges = [top]
        for height in row_heights:
            edges.append(edges[-1]+height)
        if edges[-1] > self.content_bottom:
            raise AssertionError(f'Table overflows page {self.index}, family={self.family}: {edges[-1]:.1f} > {self.content_bottom:.1f}')

        for r, selected in enumerate(layout):
            for col, colspan, rowspan, item, lines, cw, fs, font in selected:
                cell_id = f'{table_id}-p{self.index:03d}-r{row_offset+r:02d}c{col:02d}'
                c_top=edges[r]; c_bottom=edges[r+rowspan]
                cell = dict(cell_id=cell_id, table_id=table_id, page=self.index,
                            row_index=(0 if continued and r < header_rows else row_offset+r),
                            column_index=col, rowspan=rowspan,
                            colspan=colspan, exact_text=item['text'], text_unit_ids=[],
                            bbox_top_origin_pt=[round(colx[col],2), round(c_top,2),
                                                round(colx[col+colspan],2), round(c_bottom,2)],
                            is_repeated_header=(continued and r < header_rows),
                            style='header' if r < header_rows else 'body')
                entry['cells'].append(cell)
                if grid:
                    self.pdf.saveState()
                    if r < header_rows:
                        self.pdf.setFillColorRGB(0.91,0.94,0.98)
                        self.pdf.rect(colx[col], self.h-c_bottom, cw, c_bottom-c_top, fill=1, stroke=0)
                    self.pdf.setStrokeColorRGB(0.56,0.64,0.72)
                    self.pdf.rect(colx[col], self.h-c_bottom, cw, c_bottom-c_top, fill=0, stroke=1)
                    self.pdf.restoreState()
                yy=c_top+4.5
                for line_text in lines:
                    self.line(line_text,colx[col]+4.5,yy,region=region,
                              role='table_header' if r < header_rows else 'table_cell',
                              size=fs,font=font,table=table_id,cell=cell_id)
                    cell['text_unit_ids'].append(self.units[-1]['unit_id'])
                    yy+=fs*self.scale*1.35
        return edges[-1]+5


def page_size(family: str, variant: int) -> tuple[float,float]:
    if family in ('grid_table','borderless_table','merged_table','financial_table',
                  'continued_table_part1','continued_table_part2','mixed_enterprise'):
        return (A4, letter, landscape(A4), landscape(letter), legal)[variant % 5]
    if family == 'edge_margins':
        return (A4, letter, landscape(A4), legal)[variant % 4]
    return (A4, letter, landscape(A4), landscape(letter), legal, (540,735))[variant % 6]


def body_region(page: Page, kind: str = 'body', order: int = 10, column: int | None = None) -> str:
    return page.region(kind, order, column=column)


def emit_typography(page: Page) -> None:
    reg = body_region(page)
    y = page.top+28
    page.tag('Fontes, tamanhos, pesos e estilos', reg)
    for i, (font, sz) in enumerate((('Sans',5.5),('SansBold',7),('SansItalic',8.6),('Serif',10.4),
                                   ('SerifBold',12.5),('Mono',6.8),('SansBoldItalic',15))):
        y += (12 if i==0 else 8)
        y=page.paragraph(f'Camada textual {i+1}: índice {page.index:03d} - ação, revisão e conformidade.',
                         page.m+4, y, page.w-2*page.m-8, region=reg,
                         role='typography_sample', size=sz, font=font)+5
    y += 14
    page.line('KERNING  AVANÇADO   ESPAÇAMENTO    Duplo', page.m+4,y,
              region=reg,role='intentional_multiple_spaces',size=8.0)
    y += 19
    for s in ('Superíndice: x², m³, custo¹ e H₂O; não consumir algarismos independentes.',
              'Palavras compostas: micro-serviço, guarda-chuva, pré-contratação, coautor.',
              'Espaçamentos: ALFA BETA GAMMA | ALFA  BETA  GAMMA | A  B C.',
              'Cadeia de prova: uma palavra isolada também deve manter a identidade.'):
        y=page.paragraph(s,page.m+4,y,page.w-2*page.m-8,region=reg,size=8.1)+8


def emit_unicode(page: Page) -> None:
    reg=body_region(page); y=page.top+37
    page.tag('Unicode, símbolos, línguas e pontuação',reg)
    sample=list(SPECIAL_LINES)
    page.rng.shuffle(sample)
    for line in sample:
        y=page.paragraph(line,page.m+4,y,page.w-2*page.m-8,region=reg,size=8)+7
    if y+35<page.content_bottom:
        page.line('Ligaturas: fi ﬁ | fl ﬂ | ff ﬀ | e\u0301 é | espaço\u00a0fixo | espaço\u2009fino',
                  page.m+4,y,region=reg,role='unicode_normalization',size=8.0)
        y += 18
        page.line('Caracteres isolados: ,  .  :  ;  +  -  =  1  2  3  §  ¶',
                  page.m+4,y,region=reg,role='isolated_characters',size=8.1)


def emit_identifiers(page: Page) -> None:
    reg=body_region(page); y=page.top+38
    page.tag('IDs, sinais, decimais, datas e campos',reg)
    samples=(
        f'FAT-{page.index:03d}-00001874 | FAT-{page.index:03d}-00001875 | FAT-{page.index:03d}-00001876',
        'Valores próximos: R$ 1.000,00; R$ 1.000,01; R$ 1.000,10; R$ -1.000,10.',
        'Datas e horas: 01/02/2026; 02/01/2026; 2026-09-19; 23:59:59; 00:00:00.',
        'CEP 01310-100 | CNPJ sintético 12.345.678/0001-90 | processo 0001/2026-A.',
        'UUID 550e8400-e29b-41d4-a716-446655440000.',
        'E-mail auditoria.exemplo@example.org | URL https://example.org/p?id=2&v=10.',
        'Zeros significativos: 00007 | 00700 | 07000 | 7,000 | 7.000.',
        'Operações: -10 + 20 = 10 | 10 − 2 = 8 | 10±2 | 0,0001.',
        'Sequências: 0123456789 / 9876543210 / 0123456788 / 0123456787.',
        'Notação: 1,25 × 10³; 2.000,00; 2,000.00; -0,01; (1.234,56).',
    )
    for idx,text in enumerate(samples):
        y=page.paragraph(f'{idx+1:02d}. {text}',page.m+3,y,page.w-2*page.m-6,region=reg,
                         role='identifier_sample',size=8.5)+9


def emit_hierarchy(page: Page) -> None:
    y=page.top+34
    reg=body_region(page,'hierarchy')
    for lvl,size in ((2,12.8),(3,11.2),(4,9.9),(5,9.0),(6,8.4)):
        page.line(f'{lvl}. Seção de nível {lvl} - controles e responsabilidades',page.m+2+(lvl-2)*7,
                  y,region=reg,role='heading',size=size,font='SansBold',style=f'heading_{lvl}')
        y+=size*page.scale*1.8
        y=page.paragraph(PARAGRAPHS[(lvl+page.variant)%len(PARAGRAPHS)],page.m+3+(lvl-2)*7,
                         y,page.w-2*page.m-22,region=reg,size=8.2)+9
    if y+40<page.content_bottom:
        page.line('Nota lateral textual: o conteúdo não é um título só por ser curto.',
                  page.m+10,y,region=reg,role='note',size=8.0,font='SansItalic')


def emit_paragraphs(page: Page) -> None:
    reg=body_region(page); y=page.top+35
    page.tag('Quebra de linha, sequência e parágrafos',reg)
    for i in range(4):
        txt=' '.join(PARAGRAPHS[(page.variant+i+j)%len(PARAGRAPHS)] for j in range(2))
        y=page.paragraph(f'Parágrafo {i+1}. {txt}',page.m+3,y,page.w-2*page.m-6,
                         region=reg,size=8.5,annotation=f'paragraph-{i+1}')+12
    if y+24<page.content_bottom:
        page.line('Fim de parágrafo antes do rodapé - verificação de conservação.',
                  page.m+3,y,region=reg,size=8.1)


def emit_lists(page: Page) -> None:
    reg=body_region(page,'list'); y=page.top+37
    page.tag('Listas aninhadas, marcadores e continuidade',reg)
    items=(('• Categoria Alfa - início do processo',0),
           ('  Subitem Alfa.1 - primeira verificação documental',1),
           ('    1. Nível numérico - valor 0010',2),
           ('    2. Nível numérico - valor 0020',2),
           ('  Subitem Alfa.2 - exceção e evidência complementar',1),
           ('• Categoria Beta - monitoramento',0),
           ('  [x] Tarefa concluída: BETA-OK',1),
           ('  [ ] Tarefa pendente: BETA-PENDENTE',1),
           ('A. Item alfabético de primeiro nível',0),
           ('  a) Subitem alfabético de segundo nível',1),
           ('I. Item romano de primeiro nível',0),
           ('  i) Subitem romano de segundo nível',1),
           ('- Linha curta com marcador isolado',0),
           ('  Continuação do item anterior em linha independente.',1))
    for idx,(txt,level) in enumerate(items):
        yy=page.paragraph(txt,page.m+4+level*13,y,page.w-2*page.m-8-level*13,
                          region=reg,role='list_item' if idx!=13 else 'list_continuation',size=8.3)
        y=yy+5


def columns(page: Page, count: int, asym: bool = False) -> None:
    gap=17 if count<=2 else 11
    area=page.w-2*page.m
    if count==2:
        widths=[(area-gap)*(.34 if asym else .5),(area-gap)*(.66 if asym else .5)]
    else:
        widths=[(area-2*gap)/3]*3
    starts=[page.m]
    for wi in widths[:-1]: starts.append(starts[-1]+wi+gap)
    regs=[page.region('column',10+i,column=i+1) for i in range(count)]
    # Intentionally write PDF stream in reverse visual order for half the variants.
    for i in (range(count-1,-1,-1) if page.variant%2 else range(count)):
        r=regs[i]; x=starts[i]; width=widths[i]; y=page.top+42
        for j in range(6 if count==3 else 8):
            src=PARAGRAPHS[(i*2+j+page.variant)%len(PARAGRAPHS)]
            short=(' '.join(src.split()[:14]) if count==3 else ' '.join(src.split()[:19]))
            y=page.paragraph(f'COLUNA {i+1}, BLOCO {j+1}. {short}',x+1,y,width-2,
                             region=r,size=7.3 if count==3 else 8.0)+6.5
        if y > page.content_bottom-5:
            raise AssertionError(f'Column overflow on page {page.index}: {y:.1f}')
    end=page.region('spanning_or_footnote',20)
    if page.h>650:
        page.text_box(f'NOTA TRANSVERSAL {page.index:03d}: este quadro deve vir após todas as colunas.',
                      page.m,page.h-114,page.w-2*page.m,region=end,size=7.7)


def emit_sidebar(page: Page) -> None:
    main=page.region('main_flow',10); side=page.region('sidebar',20)
    page.tag('Sidebar: sequência principal antes da nota lateral',main)
    gap=13; side_width=max(96,(page.w-2*page.m)*.25)
    body_width=page.w-2*page.m-side_width-gap
    side_first=page.variant%2 == 1
    def body():
        y=page.top+47
        for i in range(7):
            y=page.paragraph(f'TEXTO PRINCIPAL {i+1:02d}. {PARAGRAPHS[i%len(PARAGRAPHS)]}',
                             page.m+2,y,body_width-5,region=main,size=7.7)+8
        if y>page.content_bottom:raise AssertionError(f'sidebar body overflow {page.index}')
    def side_fn():
        x=page.w-page.m-side_width
        page.text_box(f'BARRA LATERAL {page.index:03d}. {PARAGRAPHS[1]}',
                      x,page.top+67,side_width,region=side,size=7.2)
        yy=page.top+160
        for j in range(3):
            yy=page.paragraph(f'SIDE-{j+1}: {PARAGRAPHS[j+2]}',x+4,yy,side_width-8,
                              region=side,size=7)+7
    if side_first:side_fn();body()
    else:body();side_fn()


def emit_header_footer(page: Page) -> None:
    main=body_region(page); page.tag('Cabeçalho, rodapé, repetição e conteúdo único',main)
    y=page.top+44
    page.line('CONFIDENCIAL',page.m+4,y,region=main,role='unique_body_same_as_furniture',size=10,font='SansBold')
    y+=25
    for txt in ('O cabeçalho repetido e a linha de corpo acima devem permanecer como ocorrências distintas.',
                'Números de contratos variam por página sem se tornarem um rodapé repetido por semelhança.',
                f'Contrato singular: FIN-{page.index:04d}-83092026 | revisão 000{page.variant+1}.'):
        y=page.paragraph(txt,page.m+4,y,page.w-2*page.m-8,region=main,size=8.6)+13
    footnote=page.region('footnote',90)
    page.line(f'NOTA DE RODAPÉ {page.index:03d}: taxa 27,4; referência local 0009.',
              page.m+4,page.h-49,region=footnote,role='footnote',size=7.3)


def emit_edge(page: Page) -> None:
    reg=body_region(page); page.tag('Área útil sem margens artificiais',reg)
    page.line(f'BORDA SUPERIOR {page.index:03d}: nenhuma palavra deve desaparecer.',
              2.6,0.8,region=reg,size=6.2,role='edge_top')
    page.line(f'BORDA ESQUERDA {page.index:03d}: texto legítimo em x mínimo.',
              2.6,page.h*.32,region=reg,size=6.4,role='edge_left')
    page.line(f'BORDA DIREITA {page.index:03d}: código {page.index:04d}.',
              page.w-2.5,page.h*.45,region=reg,size=6.4,align='right',role='edge_right')
    page.line(f'BORDA INFERIOR {page.index:03d}: prova de retenção.',
              2.7,page.h-7.4,region=reg,size=5.8,role='edge_bottom')
    y=page.top+38
    for i in range(6):
        y=page.paragraph(PARAGRAPHS[(i+page.variant)%len(PARAGRAPHS)],
                         4,y,page.w-8,region=reg,size=8.3)+9


def table_region(page: Page, kind: str) -> tuple[str,float,float,list[float]]:
    reg=page.region(kind,10)
    x=page.m+2; top=page.top+42; total=page.w-2*page.m-4
    return reg,x,top,[total*.22,total*.30,total*.23,total*.25]


def emit_grid_table(page: Page) -> None:
    reg,x,y,widths=table_region(page,'grid_table')
    rows=[[{'text':v} for v in ('Código','Descrição do item','Quantidade','Preço')]]
    for i in range(1,13):
        rows.append([{'text':f'ITM-{page.index:03d}-{i:03d}'},
                     {'text':f'Equipamento auditável {i:02d} de revisão e garantia'},
                     {'text':f'{(i*7):05d}'},{'text':f'R$ {i*103:04d},{i*7%100:02d}'}])
    page.table(rows,x,y,widths,region=reg,grid=True,label='grade com texto nativo')


def emit_borderless_table(page: Page) -> None:
    reg,x,y,widths=table_region(page,'borderless_table')
    page.line('Tabela sem bordas: campos devem manter suas colunas.',x,y,region=reg,size=8,font='SansBold')
    y+=19
    rows=[[{'text':v} for v in ('Centro','Conta','Período','Saldo')]]
    for i in range(1,12):
        rows.append([{'text':f'C-{i:03d}'},{'text':f'CC-{page.index:03d}-{i:04d}'},
                     {'text':f'2026-{(i%12)+1:02d}'},{'text':f'({i*371:05d},0{i%10})'}])
    page.table(rows,x,y,widths,region=reg,grid=False,label='sem bordas com trilhas')


def emit_merged_table(page: Page) -> None:
    reg,x,y,widths=table_region(page,'merged_table')
    widths=[widths[0],widths[1],widths[2],widths[3]]
    rows=[
        [{'text':f'Resumo consolidado {page.index:03d}', 'colspan':4}],
        [{'text':'Centro','rowspan':2},{'text':'Indicadores financeiros','colspan':2},{'text':'Notas','rowspan':2}],
        [{'text':'Receita'},{'text':'Despesa'}],
    ]
    for i in range(1,10):
        rows.append([{'text':f'U-{i:02d}'},{'text':f'{i*1001:05d},12'},
                     {'text':f'{i*300:04d},90'},{'text':f'Nota {i:02d}, ramo {page.variant:02d}'}])
    page.table(rows,x,y,widths,region=reg,grid=True,label='merged headers',header_rows=3)


def emit_financial_table(page: Page) -> None:
    reg,x,y,widths=table_region(page,'financial_table')
    widths=[widths[0]*.82,widths[1]*1.42,widths[2]*.84,widths[3]*1.0]
    normalizer=(page.w-2*page.m-4)/sum(widths)
    widths=[q*normalizer for q in widths]
    rows=[[{'text':v} for v in ('Conta','Descrição','Débito','Crédito')]]
    for i in range(1,12):
        debit=f'-{i*129:05d},{i%10}{i%7}' if i%3==0 else f'{i*129:05d},{i%10}{i%7}'
        credit=f'({i*207:05d},{i%10}{i%7})' if i%4==0 else f'{i*207:05d},{i%10}{i%7}'
        rows.append([{'text':f'0{i:03d}'},{'text':f'Centro de custo {i:02d}'},
                     {'text':debit},{'text':credit}])
    rows.append([{'text':'TOTAL','bold':True},{'text':'Resultado líquido','bold':True},
                 {'text':'0123456,78','bold':True},{'text':'0876543,21','bold':True}])
    page.table(rows,x,y,widths,region=reg,grid=page.variant%2==0,label='financial exact values')


def emit_continued(page: Page, second: bool) -> None:
    reg,x,y,widths=table_region(page,'continued_table')
    series=(page.variant+1)
    # page variants are same for consecutive part 1 and part 2.
    tid=f'SERIES-{series:02d}-CONTINUED-TABLE'
    if second:
        page.line(f'CONTINUAÇÃO DA TABELA {tid} - página 2/2',x,y,region=reg,size=8.7,font='SansBold')
        y+=18
    else:
        page.line(f'INÍCIO DA TABELA {tid} - página 1/2',x,y,region=reg,size=8.7,font='SansBold')
        y+=18
    rows=[[{'text':v} for v in ('Linha','Referência','Categoria','Valor')]]
    start=13 if second else 1
    for i in range(start,start+12):
        rows.append([{'text':f'{i:03d}'},{'text':f'DOC-{series:02d}-{i:04d}'},
                     {'text':f'Classe {i%5}'},{'text':f'{i*601:05d},{i%10}{(i+1)%10}'}])
    page.table(rows,x,y,widths,region=reg,table_id=tid,grid=True,header_rows=1,
               continued=second,label='paired consecutive pages',row_offset=(12 if second else 0))
    note=page.region('table_note',20)
    page.line(f'NOTA {tid}: a continuidade não deve duplicar os valores.',x,page.h-71,
              region=note,role='table_note',size=7.3)


def emit_rotated(page: Page) -> None:
    reg=body_region(page); page.tag('Rotação, direção e notas marginais nativas',reg)
    page.line(f'ALONG LEFT EDGE {page.index:03d} • TEXTO 90 GRAUS',
              17,page.h*.69,region=reg,size=7.0,angle=90,role='vertical_90')
    page.line(f'ALONG RIGHT EDGE {page.index:03d} • TEXTO 270 GRAUS',
              page.w-17,page.h*.29,region=reg,size=7.0,angle=270,role='vertical_270')
    page.line(f'RÓTULO {page.index:03d} • LEITURA INVERTIDA',
              page.w-29,page.h*.67,region=reg,size=7.5,angle=180,role='rotation_180')
    y=page.top+46
    for i in range(6):
        y=page.paragraph(f'CORPO HORIZONTAL {i+1}. {PARAGRAPHS[i%len(PARAGRAPHS)]}',
                         page.m+19,y,page.w-2*page.m-38,region=reg,size=8.1)+10


def emit_mixed(page: Page) -> None:
    reg=body_region(page); page.tag('Tabela + sidebar + duas colunas + footer próprio',reg)
    left=page.region('column',10,column=1)
    right=page.region('column',11,column=2)
    gap=15; full=page.w-2*page.m; width=(full-gap)*.5
    if page.variant%2:
        emitting=[(right,page.m+width+gap,2),(left,page.m,1)]
    else:
        emitting=[(left,page.m,1),(right,page.m+width+gap,2)]
    for region,x,i in emitting:
        y=page.top+44
        for j in range(4):
            y=page.paragraph(f'COL {i} / TEXTO {j+1}. {PARAGRAPHS[(i+j)%len(PARAGRAPHS)]}',
                             x,y,width,region=region,size=7.4)+6
    table_reg=page.region('embedded_table',20)
    table_y=page.h*.59
    tw=[full*.2,full*.25,full*.28,full*.27]
    rows=[[{'text':v} for v in ('ID','Responsável','Prazo','Valor')]]
    for k in range(1,7):
        rows.append([{'text':f'X{k:03d}'},{'text':f'Equipe {k:02d}'},
                     {'text':f'2026-10-{k:02d}'},{'text':f'R$ {k*318:04d},00'}])
    page.table(rows,page.m,table_y,tw,region=table_reg,grid=page.variant%2==0,
               label='embedded full-width table after columns')
    note=page.region('footnote',90)
    page.line(f'Nota lateral inferior {page.index:03d}: importante para auditoria.',
              page.m,page.h-48,region=note,role='footnote',size=7.0)


HANDLERS = {
    'typography': emit_typography,
    'unicode': emit_unicode,
    'identifiers': emit_identifiers,
    'hierarchy': emit_hierarchy,
    'paragraphs': emit_paragraphs,
    'lists': emit_lists,
    'two_columns': lambda p: columns(p,2),
    'three_columns': lambda p: columns(p,3),
    'asymmetric_columns': lambda p: columns(p,2,True),
    'sidebar': emit_sidebar,
    'header_footer': emit_header_footer,
    'edge_margins': emit_edge,
    'grid_table': emit_grid_table,
    'borderless_table': emit_borderless_table,
    'merged_table': emit_merged_table,
    'financial_table': emit_financial_table,
    'continued_table_part1': lambda p: emit_continued(p,False),
    'continued_table_part2': lambda p: emit_continued(p,True),
    'rotated_text': emit_rotated,
    'mixed_enterprise': emit_mixed,
}


def generate(output_dir: Path, prefix: str = 'Document_Text_Stress_V1') -> dict:
    register_fonts()
    output_dir.mkdir(parents=True, exist_ok=True)
    pdf_path=output_dir/(prefix+'.pdf')
    refs_path=output_dir/(prefix+'.reference.jsonl')
    manifest_path=output_dir/(prefix+'.manifest.json')
    ref_json_path=output_dir/(prefix+'.reference.json')
    pdf=canvas.Canvas(str(pdf_path),pageCompression=1,invariant=1,
                      pdfVersion=(1,4),enforceColorSpace='RGB')
    pdf.setTitle('PDFExtractor - Native Text Stress V1 (test-only)')
    pdf.setAuthor('Synthetic deterministic test corpus')
    pdf.setSubject('Native text coverage and geometry independent reference')
    pages=[]
    all_units=[]
    all_tables=[]
    all_regions=[]
    for variant in range(VARIANTS):
        for family in FAMILIES:
            n=len(pages)+1
            w,h=page_size(family,variant)
            pdf.setPageSize((w,h))
            units=[];tables=[];regions=[]
            page=Page(pdf,n,family,variant,w,h,units,tables,regions,tags=[family,f'variant_{variant+1}'])
            page.framing()
            page.title(f'{family.replace("_"," ").upper()} • repetição {variant+1}/10')
            HANDLERS[family](page)
            # Every page must carry a unique searchable text marker, including
            # pages whose family-specific handler has no case marker.
            if not any(f'CASE-{n:03d}' in unit['exact_text'] for unit in units):
                case_region = page.region('case_marker', order=2)
                page.line(f'CASE-{n:03d} • native digital text', page.m,
                          page.top+27, region=case_region,
                          role='case_marker', size=7.0, font='Mono')
            region_rank={r['region_id']:r['logical_order'] for r in regions}
            units.sort(key=lambda u:(region_rank[u['region_id']],u['region_line_order']))
            for idx,u in enumerate(units,1):
                u['logical_reading_order']=idx
            record=dict(page=n,family=family,variant=variant+1,
                        page_size_pt=[round(w,2),round(h,2)],tags=page.tags,
                        regions=regions,units=units,tables=tables,
                        expected_visible_units=len(units),
                        expected_native_units=len(units),
                        expected_structural_tables=len(tables),
                        expected_repeated_header=True,expected_repeated_footer=True,
                        normalization_policy='exact_source_text; explicit visual reading order',
                        geometry_note='Text bbox from embedded font metrics, not independent rendered ink measurement')
            pages.append(record)
            all_units.extend(units);all_tables.extend(tables);all_regions.extend(regions)
            pdf.showPage()
    pdf.save()
    with refs_path.open('w',encoding='utf-8',newline='\n') as file:
        for p in pages:
            file.write(json.dumps(p,ensure_ascii=False,separators=(',',':'))+'\n')
    ref_json_path.write_text(json.dumps({'schema':'pdfextractor.native_text_stress.reference.v1',
                                         'pages':pages},ensure_ascii=False,separators=(',',':'))+'\n',
                             encoding='utf-8')
    counter=Counter(page['family'] for page in pages)
    obj=dict(schema='pdfextractor.native_text_stress.v1',seed=SEED,pages=len(pages),
             pdf_filename=pdf_path.name,reference_filename=refs_path.name,
             reference_json_filename=ref_json_path.name,
             page_families=dict(counter),unit_count=len(all_units),
             region_count=len(all_regions),table_count=len(all_tables),
             cell_count=sum(len(t['cells']) for t in all_tables),
             unique_page_marker_format='CASE-NNN',
             native_only=True,images_in_corpus=0,
             generated_from='test-only generator; reference from drawing intents, never PDF extraction',
             cautions=[
                'Reference records authored drawing intents; do not train or hardcode parser against values or page coordinates.',
                'The PDF source stream can differ from the reference logical order; use region/line logical order.',
                'Text bbox is estimated from font ascent/descent; validate actual ink separately if needed.',
                'A PDF-native character may be represented differently by extraction libraries (ligatures/Unicode normalization).',
                'Rowspan/colspan metadata describes cell intent; Markdown alone cannot prove merged-cell fidelity.',
                'All intentional printable text is native PDF text; no OCR is necessary for this suite.',
             ])
    obj['pdf_sha256']=sha256(pdf_path.read_bytes()).hexdigest()
    obj['reference_sha256']=sha256(refs_path.read_bytes()).hexdigest()
    obj['reference_json_sha256']=sha256(ref_json_path.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return dict(pdf=str(pdf_path),reference=str(refs_path),reference_json=str(ref_json_path),
                manifest=str(manifest_path),**obj)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',type=Path,default=Path(__file__).resolve().parent)
    args=parser.parse_args()
    generated=generate(args.output_dir)
    print(json.dumps({k:v for k,v in generated.items() if k not in {'page_families','cautions'}},ensure_ascii=False,indent=2))
