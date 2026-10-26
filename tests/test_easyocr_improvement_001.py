"""Regressions for independent orientation evidence and regional inference."""
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock
import numpy as np
import pytest
from structured_pdf_text.document import OcrToken, SourceKind
from structured_pdf_text.errors import ResourceExhaustedExtractionError
from structured_pdf_text.geometry import BBox
from structured_pdf_text.ocr.backends import easyocr as backend
from structured_pdf_text.ocr.pipelines.easyocr import craft_postprocess as craft


def raw(text, confidence=.95):
    return [([[10,5],[90,5],[90,25],[10,25]],text,confidence)]


def prior(*holes):
    return [OcrToken('corpo',BBox(c*100+40,r*100+40,c*100+60,r*100+60),.95,'pt-BR',SourceKind.OCR_PAGE)
            for r in range(4) for c in range(4) if (r,c) not in holes]


def image():
    return np.full((400,400,3),128,np.uint8)


def test_later_region_rejected_rotation_still_tries_other_angle(monkeypatch):
    old=prior((0,0),(0,1));record=Mock()
    run=Mock(side_effect=[(raw('CARIMBO A'),None),(raw('ruido',.1),None),(raw('CARIMBO B'),None)])
    monkeypatch.setattr(backend,'_run_easyocr',run)
    result=backend._supplement_with_regional_orientation(None,image(),{},old,0,'pt-BR',record_call=record)
    assert [t.text for t in result[len(old):]]==['CARIMBO A','CARIMBO B']
    assert [t.rotation for t in result[len(old):]]==[270,90]
    assert run.call_count==record.call_count==3


@pytest.mark.parametrize('error,expected',[(MemoryError('allocation failed'),ResourceExhaustedExtractionError),(RuntimeError('CUDA library not found'),RuntimeError)])
def test_optional_inference_preserves_fatal_and_device_failures(monkeypatch,error,expected):
    run=Mock(side_effect=error);monkeypatch.setattr(backend,'_run_easyocr',run)
    with pytest.raises(expected):
        backend._supplement_with_regional_orientation(None,image(),{},prior((0,0)),0,'pt-BR')
    assert run.call_count==1


def test_soft_optional_failure_preserves_observations(monkeypatch):
    run=Mock(side_effect=ValueError('optional crop unsupported'));monkeypatch.setattr(backend,'_run_easyocr',run)
    old=prior((0,0))
    assert backend._supplement_with_regional_orientation(None,image(),{},old,0,'pt-BR')==old
    assert run.call_count==2


def test_regional_fallback_is_reported(monkeypatch):
    fallback={'primary_error':'detect_failed'};record=Mock();old=prior((0,0))
    monkeypatch.setattr(backend,'_run_easyocr',Mock(return_value=(raw('CARIMBO'),fallback)))
    result=backend._supplement_with_regional_orientation(None,image(),{},old,0,'pt-BR',record_call=record)
    assert len(result)==len(old)+1
    record.assert_called_once_with(fallback)


def test_white_auxiliary_cells_do_not_run_inference(monkeypatch):
    run=Mock(return_value=(raw('invention'),None))
    monkeypatch.setattr(backend,'_run_easyocr',run)
    old=prior((0,0))
    result=backend._supplement_with_regional_orientation(None,np.full((400,400,3),255,np.uint8),{},old,0,'pt-BR')
    assert result==old
    run.assert_not_called()


def test_nonwhite_auxiliary_cell_remains_eligible(monkeypatch):
    pixels=np.full((400,400,3),255,np.uint8)
    pixels[20:30,20:50]=0
    run=Mock(return_value=(raw('RÓTULO'),None))
    monkeypatch.setattr(backend,'_run_easyocr',run)
    result=backend._supplement_with_regional_orientation(None,pixels,{},[],0,'pt-BR')
    assert [t.text for t in result]==['RÓTULO']
    assert run.call_count==1


