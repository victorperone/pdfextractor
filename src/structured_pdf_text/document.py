from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from enum import Enum
from typing import Any

from .geometry import BBox, Point


class SourceKind(str, Enum):
    """Origin of a text token or table cell, used as provenance throughout the pipeline."""

    NATIVE_PDF = "native_pdf"
    NATIVE_GENERATED = "native_generated"
    OCR_REGION = "ocr_region"
    OCR_PAGE = "ocr_page"
    TABLE_NATIVE = "table_native"
    TABLE_MODEL = "table_model"
    RECOVERED_UNICODE = "recovered_unicode"


class TokenFlag(str, Enum):
    """Diagnostic flags attached to individual ``TextToken`` instances."""

    DUPLICATE = "duplicate"
    WHITESPACE_INFERRED = "whitespace_inferred"
    UNICODE_MAPPING_FAILED = "unicode_mapping_failed"
    GENERATED = "generated"
    OCR_CONFLICT = "ocr_conflict"


class RegionKind(str, Enum):
    """Semantic type of a layout region detected on a page."""

    TEXT = "text"
    TITLE = "title"
    LIST = "list"
    TABLE = "table"
    FIGURE = "figure"
    CAPTION = "caption"
    HEADER = "header"
    FOOTER = "footer"
    FOOTNOTE = "footnote"
    MARGINALIA = "marginalia"
    DECORATIVE = "decorative"
    UNKNOWN = "unknown"


class RegionDecision(str, Enum):
    """Quality-gate decision for a single layout region.

    ``KEEP_NATIVE`` — native text is healthy; no OCR needed.
    ``MERGE_OCR`` — blend OCR tokens with native text.
    ``OCR_REGION`` — replace native text with targeted region OCR.
    ``ESCALATE_PAGE_OCR`` — full-page OCR required.
    """

    KEEP_NATIVE = "keep_native"
    MERGE_OCR = "merge_ocr"
    OCR_REGION = "ocr_region"
    ESCALATE_PAGE_OCR = "escalate_page_ocr"


class TableMethod(str, Enum):
    """Algorithm that produced a ``StructuredTable``."""

    STRICT_GRID = "strict_grid"
    RELAXED_GRID = "relaxed_grid"
    TEXT_TRACKS = "text_tracks"
    VISUAL_MODEL = "visual_model"
    UNKNOWN = "unknown"


class ExtractionStatus(str, Enum):
    """Overall outcome of document extraction."""

    SUCCESS = "success"
    PARTIAL_SUCCESS = "partial_success"
    FAILURE = "failure"


class PageStrategy(str, Enum):
    """Extraction strategy resolved for a single page."""

    NATIVE = "native"
    HYBRID_CANDIDATE = "hybrid_candidate"
    OCR_CANDIDATE = "ocr_candidate"
    MIXED = "mixed"


class ComplexityReason(str, Enum):
    """Signal that influenced page complexity classification."""

    NO_TEXT = "no_text"
    SCANNED = "scanned"
    SPARSE_TEXT = "sparse_text"
    EMBEDDED_IMAGES = "embedded_images"
    GARBLED_UNICODE = "garbled_unicode"
    DUPLICATE_TEXT_LAYER = "duplicate_text_layer"
    INVISIBLE_TEXT = "invisible_text"
    VECTOR_TEXT = "vector_text"
    ANNOTATION_TEXT = "annotation_text"
    MULTI_COLUMN_LIKELY = "multi_column_likely"
    TABLE_LIKELY = "table_likely"
    ROTATED_TEXT = "rotated_text"


@dataclass(frozen=True, slots=True)
class EvidenceRef:
    """Pointer to the origin of a text token within the evidence graph."""

    source: SourceKind
    page_index: int
    element_id: str


@dataclass(frozen=True, slots=True)
class NativeCharacter:
    """Single character extracted from a PDFium text page.

    Preserves the full raw evidence: geometry, origin, font, color, text render
    mode, marked-content ID, and PDFium flags.  Optional fields are ``None``
    when the underlying PDFium capability is unavailable.
    """

    page_index: int
    char_index: int
    text: str
    unicode_codepoint: int | None
    bbox: BBox
    origin: Point | None = None
    angle: float | None = None
    font_name: str | None = None
    font_size: float | None = None
    font_weight: int | None = None
    fill_color: tuple[int, int, int, int] | None = None
    stroke_color: tuple[int, int, int, int] | None = None
    text_render_mode: int | str | None = None
    marked_content_id: int | None = None
    generated: bool | None = None
    hyphen: bool | None = None
    unicode_mapping_failed: bool | None = None
    visible_candidate: bool | None = None

    @property
    def evidence_ref(self) -> EvidenceRef:
        source = SourceKind.NATIVE_GENERATED if self.generated else SourceKind.NATIVE_PDF
        return EvidenceRef(source, self.page_index, f"char:{self.char_index}")


@dataclass(frozen=True, slots=True)
class NativeObjectSummary:
    page_index: int
    object_index: int
    object_type: int | str
    bbox: BBox | None
    level: int | None = None
    marked_content_id: int | None = None
    matrix: tuple[float, float, float, float, float, float] | None = None


