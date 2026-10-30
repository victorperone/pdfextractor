"""Priority fixes: evidence-backed cell recovery, grids and rotated prose."""
from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import Mock

import numpy as np
import pytest

from structured_pdf_text.api import _refine_table_cells_ocr
from structured_pdf_text.document import (OcrToken, SourceKind, TableCell, TableMethod,
    TextToken, TextLine, WritingDirection)
from structured_pdf_text.geometry import BBox
from structured_pdf_text.ocr.pipeline.coordinator import OCRPageFacade
from structured_pdf_text.ocr.pipeline.policies import CellRecoveryPolicy
from structured_pdf_text.ocr.pipelines.easyocr.layout import order_rotated_columns
from structured_pdf_text.ocr.pipelines.easyocr.policy import CELL_POLICY, FUSION_POLICY
from structured_pdf_text.ocr.pipelines.easyocr.recognition import recover_signed_currency
from structured_pdf_text.ocr.pipelines.easyocr.structure import EasyOCRTableStructureEngine
from structured_pdf_text.ocr.backends.easyocr import _best_candidate


def token(text,confidence=.90,box=(10,10,150,40),rotation=0):
    return OcrToken(text,BBox(*box),confidence,'pt-BR',SourceKind.OCR_PAGE,rotation=rotation)


def candidates():
    return [('default',[token('R$ 350,00',.98)]),('high_recall',[token('-Rs 350,00')]),
            ('otsu',[token('-Rs 350,00',.85)])]


@pytest.mark.parametrize('text,confidence,accepted',[
    ('-R$ 350,00',.95,True),('R$ -350,00',.90,True),('R$ 350,00',.99,False),
    ('-R$ 351,00',.99,False),('-R$ 350,00',.70,False),('-R$ 350,00',None,False),('-Rs 350,00',.99,False),
    ('-R$ 3,500,0',.99,False),('-R$ 0350,00',.99,False)])
def test_signed_currency_requires_an_entire_observed_direct_read(text,confidence,accepted):
    raw=[([[0,0],[140,0],[140,30],[0,30]],text,confidence)]
    reader=NS(recognize=Mock(return_value=raw));before=candidates();image=np.full((80,200),255,np.uint8)
    after=recover_signed_currency(reader,image,before,page_index=0,language='pt-BR')
    assert reader.recognize.call_count==1
    assert (len(after)>len(before))==accepted
    if accepted:
        result=after[-1][1][0]
        assert result.text==text and result.confidence==confidence
        assert 0<=result.bbox.x0<result.bbox.x1<=200
        assert [t.text for t in _best_candidate(after)[0]]==[text]
    assert before[0][1][0].text=='R$ 350,00'


def test_unconfirmed_negative_or_untyped_marker_does_not_trigger_inference():
    reader=NS(recognize=Mock())
    for groups in [candidates()[:2], [('a',[token('-Rs 350,00')]),('b',[token('-Rs 350,00')])]]:
        assert recover_signed_currency(reader,np.ones((80,200),np.uint8),groups,page_index=0,language='pt-BR')==groups
    reader.recognize.assert_not_called()


@pytest.mark.parametrize('text',['+R$ 350,00','R$ +350,00'])
def test_explicit_positive_currency_does_not_trigger_negative_only_verification(text):
    reader=NS(recognize=Mock())
    groups=candidates()
    groups[0]=('default',[token(text,.95)])
    assert recover_signed_currency(reader,np.ones((80,200),np.uint8),groups,page_index=0,language='pt-BR')==groups
    reader.recognize.assert_not_called()


def test_direct_gpu_failure_is_propagated_to_the_existing_retry_boundary():
    exc=RuntimeError('CUDA library not found');reader=NS(recognize=Mock(side_effect=exc))
    def propagate(error):raise error
    with pytest.raises(RuntimeError,match='CUDA library'):
        recover_signed_currency(reader,np.zeros((80,200),np.uint8),candidates(),
                                page_index=0,language='pt-BR',propagate_error=propagate)


