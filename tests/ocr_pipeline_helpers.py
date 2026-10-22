from pathlib import Path

from structured_pdf_text.geometry import BBox
from structured_pdf_text.ocr.pipeline.authorization import AuthorizationRegistry
from structured_pdf_text.ocr.pipeline.contracts import EngineSelection, OCRWorkRequest, PageGeometry, TargetRef, WorkScope


def make_work(*, regions=None, page_bbox=None, rotation=0, selection=None, **kwargs):
    box = page_bbox or BBox(0, 0, 200, 100)
    scope = WorkScope("extraction-1", "document-1", 0, PageGeometry(box, rotation), TargetRef("region", "region-1", 1), "evidence-v1")
    registry = AuthorizationRegistry(scope.extraction_id, scope.document_id)
    auth = registry.issue(scope, tuple(regions) if regions is not None else (box,))
    request = OCRWorkRequest("request-1", scope, selection or EngineSelection("custom"), (auth,), **kwargs)
    return registry, request, auth


def pdf_with_markers(path: Path, *, rotation=0, crop=False):
    from reportlab.pdfgen import canvas
    pdf = canvas.Canvas(str(path), pagesize=(200, 100))
    pdf.setFillColorRGB(1, 0, 0)
    pdf.rect(10, 10, 30, 80, fill=1, stroke=0)
    pdf.setFillColorRGB(0, 1, 0)
    pdf.rect(80, 10, 30, 80, fill=1, stroke=0)
    pdf.setFillColorRGB(0, 0, 1)
    pdf.rect(150, 10, 30, 80, fill=1, stroke=0)
    pdf.save()
    if crop or rotation:
        import pypdfium2 as pdfium
        doc = pdfium.PdfDocument(path)
        page = doc[0]
        if crop:
            page.set_cropbox(10, 5, 190, 95)
        page.set_rotation(rotation)
        page.gen_content()
        target = path.with_stem(path.stem+"-transformed")
        doc.save(target)
        page.close()
        doc.close()
        return target
    return path