@dataclass(frozen=True, slots=True)
class ImageEvidence:
    page_index: int
    object_index: int
    bbox: BBox | None
    pixel_width: int | None = None
    pixel_height: int | None = None
    dpi_x: float | None = None
    dpi_y: float | None = None
    bits_per_pixel: int | None = None
    color_space: int | str | None = None


@dataclass(frozen=True, slots=True)
class PathEvidence:
    page_index: int
    object_index: int
    bbox: BBox | None
    level: int | None = None


@dataclass(frozen=True, slots=True)
class AnnotationEvidence:
    page_index: int
    annotation_index: int
    subtype: str | None
    bbox: BBox | None
    contents: str | None = None
    appearance_streams: dict[str, str] = field(default_factory=dict)
    object_count: int | None = None


@dataclass(frozen=True, slots=True)
class StructureTreeEvidence:
    available: bool
    node_count: int | None = None
    raw_summary: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class NativeObjectEvidence:
    images: tuple[ImageEvidence, ...]
    paths: tuple[PathEvidence, ...]
    annotations: tuple[AnnotationEvidence, ...]
    structure_tree: StructureTreeEvidence | None
    page_bbox: BBox
    crop_bbox: BBox
    rotation: int
    objects: tuple[NativeObjectSummary, ...] = ()
    media_bbox: BBox | None = None
    coordinate_origin: Point = Point(0.0, 0.0)
    capabilities: dict[str, bool] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class NativePageEvidence:
    page_index: int
    bbox: BBox
    characters: tuple[NativeCharacter, ...]
    objects: NativeObjectEvidence
    extracted_text: str
    capabilities: dict[str, bool] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class OcrToken:
    """One OCR recognition result in canonical PDF page coordinates.

    Immutable; produced by PaddleOcrEngine and region refinement and consumed
    by the fusion layer.
    """

    text: str
    bbox: BBox
    confidence: float | None
    language: str | None
    source: SourceKind
    rotation: int = 0
    provenance: str | None = None


@dataclass(slots=True)
class TextToken:
    """Mutable composite token produced after native evidence processing.

    Aggregates one or more ``NativeCharacter`` references and carries
    normalised text, font metadata, and diagnostic flags.  Mutability allows
    the pipeline to attach flags and overrides without creating new objects.
    """

    text: str
    bbox: BBox
    sources: list[EvidenceRef]
    confidence: float
    normalized_text: str | None
    flags: set[TokenFlag] = field(default_factory=set)
    font_name: str | None = None
    font_size: float | None = None
    font_weight: int | None = None
    fill_color: tuple[int, int, int, int] | None = None
    stroke_color: tuple[int, int, int, int] | None = None
    text_render_mode: int | str | None = None
    provenance: str | None = None
    rotation: int = 0


@dataclass(frozen=True, slots=True)
class Baseline:
    y: float
    angle: float = 0.0


class WritingDirection(str, Enum):
    LEFT_TO_RIGHT = "left_to_right"
    RIGHT_TO_LEFT = "right_to_left"
    TOP_TO_BOTTOM = "top_to_bottom"
    UNKNOWN = "unknown"


@dataclass(slots=True)
class TextLine:
    tokens: list[TextToken]
    bbox: BBox
    baseline: Baseline | None
    direction: WritingDirection
    native_order_min: int | None
    native_order_max: int | None
    gap_mode: str = "fallback"
    order_mode: str = "geometry"
    line_id: str | None = None
    text_override: str | None = None
    join_next_without_space: bool = False
    ghost_punctuation_candidate: bool = False
    merged_source_line_ids: tuple[str, ...] = ()

    @property
    def text(self) -> str:
        return self.text_override if self.text_override is not None else "".join(token.text for token in self.tokens)


@dataclass(slots=True)
class RegionQuality:
    """Quality assessment result for one layout region.

    Produced by the quality gate and consumed by the recovery decision logic
    to determine whether a region should be kept native, OCR-recovered or
    escalated to full-page OCR.
    """

    decision: RegionDecision
    reasons: list[str] = field(default_factory=list)
    confidence: float | None = None


@dataclass(slots=True)
class LayoutRegion:
    """One detected layout region on a page.

    Holds both native text lines and the OCR tokens that replace or supplement
    them after targeted recovery.  ``heading_level`` is set only for TITLE
    regions after heading-level assignment.
    """

    region_id: str
    kind: RegionKind
    bbox: BBox
    layout_confidence: float | None
    native_lines: list[TextLine]
    ocr_tokens: list[OcrToken]
    quality: RegionQuality
    heading_level: int | None = None  # 1, 2 or 3; None for non-title regions
    ocr_lines: list[TextLine] = field(default_factory=list)
    semantic_role: str | None = None
    edge_role: str | None = None


@dataclass(slots=True)
class TableFragment:
    page_index: int
    bbox: BBox | None
    row_start: int
    row_end: int


@dataclass(slots=True)
class TableCell:
    row: int
    col: int
    rowspan: int
    colspan: int
    bbox: BBox | None
    text: str
    tokens: list[TextToken]
    confidence: float