def test_empty_numeric_cell_finds_the_second_tier_header_and_obeys_page_budget():
    cells=[TableCell(0,0,1,2,BBox(0,0,200,20),'Semestre',[],.8),
           TableCell(1,0,1,1,BBox(0,20,100,40),'Quantidade',[],.9)]
    cells += [TableCell(row,0,1,1,BBox(0,row*20,100,(row+1)*20),'',[],.35) for row in range(2,30)]
    table=NS(header_rows=[],cells=cells,table_id='raster',method=TableMethod.VISUAL_MODEL)
    calls=[];facade=OCRPageFacade(NS(pipeline=NS(cell_policy=CELL_POLICY)))
    facade.refine_cell=lambda **kwargs:calls.append(kwargs) or True
    assert _refine_table_cells_ocr(engine=facade,page_image=None,page_index=0,
        page_bbox=BBox(0,0,200,400),tables=[table],quality_policy='adaptive',automatic=True)==16
    assert all(call['header_text']=='Quantidade' for call in calls)
    assert all(call['cell'].text=='' for call in calls)
    table.method=TableMethod.STRICT_GRID
    calls.clear()
    _refine_table_cells_ocr(engine=facade,page_image=None,page_index=0,
        page_bbox=BBox(0,0,200,400),tables=[table],quality_policy='adaptive',automatic=True)
    assert not calls
    assert not CellRecoveryPolicy().automatic_recovery


@pytest.mark.parametrize('data,accepted',[
    ([('Nome','Descrição'),('Alfa','Uma descrição longa de vários termos explicativos'),
      ('Beta','Outra descrição longa de vários termos adicionais')],True),
    ([('Coluna esquerda','Coluna direita'),('Texto extenso que prossegue nesta coluna inteira','Outro texto extenso que prossegue nesta coluna inteira'),
      ('Mais um parágrafo completo com muitas palavras','Mais um parágrafo completo com muitas palavras')],False)])
def test_borderless_track_admission_distinguishes_fields_from_prose(data,accepted):
    table=NS(header_rows=[0],cells=[NS(row=r,col=c,text=text) for r,row in enumerate(data) for c,text in enumerate(row)])
    assert EasyOCRTableStructureEngine().accept_ocr_tracks(table)==accepted


def make_line(text,box,rotation):
    t=TextToken(text,box,[],.90,text,rotation=rotation)
    return TextLine([t],box,None,WritingDirection.UNKNOWN,None,None,line_id=text)


@pytest.mark.parametrize('angle',[90,180,270])
def test_rotated_columns_are_ordered_without_mutating_evidence(angle):
    width,height=600,800
    boxes=[BBox(x,100+i*25,x+200,120+i*25) for i in range(3) for x in [40,330]]
    texts=[f'Texto completo da coluna {side} na linha {i}' for i in range(3) for side in ['esquerda','direita']]
    physical=[box.rotate_to_visual(angle,width,height) for box in boxes]
    page=BBox(0,0,height if angle in (90,270) else width,width if angle in (90,270) else height)
    lines=[make_line(text,box,angle) for text,box in zip(texts,physical)]
    result=order_rotated_columns(lines,page)
    assert [line.text for line in result]==texts[::2]+texts[1::2]
    assert {id(line) for line in result}=={id(line) for line in lines}
    assert [line.bbox for line in lines]==physical
    assert all(line.tokens[0].rotation==angle for line in lines)


def test_native_unrotated_layout_keeps_the_default_column_policy():
    from structured_pdf_text.layout.ocr_aware import _order_ocr_lines_by_columns
    lines=[make_line('Texto simples de uma linha',BBox(10,100,500,120),0)]
    assert order_rotated_columns(lines,BBox(0,0,600,800))==_order_ocr_lines_by_columns(lines,BBox(0,0,600,800))


def test_unproven_rotated_lanes_keep_the_default_column_policy():
    from structured_pdf_text.layout.ocr_aware import _order_ocr_lines_by_columns
    lines=[make_line('Texto simples de uma linha',BBox(10,100,500,120),90),
           make_line('Outra linha sem duas colunas',BBox(10,130,500,150),90)]
    assert order_rotated_columns(lines,BBox(0,0,600,800))==_order_ocr_lines_by_columns(lines,BBox(0,0,600,800))