def profiles():
    one=[([[10,10],[210,10],[210,30],[10,30]],'CARIMBO PRINCIPAL',.99)]
    return [(label,one) for label in ('default','high_recall','autocontrast')]


def test_correlated_variants_do_not_satisfy_independent_token_minimum(monkeypatch):
    monkeypatch.setattr(backend,'_adaptive_candidates',Mock(return_value=profiles()))
    run=Mock(return_value=([],None));monkeypatch.setattr(backend,'_run_easyocr',run)
    candidates,diag=backend._adaptive_with_orientation_selection(None,image(),{},0,'pt-BR')
    assert run.call_count==3 and len(candidates)==3
    assert diag['attempts'][0]['sufficient'] is False and diag['selected_angle']==0
    assert set(diag['attempts'][0]['candidate_token_counts'].values())=={1}


def test_correlated_title_does_not_hide_rotated_body(monkeypatch):
    monkeypatch.setattr(backend,'_adaptive_candidates',Mock(return_value=profiles()))
    body=[([[10,y],[300,y],[300,y+20],[10,y+20]],'Texto completo do corpo preservado na orientação correta.',.99) for y in (20,60,100)]
    monkeypatch.setattr(backend,'_run_easyocr',Mock(side_effect=[(body,None),([],None),([],None)]))
    candidates,diag=backend._adaptive_with_orientation_selection(None,image(),{},0,'pt-BR')
    assert diag['selected_angle']==90 and candidates[0][0]=='rot90'
    assert len(candidates)==1 and len(candidates[0][1])==3
    assert all(t.rotation==270 for t in candidates[0][1])


def test_ctc_orientation_proxy_accounts_for_length_without_rewriting_confidence():
    for length in (4,100):
        native=.9 ** (2*np.sqrt(length))
        token=OcrToken('a'*length,BBox(0,0,200,15),native,'pt-BR',SourceKind.OCR_PAGE)
        assert backend._orientation_character_confidence(token)==pytest.approx(.9)
        assert token.confidence==native


def test_orientation_cannot_discard_a_body_for_confident_digits():
    tokens=[OcrToken('88',BBox(i*20,0,i*20+10,15),.99,'pt-BR',SourceKind.OCR_PAGE) for i in range(4)]
    assert backend._orientation_candidate_can_replace_upright(tokens=tokens,score=.99,best_score=.7)
    assert not backend._orientation_candidate_can_replace_upright(tokens=tokens,score=.99,best_score=.7,
               reference_character_mass=1000,character_confidence=True)


def test_length_proxy_recovers_long_low_native_confidence_body(monkeypatch):
    body=[([[10,y],[350,y],[350,y+20],[10,y+20]],'Texto completo do documento com valores e palavras preservados.',.12) for y in (20,60,100)]
    monkeypatch.setattr(backend,'_adaptive_candidates',Mock(side_effect=[profiles(), [('default',body),('high_recall',body)]]))
    digits=[([[i*20,10],[i*20+10,10],[i*20+10,25],[i*20,25]],'8',.99) for i in range(10)]
    monkeypatch.setattr(backend,'_run_easyocr',Mock(side_effect=[(body,None),(digits,None),([],None)]))
    candidates,diag=backend._adaptive_with_orientation_selection(None,image(),{},0,'pt-BR',character_confidence=True)
    assert diag['selected_angle']==90 and len(candidates[0][1])==3
    assert all(t.confidence==.12 for t in candidates[0][1])


def test_length_proxy_does_not_skip_existing_half_turn_search(monkeypatch):
    wrong=[([[10,y],[350,y],[350,y+20],[10,y+20]],'abcdef'*20,.2) for y in (20,60,100)]
    correct=[(points,'Texto legível na posição correta. '*4,.9) for points,_text,_confidence in wrong]
    monkeypatch.setattr(backend,'_adaptive_candidates',Mock(side_effect=[[('default',wrong),('high_recall',wrong)], [('default',correct),('high_recall',correct)]]))
    run=Mock(side_effect=[([],None),(correct,None),([],None)])
    monkeypatch.setattr(backend,'_run_easyocr',run)
    candidates,diag=backend._adaptive_with_orientation_selection(None,image(),{},0,'pt-BR',character_confidence=True)
    assert run.call_count==3 and diag['selected_angle']==180
    assert all(t.confidence==.9 for t in candidates[0][1])