@dataclass(slots=True)
class StructuredTable:
    """A complete detected table, potentially spanning multiple page fragments.

    ``page_fragments`` tracks which physical page(s) contain table rows.  When
    ``continued_from_previous_page`` or ``continues_to_next_page`` is ``True``,
    this instance is one logical fragment of a cross-page table — the document-
    level ``tables`` list holds the merged logical view.
    """

    table_id: str
    page_fragments: list[TableFragment]
    cells: list[TableCell]
    column_count: int
    row_count: int
    confidence: float
    method: TableMethod
    continued_from_previous_page: bool = False
    continues_to_next_page: bool = False


@dataclass(slots=True)
class PageDiagnostics:
    """Per-page extraction diagnostics included in the API result.

    ``facts`` is an open-ended dict for structured data (timings, OCR passes,
    variant scores, table decisions, etc.) that does not fit the fixed fields.
    """

    page_index: int
    strategy: PageStrategy
    reasons: list[ComplexityReason]
    native_chars: int
    native_text_length: int
    ocr_tokens_added: int = 0
    conflicts: int = 0
    tables: int = 0
    processing_time_ms: float | None = None
    warnings: list[str] = field(default_factory=list)
    facts: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class DocumentDiagnostics:
    """Document-level extraction summary included in the API result."""

    status: ExtractionStatus
    page_count: int
    native_pages: int
    mixed_pages: int
    ocr_pages: int
    warnings: list[str] = field(default_factory=list)
    facts: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class DocumentMetadata:
    source_path: str
    page_count: int
    pdfium_version: str | None


class ContentKind(str, Enum):
    """Semantic type of a ``PageContentBlock`` used by the Markdown renderer."""

    TEXT = "text"
    TITLE = "title"
    LIST = "list"
    TABLE = "table"
    CAPTION = "caption"
    HEADER = "header"
    FOOTER = "footer"
    FOOTNOTE = "footnote"
    MARGINALIA = "marginalia"
    FIGURE = "figure"
    UNKNOWN = "unknown"


class ContentDisposition(str, Enum):
    """Reason a ``PageContentBlock`` was rendered or suppressed."""

    RENDERED = "rendered"
    TABLE_OWNED = "table_owned"
    FIGURE_OWNED = "figure_owned"
    SUPPRESSED_REPEATED_HEADER = "suppressed_repeated_header"
    SUPPRESSED_REPEATED_FOOTER = "suppressed_repeated_footer"
    SUPPRESSED_DECORATIVE = "suppressed_decorative"
    DEDUPLICATED = "deduplicated"
    SUPPRESSED_POLICY = "suppressed_policy"
    SUPPRESSED_REDACTED = "suppressed_redacted"


@dataclass(slots=True)
class StructuredListItem:
    marker: str
    text: str
    level: int
    bbox: BBox
    order_index: int
    confidence: float | None = None
    marker_source: str = "observed"


@dataclass(slots=True)
class PageContentBlock:
    """One atomic renderable unit on a page.

    Produced by ``assemble_page_content()`` and consumed by the renderers.
    Renderers iterate blocks in ``order_index`` order and dispatch by ``kind``
    with no geometry lookups.
    """

    block_id: str
    page_index: int
    kind: ContentKind
    bbox: BBox
    order_index: int

    text: str = ""
    table_id: str | None = None
    heading_level: int | None = None

    source_region_ids: list[str] = field(default_factory=list)

    confidence: float | None = None
    fallback_from_table: bool = False
    list_items: list[StructuredListItem] = field(default_factory=list)
    # Compatibility-preserving view flag; raw/evidence remains available.
    decorative: bool = False
    line_ids: list[str] = field(default_factory=list)
    suppressed: bool = False
    suppression_reason: str | None = None


@dataclass(slots=True)
class StructuredPage:
    page_index: int
    bbox: BBox
    regions: list[LayoutRegion]
    tables: list[StructuredTable]
    raw_text: str
    reading_text: str
    diagnostics: PageDiagnostics
    native_evidence: NativePageEvidence | None = None
    content_blocks: list[PageContentBlock] = field(default_factory=list)


@dataclass(slots=True)
class StructuredDocument:
    pages: list[StructuredPage]
    tables: list[StructuredTable]
    raw_text: str
    reading_text: str
    metadata: DocumentMetadata
    diagnostics: DocumentDiagnostics

    def to_dict(self) -> dict[str, Any]:
        return to_plain_data(self)


def to_plain_data(value: Any) -> Any:
    """Recursively convert dataclasses, Enums, sets and nested collections to plain data.

    Produces output that is JSON-serialisable without a custom encoder.
    Set members are sorted by string representation to ensure deterministic output.
    """
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, set):
        return [to_plain_data(item) for item in sorted(value, key=lambda item: str(item))]
    if isinstance(value, (list, tuple)):
        return [to_plain_data(item) for item in value]
    if isinstance(value, dict):
        return {str(key): to_plain_data(item) for key, item in value.items()}
    if is_dataclass(value):
        return {key: to_plain_data(item) for key, item in asdict(value).items()}
    return value