def test_cross_variant_boundary_uses_observed_coherent_tail():
    prefix=token('O lote apresenta ação informação,',.94,(10,10,300,40))
    short=token('coração e saúde.',.96,(298,12,440,42))
    overlap=token('informação; coração e saúde.',.93,(220,12,440,42))
    out,diag=_best_candidate([('default',[prefix,short]),('sharpen',[prefix,overlap])])
    assert sorted(t.text for t in out)==sorted([prefix.text,short.text])
    assert sum(d['boundary_segmentation_repairs'] for d in diag)==1
    assert out[1].bbox==short.bbox and out[1].confidence==short.confidence


def test_real_repeated_word_in_distinct_boxes_is_preserved():
    first=token('Uma expressão realmente muito',.94,(10,10,220,40))
    second=token('muito importante para todos',.94,(225,10,440,40))
    out,_=_best_candidate([('default',[first,second])])
    assert [t.text for t in out]==[first.text,second.text]


@pytest.mark.parametrize('angle',[0,90,180,270])
def test_coherent_tail_retains_word_spacing_after_overlapping_punctuation(angle):
    from structured_pdf_text.ocr.reconstruct import reconstruct_ocr_lines
    from structured_pdf_text.ocr.pipelines.easyocr.layout import prepare_layout_lines
    from structured_pdf_text.document import TokenFlag
    prefix=token('O lote apresenta ação informação,',.94,(10,10,300,40))
    short=token('coração e saúde.',.96,(298,12,440,42))
    overlap=token('informação; coração e saúde.',.93,(220,12,440,42))
    prefix,short,overlap=[replace(t,bbox=t.bbox.rotate_to_visual(angle,500,100),rotation=angle)
                          for t in (prefix,short,overlap)]
    out,_=_best_candidate([('default',[prefix,short]),('sharpen',[prefix,overlap])])
    page=BBox(0,0,100,500) if angle%180 else BBox(0,0,500,100)
    lines=reconstruct_ocr_lines(out,0,page)
    result=prepare_layout_lines(lines,page)
    assert result[0].text=='O lote apresenta ação informação, coração e saúde.'
    assert lines[0].text=='O lote apresenta ação informação,coração e saúde.'
    gap=next(t for t in result[0].tokens if TokenFlag.WHITESPACE_INFERRED in t.flags)
    assert gap.text==' ' and gap.transformation_type=='inferred_whitespace'
    assert sorted(t.text for t in out)==sorted([prefix.text,short.text])


def test_unmarked_overlapping_punctuation_keeps_legacy_spacing():
    from structured_pdf_text.ocr.reconstruct import reconstruct_ocr_lines
    from structured_pdf_text.ocr.pipelines.easyocr.layout import prepare_layout_lines
    lines=reconstruct_ocr_lines([token('Uma frase completa,',box=(10,10,300,40)),
                                 token('outro trecho',box=(298,12,440,42))],0,BBox(0,0,500,100))
    assert prepare_layout_lines(lines,BBox(0,0,500,100))[0] is lines[0]


def test_uniform_shadow_recovery_keeps_raster_frame_and_does_not_invent_a_grid():
    policy=EasyOCRTableStructureEngine()
    image=np.tile(np.linspace(120,255,800,dtype=np.uint8),(1000,1))
    recovered=policy.prepare_recovery_image(image)
    assert recovered.shape==image.shape
    assert policy.recognize(recovered) is None


def test_cell_recovery_never_loses_a_sign_from_a_malformed_marker():
    proposed=CELL_POLICY.select([[token('R$ 350,00',.99)]],current_text='-Rs 350,00',
        cell_bbox=BBox(0,0,200,50),header_text='Valor')
    assert not proposed