def test_orientation_probe_device_failure_reaches_retry_boundary(monkeypatch):
    monkeypatch.setattr(backend,'_adaptive_candidates',Mock(return_value=[('default',[])]))
    run=Mock(side_effect=RuntimeError('CUDA library not found'));monkeypatch.setattr(backend,'_run_easyocr',run)
    with pytest.raises(RuntimeError,match='CUDA library'):
        backend._adaptive_with_orientation_selection(None,image(),{},0,'pt-BR')
    assert run.call_count==1


@pytest.mark.parametrize('error,expected',[(MemoryError('allocation failed'),ResourceExhaustedExtractionError),(RuntimeError('CUDA library not found'),RuntimeError)])
def test_exhaustive_orientation_does_not_hide_fatal_or_device_error(monkeypatch,error,expected):
    run=Mock(side_effect=[([],None),error])
    monkeypatch.setattr(backend,'_run_easyocr',run)
    with pytest.raises(expected):
        backend._exhaustive_with_orientation_selection(None,image(),{})
    assert run.call_count==2


@pytest.mark.parametrize('cpu_failure',[False,True])
def test_regional_cuda_failure_uses_existing_cpu_retry_once(monkeypatch,caplog,cpu_failure):
    engine=object.__new__(backend.EasyOCRBackend)
    gpu_reader=SimpleNamespace(_pdfextractor_init_options={'lang_list':['pt','en'],'gpu':True})
    cpu_reader=SimpleNamespace()
    engine._reader=gpu_reader
    engine._closed=False
    engine._language='pt-BR'
    engine._device=engine._runtime_device='cuda'
    engine._cpu_quantize=True
    engine._auxiliary_readers={}
    engine._run_kwargs=lambda:{}
    engine.reset_page_diagnostics()
    old=prior((0,0))
    monkeypatch.setattr(backend,'_adaptive_with_orientation_selection',
                        Mock(return_value=([('default',old)],{'selected_angle':0,'attempts':[]})))
    monkeypatch.setattr(backend,'_best_candidate',Mock(return_value=(old,[])))
    construct=Mock(return_value=cpu_reader)
    monkeypatch.setattr(backend,'_import_easyocr',lambda:SimpleNamespace(Reader=construct))

    def run(reader,*args,**kwargs):
        if reader is gpu_reader or cpu_failure:
            raise RuntimeError('CUDA library not found')
        return raw('CPU OK'),None

    infer=Mock(side_effect=run)
    monkeypatch.setattr(backend,'_run_easyocr',infer)
    if cpu_failure:
        with pytest.raises(RuntimeError,match='CUDA library'):
            engine.recognize_page(image(),0,quality_policy='adaptive')
    else:
        result=engine.recognize_page(image(),0,quality_policy='adaptive')
        assert result[-1].text=='CPU OK'
        assert engine._easyocr_calls==1
    assert construct.call_count==1 and infer.call_count==2
    assert construct.call_args.kwargs['gpu'] is False
    assert construct.call_args.kwargs['quantize'] is True
    assert engine._device==engine._runtime_device=='cpu'
    assert 'retrying on CPU' in caplog.text


