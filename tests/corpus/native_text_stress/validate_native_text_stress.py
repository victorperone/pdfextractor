#!/usr/bin/env python3
"""Preflight of the *test corpus itself*; never tests PDFExtractor output.

Usage: python validate_native_text_stress.py --root tests/corpus/native_text_stress
Requires pypdfium2 to validate PDF pages/text markers (pip requirements already
include this in the PDFExtractor project). No OCR is performed.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path


def digest(path: Path) -> str:
    h=hashlib.sha256()
    with path.open('rb') as file:
        for chunk in iter(lambda:file.read(1024*1024),b''):
            h.update(chunk)
    return h.hexdigest()


def validate(root: Path, *, check_pdf: bool=True) -> dict:
    prefix='Document_Text_Stress_V1'
    manifest=json.loads((root/f'{prefix}.manifest.json').read_text(encoding='utf8'))
    reference=json.loads((root/f'{prefix}.reference.json').read_text(encoding='utf8'))
    pages=reference['pages']
    assert len(pages)==manifest['pages']==200
    assert len({p['page'] for p in pages})==200
    assert [p['page'] for p in pages]==list(range(1,201))
    assert digest(root/f'{prefix}.pdf')==manifest['pdf_sha256']
    assert digest(root/f'{prefix}.reference.jsonl')==manifest['reference_sha256']
    assert digest(root/f'{prefix}.reference.json')==manifest['reference_json_sha256']
    jsonl=[json.loads(line) for line in (root/f'{prefix}.reference.jsonl').read_text(encoding='utf8').splitlines()]
    assert jsonl==pages, 'JSON and JSONL must contain identical page references'
    all_units=[];all_cells=[];all_regions=[]
    for p in pages:
        rids={r['region_id'] for r in p['regions']}
        assert len(rids)==len(p['regions'])
        all_regions.extend(rids)
        uids={u['unit_id'] for u in p['units']}
        assert len(uids)==p['expected_native_units']==p['expected_visible_units']==len(p['units'])
        assert list(u['logical_reading_order'] for u in p['units'])==list(range(1,len(p['units'])+1))
        assert sum(f'CASE-{p["page"]:03d}' in u['exact_text'] for u in p['units'])==1
        bbox_page=p['page_size_pt']
        for u in p['units']:
            assert u['region_id'] in rids
            assert u['visible'] and u['source_kind']=='native_pdf_text'
            x0,y0,x1,y1=u['bbox_top_origin_pt']
            assert -0.9 <= x0 < x1 <= bbox_page[0]+0.9, (p['page'],u)
            assert -0.9 <= y0 < y1 <= bbox_page[1]+0.9, (p['page'],u)
        for t in p['tables']:
            occupied=set()
            for c in t['cells']:
                assert c['cell_id'] not in {x['cell_id'] for x in all_cells}
                assert c['rowspan']>=1 and c['colspan']>=1
                units=[u for u in p['units'] if u['cell_id']==c['cell_id']]
                assert c['text_unit_ids']==[u['unit_id'] for u in sorted(units,key=lambda u:u['source_draw_order'])]
                assert units, (p['page'],c['cell_id'])
                for rr in range(c['row_index'],c['row_index']+c['rowspan']):
                    for cc in range(c['column_index'],c['column_index']+c['colspan']):
                        key=(rr,cc)
                        assert key not in occupied, ('table cell overlap',p['page'],t['table_id'],key)
                        occupied.add(key)
                all_cells.append(c)
        all_units.extend(p['units'])
    assert len({u['unit_id'] for u in all_units})==manifest['unit_count']
    assert len({c['cell_id'] for c in all_cells})==manifest['cell_count']
    assert len(all_regions)==manifest['region_count']
    assert sum(len(p['tables']) for p in pages)==manifest['table_count']
    assert len(set(all_regions))==len(all_regions)
    assert sum(c['rowspan']>1 for c in all_cells)>0
    assert sum(c['colspan']>1 for c in all_cells)>0
    missing=[]
    if check_pdf:
        import pypdfium2 as pdfium
        pdf=pdfium.PdfDocument(str(root/f'{prefix}.pdf'))
        assert len(pdf)==200
        for i in range(1,201):
            text=pdf[i-1].get_textpage().get_text_range()
            if f'CASE-{i:03d}' not in text:
                missing.append(i)
        assert not missing, ('Case markers missing in PDFium native extraction',missing)
    return dict(pages=len(pages),units=len(all_units),regions=len(all_regions),
                tables=manifest['table_count'],cells=len(all_cells),
                rowspan_cells=sum(c['rowspan']>1 for c in all_cells),
                colspan_cells=sum(c['colspan']>1 for c in all_cells),
                pdf_case_markers_missing=missing,
                corpus_integrity='PASS')


if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--root',type=Path,default=Path(__file__).resolve().parent)
    p.add_argument('--skip-pdf',action='store_true')
    args=p.parse_args()
    print(json.dumps(validate(args.root,check_pdf=not args.skip_pdf),ensure_ascii=False,indent=2))