@pytest.mark.parametrize('quality,expected',[(None,'greedy'),('baseline','greedy'),('adaptive','beamsearch'),('exhaustive','beamsearch')])
def test_direct_verification_uses_one_beam_pass_only_when_requested(quality,expected):
    from structured_pdf_text.ocr.backends.easyocr import EasyOCRBackend
    engine=object.__new__(EasyOCRBackend)
    engine._closed=False;engine._decoder='greedy';engine._beamwidth=5
    engine._allowlist=engine._blocklist=None;engine._workers=0;engine._language='pt-BR'
    engine._reader=NS(recognize=Mock(return_value=[]))
    engine.reset_page_diagnostics()
    engine.recognize_direct(np.zeros((30,100),np.uint8),0,BBox(0,0,100,30),quality_policy=quality)
    assert engine._reader.recognize.call_count==1
    assert engine._reader.recognize.call_args.kwargs['decoder']==expected


def test_direct_currency_verification_uses_the_actual_clockwise_correction():
    groups=[(name,[replace(t,rotation=270) for t in tokens]) for name,tokens in candidates()]
    image=np.arange(80*200,dtype=np.uint8).reshape(80,200)
    reader=NS(recognize=Mock(return_value=[([], '-R$ 350,00', .95)]))
    after=recover_signed_currency(reader,image,groups,page_index=0,language='pt-BR')
    actual=reader.recognize.call_args.args[0][8:-8,8:-8]
    assert np.array_equal(actual,np.rot90(image[7:43,7:153],3))
    assert after[-1][1][0].rotation==270


@pytest.mark.parametrize('title',['Página 080 | OCR de texto','Pagina 80','página 080 - teste'])
def test_body_page_title_does_not_impersonate_a_page_delimiter(title):
    from structured_pdf_text.renderers.markdown import _render_heading
    from structured_pdf_text.evaluation.reference_inputs import document_pages
    from structured_pdf_text.evaluation.markdown_syntax import markdown_tokens
    heading=_render_heading('##',title)
    text='## Página 80\n\n'+heading+'\n\nTexto real.'
    assert set(document_pages(text))=={80}
    inline=next(t for t in markdown_tokens(heading) if t.type=='inline')
    assert ''.join(t.content for t in inline.children)==title
    assert _render_heading('###',title).startswith('### '+title)


def test_geometry_confidence_does_not_make_a_reliable_identifier_weak():
    from structured_pdf_text.document import EvidenceRef
    t=TextToken('0101',BBox(0,0,80,30),[EvidenceRef(SourceKind.OCR_PAGE,0,'id')],.99,'0101')
    cell=TableCell(2,0,1,1,t.bbox,t.text,[t],.55)
    assert CELL_POLICY.automatic_priority(cell,'Lote') is None
    cell.tokens=[replace(t,confidence=.4)]
    assert CELL_POLICY.automatic_priority(cell,'Lote')==3


@pytest.mark.parametrize('old,new',[('Rs 1.234','R$ 12,34'),('Rs 1234','R$ 12,34'),('Rs 350,00','R$ 35,000')])
def test_currency_repairs_do_not_invent_or_move_decimal_places(old,new):
    assert not CELL_POLICY.select([[token(new,.99)]],current_text=old,cell_bbox=BBox(0,0,200,50),header_text='Valor')


def test_observed_separator_repair_keeps_integer_and_cents():
    selected=CELL_POLICY.select([[token('R$ 61.000,00',.99)]],current_text='R$ 61,000,00',cell_bbox=BBox(0,0,200,50),header_text='Valor')
    assert selected[0].text=='R$ 61.000,00'


def test_visual_table_reading_cannot_move_an_ambiguous_decimal_place():
    from structured_pdf_text.ocr.pipelines.easyocr.policy import TOKEN_REFINEMENT_POLICY
    old=NS(text='Rs 1.234',tokens=[token('Rs 1.234',.85)])
    new=NS(text='R$ 12,34',tokens=[token('R$ 12,34',.99)])
    assert TOKEN_REFINEMENT_POLICY._keeps_prior(old,new)