@pytest.mark.parametrize('x,y,expected',[(10,5,[[8,3],[31,3],[31,11],[8,11]]),(0,0,[[0,0],[21,0],[21,6],[0,6]])])
def test_component_geometry_and_readonly_inputs(x,y,expected):
    pytest.importorskip('cv2');a=np.zeros((32,64),np.float32);b=np.zeros_like(a)
    a[y:y+5,x:x+20]=.95;before=a.copy();a.flags.writeable=False;b.flags.writeable=False
    boxes,labels,mapper=craft.component_boxes(a,b,.7,.4,.3)
    assert mapper==[1] and len(boxes)==1 and np.count_nonzero(labels)==100
    np.testing.assert_allclose(boxes[0],np.asarray(expected,np.float32),rtol=0,atol=1e-5)
    np.testing.assert_array_equal(a,before)


def test_large_weak_component_is_rejected_before_dilation_arithmetic():
    pytest.importorskip('cv2')
    # Upstream skips this component before niter; computing niter first can
    # overflow int32 and fail even though the component is below threshold.
    text=np.full((1800,2400),.2,np.float32)
    link=np.full_like(text,.9)
    with np.errstate(over='raise',invalid='raise'):
        boxes,labels,mapper=craft.component_boxes(text,link,.7,.4,.3)
    assert not boxes and not mapper
    assert labels.shape==text.shape and np.all(labels==1)


def detection_module():
    utilities=ModuleType('easyocr.craft_utils')
    exec('def getDetBoxes(textmap, linkmap, text_threshold, link_threshold, low_text, poly=False, estimate_num_chars=False):\n return ("upstream",poly,estimate_num_chars)\n',utilities.__dict__)
    detection=ModuleType('easyocr.detection');detection.getDetBoxes=utilities.getDetBoxes
    exec('def test_net(*args,**kwargs):\n return getDetBoxes(*args,**kwargs)\ndef get_textbox(*args,**kwargs):\n return test_net(*args,**kwargs)\n',detection.__dict__)
    return detection


def test_binding_changes_only_selected_reader(monkeypatch):
    monkeypatch.setattr(craft,'_easyocr_version',lambda:'1.7.2')
    monkeypatch.setattr(craft,'component_boxes',lambda *a:([['bounded']],None,[7]))
    module=detection_module();original=(module.get_textbox,module.test_net,module.getDetBoxes)
    selected=SimpleNamespace(get_textbox=module.get_textbox);other=SimpleNamespace(get_textbox=module.get_textbox)
    assert craft.bind_craft_postprocessing(selected)
    assert (module.get_textbox,module.test_net,module.getDetBoxes)==original
    args=(None,None,.7,.4,.3)
    assert selected.get_textbox(*args)==([['bounded']],[None],[7])
    assert other.get_textbox(*args)==('upstream',False,False)
    assert selected.get_textbox(*args,poly=True)==('upstream',True,False)
    assert selected.get_textbox(*args,estimate_num_chars=True)==('upstream',False,True)
    bound=selected.get_textbox
    assert craft.bind_craft_postprocessing(selected) and selected.get_textbox is bound


@pytest.mark.parametrize('release',['','1.8.0','2.0.0'])
def test_unknown_release_keeps_upstream(monkeypatch,release):
    monkeypatch.setattr(craft,'_easyocr_version',lambda:release)
    module=detection_module();reader=SimpleNamespace(get_textbox=module.get_textbox)
    assert not craft.bind_craft_postprocessing(reader)
    assert reader.get_textbox is module.get_textbox


def test_chosen_orientation_reuses_recorded_probe_without_second_default_call(monkeypatch):
    cached = raw("BASE JÁ OBSERVADA")
    additional = raw("VARIANTE HIGH RECALL")
    run = Mock(return_value=(additional, None))
    record = Mock()
    monkeypatch.setattr(backend, "_run_easyocr", run)
    monkeypatch.setattr(backend, "_image_preprocessing_candidates", Mock(return_value=[]))
    candidates = backend._adaptive_candidates(None, image(), {
        "text_threshold": .7, "low_text": .4, "link_threshold": .4, "min_size": 10,
    }, baseline_raw=cached, record_call=record)
    assert candidates == [("default", cached), ("high_recall", additional)]
    run.assert_called_once()
    record.assert_called_once_with(None)