def test_unrecognized_currency_prefix_requires_observed_column_context():
    a=token('As 293.500,00',.9)
    # An explicit reread retains the legacy selection behavior; automatic
    # scheduling additionally needs an observed monetary column context.
    assert CELL_POLICY.select([[token('R$ 293.500,00',.99)]],current_text=a.text,cell_bbox=BBox(0,0,200,50),header_text='Valor')
    cell=TableCell(2,0,1,1,a.bbox,a.text,[replace(a,ocr_provenance=FUSION_POLICY.provenance(a,NS(engine='easyocr',candidate_id='a',family='craft')))],.55)
    assert CELL_POLICY.automatic_priority(cell,'Concentração') is None
    assert CELL_POLICY.automatic_priority(cell,'Realizado | R$')==1
    selected=CELL_POLICY.select([[token('R$ 293.500,00',.99)]],current_text=a.text,cell_bbox=BBox(0,0,200,50),header_text='Realizado | R$')
    assert selected[0].text=='R$ 293.500,00'


def test_ambiguous_currency_sign_needs_two_complete_readings():
    value=token('R$ -0,01',.95)
    args=dict(current_text='Rs .0,01',cell_bbox=BBox(0,0,200,50),header_text='Variação | R$')
    assert not CELL_POLICY.select([[value]],**args)
    selected=CELL_POLICY.select([[value],[replace(value,confidence=.90)]],**args)
    assert selected[0].text==value.text


@pytest.mark.parametrize('angle',[0,90,180,270])
def test_direct_cell_verification_uses_only_an_observed_unambiguous_orientation(angle):
    from structured_pdf_text.ocr.pipeline.cell_recovery import refine_cell
    cell=NS(bbox=BBox(0,0,40,150),tokens=[token('Rs 350,00',rotation=angle)],text='Rs 350,00')
    facade=NS(capabilities={'direct_recognition':True},recognize_page=Mock(return_value=[]),
              recognize_direct=Mock(return_value=[]),discard_selected_tokens=Mock())
    target=NS(page_index=0,cell=cell)
    assert not refine_cell(facade,target,quality_policy='adaptive',base_scale=3.,header_text='Valor',policy=CELL_POLICY)
    assert facade.recognize_direct.call_args.kwargs.get('preprocessing_rotation',0)==angle
    from PIL import Image
    from structured_pdf_text.ocr.backends.easyocr import _rotate_image
    pixels=np.arange(5*7,dtype=np.uint8).reshape(5,7)
    actual=np.asarray(Image.fromarray(pixels).rotate(angle,expand=True,fillcolor='white'))
    assert np.array_equal(actual,_rotate_image(pixels,(-angle)%360))
    assert all('preprocessing_rotation' not in c.kwargs for c in facade.recognize_page.call_args_list)
    cell.tokens.append(token('outra leitura',rotation=(angle+90)%360))
    assert CELL_POLICY.direct_preprocessing_rotation(cell)==0


def test_baseline_direct_cell_keeps_legacy_orientation_behavior():
    from structured_pdf_text.ocr.pipeline.cell_recovery import refine_cell
    cell=NS(bbox=BBox(0,0,40,150),tokens=[token('Rs 350,00',rotation=270)],text='Rs 350,00')
    facade=NS(capabilities={'direct_recognition':True},recognize_page=Mock(return_value=[]),
              recognize_direct=Mock(return_value=[]),discard_selected_tokens=Mock())
    assert not refine_cell(facade,NS(page_index=0,cell=cell),quality_policy='baseline',base_scale=3.,header_text='Valor',policy=CELL_POLICY)
    assert 'preprocessing_rotation' not in facade.recognize_direct.call_args.kwargs


@pytest.mark.parametrize('correction',[90,180,270])
def test_direct_cell_orientation_metadata_returns_to_the_source_frame(correction):
    from structured_pdf_text.ocr.pipelines.easyocr.policy import INTEGRATION_POLICY
    from structured_pdf_text.ocr.pipeline.contracts import WorkGoal
    request=NS(goal=WorkGoal.CELL,selection_stage='direct',preprocessing_rotation=correction)
    raw=token('R$ 350,00')
    result=INTEGRATION_POLICY.prepare_token(raw,request)
    assert result.rotation==correction
    assert result.bbox==raw.bbox and result.text==raw.text and result.confidence==raw.confidence


def test_table_row_rebuild_keeps_committed_cell_reading_and_consumes_superseded_line():
    from structured_pdf_text.tables.ocr_lines import _rebuild_table_ocr_lines
    old=TextToken('Rs 350,00',BBox(10,10,150,40),[],.9,'Rs 350,00')
    new=TextToken('R$ 350,00',old.bbox,[],.95,'R$ 350,00',transformation_type='table_cell_ocr')
    cell=TableCell(0,0,1,1,BBox(0,0,200,50),new.text,[new],.95)
    line=TextLine([old],old.bbox,None,WritingDirection.LEFT_TO_RIGHT,None,None,line_id='original-table-line')
    table=NS(table_id='visual',row_count=1,cells=[cell])
    result=_rebuild_table_ocr_lines(table,[line],0,preserve_cell_refinements=True)
    assert cell.text==new.text and cell.tokens==[new]
    assert result[0].text==new.text
    assert 'original-table-line' in result[0].merged_source_line_ids
    assert line.tokens==[old] and old.text=='Rs 350,00'


def test_recovered_empty_quantity_survives_table_row_rebuild():
    from structured_pdf_text.tables.ocr_lines import _rebuild_table_ocr_lines
    label=TextToken('Quantidade',BBox(0,0,100,20),[],.99,'Quantidade')
    new=TextToken('6',BBox(10,25,40,45),[],.99,'6',transformation_type='table_cell_ocr')
    cells=[TableCell(0,0,1,1,BBox(0,0,100,20),label.text,[label],.99),
           TableCell(1,0,1,1,BBox(0,20,100,50),new.text,[new],.99)]
    line=TextLine([label],label.bbox,None,WritingDirection.LEFT_TO_RIGHT,None,None,line_id='header')
    result=_rebuild_table_ocr_lines(NS(table_id='visual',row_count=2,cells=cells),[line],0,preserve_cell_refinements=True)
    assert [line.text for line in result]==['Quantidade','6']
    assert cells[1].text=='6'


def test_default_table_row_rebuild_keeps_the_legacy_path():
    from structured_pdf_text.tables.ocr_lines import _rebuild_table_ocr_lines
    old=TextToken('Leitura antiga',BBox(10,10,150,40),[],.9,'Leitura antiga')
    new=TextToken('Outra leitura',old.bbox,[],.95,'Outra leitura',transformation_type='table_cell_ocr')
    cell=TableCell(0,0,1,1,BBox(0,0,200,50),new.text,[new],.95)
    line=TextLine([old],old.bbox,None,WritingDirection.LEFT_TO_RIGHT,None,None)
    result=_rebuild_table_ocr_lines(NS(table_id='visual',row_count=1,cells=[cell]),[line],0)
    assert result[0].text==old.text and cell.text==old.text


def test_malformed_currency_reread_is_not_committed_as_a_repair():
    assert not CELL_POLICY.select([[token('Rs 350,00',.99)]],current_text='RS 350,00',
        cell_bbox=BBox(0,0,200,50),header_text='Valor')


@pytest.mark.parametrize('text',['Semestre /','Semestre 1','Semestre','Semestre l'])
def test_semester_header_recovery_requires_an_observed_roman_numeral(text):
    assert not CELL_POLICY.select([[token(text,.99)]],current_text='Semestre',
        cell_bbox=BBox(0,0,200,50),header_text='Semestre')


@pytest.mark.parametrize('labels',[['Lote','Credor','Valor','Status'],['Ref.','Conferência']])
def test_financial_field_headers_remain_headers_after_shadow_grid_recovery(labels):
    cells=[NS(row=0,text=t) for t in labels]+[NS(row=1,text=t) for t in ['01','R$ 10,00']]
    assert EasyOCRTableStructureEngine().recover_header_rows(cells)==(0,)
