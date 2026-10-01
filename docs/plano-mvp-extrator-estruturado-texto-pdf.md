# MVP Implementation Plan — Robust PDF Text and Structure Extractor

**Status:** architectural proposal revised after a comparative study of market parsers  
**Revision date:** September 12, 2026  
**Working name:** `structured-pdf-text`  
**Objective:** build a local, auditable, and commercially usable extractor, aimed at maximum possible recovery of text actually present or visible in PDF documents, with explicit handling of multiple columns, tables, cross-page tables, defective text layers, and scanned documents.

---

## 1. Executive summary

The first version of this plan proposed an essentially deterministic architecture:

```text
PDF
  ↓
PDFium
  ↓
characters + geometry + fonts
  ↓
normalization
  ↓
lines
  ↓
words
  ↓
spans
  ↓
blocks
  ↓
reading order
  ↓
structured text
```

This foundation remains correct and should be preserved. The study of Docling, MinerU, LiteParse, Xberg, Unstructured, PaddleOCR, MarkItDown and Marker, however, shows that it is insufficient as a complete architecture for the now-defined objective: robustly handling heterogeneous enterprise documents, including PDFs with two columns, poor tables, tables split across pages, scans, text drawn as vectors, defective OCR layers, partially rasterized content, and mixtures of native text with images.

The main revision is to transform the product into a **hybrid evidence-fusion pipeline**.

The new architecture keeps PDFium as the primary and exact source for digital PDFs, but adds four concepts that become central:

1. **Diagnosis before choosing the strategy.** Each page and, when possible, each region receives quality signals. The system does not just decide “native PDF or OCR”; it decides where the text layer is reliable, incomplete, duplicated, invisible, corrupted, or absent.
2. **Layout before global reading order.** A layout detector identifies regions such as prose, title, table, figure, header, and footer. Reading order is no longer a single heuristic applied to the entire page.
3. **OCR as selective recovery, not automatic replacement.** Reliable native text is preserved. OCR is used only on pages or regions where there is evidence of loss, corruption, or exclusively visual content.
4. **Tables as an independent subsystem.** Tables do not go through the same ordering logic used for paragraphs. Detection uses a cascade of strategies and cross-page continuity is resolved in a dedicated document-level step.

The revised architecture can be summarized as follows:

```text
                                  PDF
                                   │
                 ┌─────────────────┴─────────────────┐
                 │                                   │
                 ▼                                   ▼
        PDFium native evidence               Page rendering
      characters, objects, paths,            low/high resolution
      images, fonts, coordinates                     │
                 │                                    ▼
                 │                           Visual layout analysis
                 │                           and OCR when necessary
                 │                                    │
                 └──────────────┬─────────────────────┘
                                ▼
                     Page evidence map
                     + confidence + provenance
                                │
                ┌───────────────┼──────────────────┐
                ▼               ▼                  ▼
          Prose regions       Tables        Problematic regions
                │               │                  │
                ▼               ▼                  ▼
        reading order     dedicated table   selective OCR
        and reconstruction  pipeline        or full page
                │               │                  │
                └───────────────┼──────────────────┘
                                ▼
                         Evidence fusion
                                │
                                ▼
                      Per-page structure
                                │
                                ▼
                  Cross-page reconstruction
                    tables, headers, flow
                                │
                                ▼
          raw_text | reading_text | structured JSON | Markdown
```

### Main decision

**None of the studied projects should be copied in full as an architecture.** For our objective, the best solution combines distinct ideas:

| Layer | Main reference | What to take from it |
|---|---|---|
| Native acquisition | LiteParse + Docling | PDFium and separation between parser and structure |
| Quality diagnosis | LiteParse + Marker | deterministic signals + text layer evaluation |
| Layout | Docling + PaddleOCR + Xberg | visual regions before semantic reconstruction |
| Selective OCR | Marker + MinerU | region-level repair and promotion to full page when necessary |
| Native/OCR fusion | LiteParse + MinerU | spatial merge with provenance |
| Reading order | Marker + Xberg | native sequence extracted as strong evidence + XY-cut only in suitable regions |
| Tables | Xberg + PaddleOCR | deterministic cascade and structural model as fallback |
| Cross-page tables | MinerU | structural signature and document continuity |
| Document model | Docling | rich intermediate structure before serialization |
| Strategy orchestration | Unstructured | automatic strategy and explicit fallback |
| Extensibility | MarkItDown | replaceable components, without tying the product to one engine |

If it were necessary to choose **a single project as the closest architectural reference to the core**, it would be **LiteParse**, because it also starts from PDFium, performs complexity detection, selective OCR, merge, and spatial reconstruction. For the production architecture, however, **Marker provides the best decision pattern between native text and visual recovery**, while **Xberg provides the best deterministic lessons for reading and tables**, **MinerU provides the most useful reference for table continuity**, and **PaddleOCR provides the best set of visual tools for the fallback**.

---

## 2. Product objective

The product should not be presented merely as “a PDF reader”. The objective is more specific:

> **Recover with the highest possible fidelity the readable textual content of a PDF and maintain sufficient evidence to reconstruct its organization, without destroying correct text while trying to fix difficult cases.**

The MVP will be evaluated primarily on real company documents.

There is no commitment to be superior to MuPDF on every existing PDF. The technically defensible commitment is:

> **approach or surpass the extraction quality of MuPDF/PyMuPDF on the company's representative corpus and, above all, recover content classes that a path purely based on the text layer misses.**

This explicitly includes:

* digital text with Brazilian Portuguese accents;
* documents with embedded fonts and unusual CMaps;
* pages in one or multiple columns;
* headers, footers, and page numbering;
* tables with explicit lines;
* borderless tables;
* tables with merged cells;
* misaligned or poorly formatted tables;
* tables continued on the next page;
* PDFs generated by legacy systems;
* PDFs with an invisible OCR layer;
* PDFs with a duplicated or incorrect OCR layer;
* fully scanned pages;
* hybrid pages with native text and rasterized regions;
* rotated text;
* text drawn as vectors;
* text in annotations or appearance streams that does not appear in the normal text path;
* invalid Unicode characters or characters without reliable mapping.

---

## 3. What “better text extraction” means

A common mistake in parsers is mixing three different objectives into a single string. Our product must keep them separate from the start.

### 3.1. `raw_text`

Objective: **maximum recovery**.

Must preserve the maximum amount of detected textual content, including elements that may later be classified as headers, footers, marginal text, or repetitions.

Must not silently remove text just because it is near the page margin.

`raw_text` **is not a blind concatenation of all hypotheses**. Obvious duplicates can be resolved by fusion. The difference is that it does not apply aggressive semantic cleaning such as removing headers, footers, or marginals. All rejected hypotheses remain available in `structured_document`/diagnostics.

This is the output used when the question is:

> “What texts can we recover from this document?”

### 3.2. `reading_text`

Objective: **coherent human reading**.

May:

* order columns;
* join lines of a paragraph;
* handle hyphenation;
* avoid duplicates;
* represent tables in linear or structured form;
* flag repeated headers and footers;
* optionally suppress them.

This is the output used when the question is:

> “How should this document be read?”

### 3.3. `structured_document`

Objective: **not losing the evidence needed for future decisions**.

Must contain pages, characters, tokens, lines, regions, tables, relevant images, coordinates, provenance, confidence, and relationships between elements.

This representation is the product's source of truth. Text, Markdown, and simplified JSON are renderings of it.

### 3.4. Why this separation is mandatory

There is a real conflict between “maximum recall” and “clean text”. An OCR may recover a header that a benchmark considers noise; a header filter may increase Markdown quality while simultaneously reducing textual coverage. These objectives must not be irreversibly decided during acquisition.

The product rule will be:

> **capture first, classify later, remove only at the appropriate rendering stage.**

---

## 4. Architectural research conducted

The current implementations and documentation of the following projects were analyzed, focusing on pipeline code, OCR routing, native extraction, layout, reading, and tables:

| Project | Predominant role | Observed code license | Relevance to our MVP |
|---|---|---|---|
| Docling | Structured Document AI | MIT | very high |
| MinerU | Hybrid parser with OCR/VLM | Apache 2.0 with additional terms | very high |
| LiteParse | Lightweight PDF parser with PDFium | Apache 2.0 | very high |
| Xberg | Multimodal parser in Rust | MIT | very high |
| Unstructured | Partitioning orchestrator | Apache 2.0 | medium |
| PaddleOCR | OCR and visual Document AI | Apache 2.0 for the code | very high |
| MarkItDown | Lightweight converter to Markdown | MIT | low for the core, medium for extensibility |
| Marker | High-quality hybrid parser | Apache 2.0 for the code; weights have their own terms | very high |

**Important:** the repository license does not guarantee that all model weights, artifacts, or dependencies share the same conditions. Model selection for production will require a separate license review.

---

## 5. General architecture comparison

### 5.1. Technical matrix

| Project | Native text | Visual layout | OCR | Adaptive selection | Native/OCR merge | Tables | Cross-page table | Reading order |
|---|---|---|---|---|---|---|---|---|
| Docling | yes, abstract backend | yes | yes | configurable | pipeline assembly | dedicated model | document structure allows evolution | dedicated model |
| MinerU | yes | yes | yes | pipeline/hybrid/VLM | hybrid by region | strong | yes, explicit | human/modeled reconstruction |
| LiteParse | PDFium | spatial heuristic | selective | yes, strong | yes | heuristic | limited compared to MinerU | projection/spatial structure |
| Xberg | native parser, optional PDFium | optional, ONNX | fallback | yes | Native/Mixed/OCR | strong cascade | structural support evolving | XY-cut + structure signals |
| Unstructured | pdfminer | hi_res | OCR only/hi_res | AUTO/FAST/HI_RES/OCR_ONLY | by strategy | hi_res | not the main focus | sorting/XY-cut |
| PaddleOCR | not its main focus | strong | strong | configurable pipeline | predominantly visual | very strong | supported | layout/model based |
| MarkItDown | pdfminer/pdfplumber | limited locally | plugin/cloud | by converter | limited | pdfplumber + heuristics | not the focus | extractor-dependent |
| Marker | pdftext | yes | selective | very strong | per page/block | strong + visual fallback | with optional LLM | uses native order when reliable |

### 5.2. Joint conclusion

The most robust projects converge on a few ideas:

* do not always trust embedded text;
* do not always apply OCR;
* render the page to validate or complement what the text layer claims;
* detect layout before reconstructing complex structures;
* treat tables as structural objects, not just lines of text;
* use different strategies depending on page quality;
* preserve an intermediate structure before producing Markdown or plain text.

The difference between them lies in **how much work they do with deterministic heuristics** and **how much they delegate to vision models**.

Our architecture should sit in the middle of that spectrum:

```text
reliable digital text ──────────────► preserve determinism

hybrid/problematic text ────────────► merge signals

scan or visual content ─────────────► use visual perception/OCR
```

---

## 6. Docling

### 6.1. How the architecture works

Docling treats the PDF as input to a complete document pipeline. The current implementation clearly separates the stages of preprocessing, layout, OCR, layout postprocessing, table structure, page assembly, and subsequent reading order resolution and heading hierarchy.

The conceptual structure is close to:

```text
PDF backend
   ↓
preprocessing
   ↓
layout
   ↓
OCR
   ↓
layout postprocessing
   ↓
table structure
   ↓
page assembly
   ↓
reading order
   ↓
heading hierarchy
   ↓
DoclingDocument
```

The project has PDF backends decoupled from the rest of the pipeline. Among them there is support for pypdfium2, whose backend exposes text cells to the upper stages.

The most important architectural decision of Docling is:

> **the PDF parser is not responsible for delivering the final document; it delivers evidence that will be interpreted by later stages.**

This aligns with the direction we should follow.

### 6.2. Strengths for our case

**Strong intermediate document model.** The result is not born as a string. Layout, table, image, and text survive long enough to be reconciled.

**Stage separation.** OCR, layout, and table are distinct components. This makes it easy to swap a model without rewriting the parser.

**Explicit layout.** The page is visually understood before several structural decisions are made.

**Reading order as its own stage.** This avoids mixing text detection with the decision about how content should be read.

**Production and parallelism.** The current pipeline uses bounded queues and per-stage batching, showing a clear direction for scaling inference without turning each page into a monolithic call.

### 6.3. Limitations for our objective

For our MVP, adopting the entire Docling pipeline as the core would have costs:

* more models and more dependencies from the start;
* higher latency on simple digital PDFs;
* risk of a visual interpretation replacing native information that was already correct;
* greater difficulty in understanding exactly why a particular character was chosen.

Our product prioritizes textual recovery and auditability over semantic enrichment. Therefore, layout should guide decisions, but should not automatically erase the correct text layer.

### 6.4. Comparison with the previous architecture

The previous architecture already had a `RawPage` and a sequence of its own detectors. Docling shows that an explicit layer was missing between `RawPage` and reconstruction:

```text
BEFORE
RawPage → LineDetector → WordDetector → BlockDetector

REVISED
RawPage + PageImage
        ↓
PageEvidence
        ↓
LayoutRegions
        ↓
region-specific reconstruction
```

### 6.5. What we should incorporate

* decoupled backend;
* `StructuredDocument` as the main domain model;
* explicit stages for layout, OCR, table, assembly, and order;
* model batch processing;
* ability to swap `LayoutEngine`, `OcrEngine`, and `TableStructureEngine`.

### 6.6. What we should not copy

* running all models on every page by default;
* making layout classification a higher authority than native text without a confidence mechanism;
* expanding the MVP scope to formulas, images, and title semantics before textual recovery is solid.

---

## 7. MinerU

### 7.1. How the architecture works

MinerU offers more than one parsing route and has a particularly relevant hybrid backend. The current implementation uses pypdfium2 in parts of the pipeline and combines layout inference, OCR, specialized recognition, and VLM at different intensities.

A simplified representation of the hybrid path is:

```text
PDF
 ↓
classification / effort selection
 ↓
rendering + layout
 ↓
regions
 ├─ regular text
 ├─ table
 ├─ image
 ├─ formula
 └─ other types
 ↓
OCR/detection per candidate regions
 ↓
reconciliation in middle representation
 ↓
document handling
 ↓
merge of cross-page tables
 ↓
Markdown/JSON
```

The architecture uses the region as the unit of decision. OCR is not necessarily a blind operation over the entire page. There is specific code for cropping regions, masking formulas before OCR detection, and normalizing coordinates so that evidence sources can be combined.

### 7.2. Strengths for our case

**Truly hybrid pipeline.** Native text, OCR, layout, and VLM are not treated as mutually exclusive alternatives.

**Region as the unit of recovery.** This is exactly what we need for partially scanned pages or pages with rasterized tables in a PDF that also has native text.

**Intermediate representation before output.** Allows subsequent corrections without re-reading the PDF.

**Cross-page tables.** MinerU has explicit logic for joining continued tables. The implementation maintains structural state of rows, columns, `colspan`, `rowspan`, headers, and occupancy that crosses page boundaries.

**Continuation handling.** The logic does not rely solely on “there is a table at the end of one page and another at the start of the next”. It compares structure and headers and handles continuation texts and captions.

### 7.3. Limitations for our objective

MinerU is significantly heavier than the initially proposed MVP.

* multiple models;
* VLM in higher-quality routes;
* larger execution footprint;
* more points of failure;
* harder to embed as a small library;
* the project's current license contains additional terms beyond Apache 2.0 that must be considered if there is intent to reuse code directly.

Furthermore, our primary objective is not to convert formulas or understand images. We must absorb the fusion architecture without absorbing the entire product.

### 7.4. Comparison with the previous architecture

The previous architecture treated OCR as a late fallback, predominantly page-wide. MinerU demonstrates that this is insufficient.

Required change:

```text
BEFORE
bad page quality → page OCR → choose result

REVISED
layout region
   ↓
quality of native evidence in that region
   ├─ good → keep native
   ├─ absent → region OCR
   ├─ corrupted → OCR + comparison
   └─ majority of page is bad → promote to full page OCR
```

### 7.5. What we should incorporate

* region-oriented recovery;
* normalized coordinates for fusion;
* a rich intermediate representation;
* `CrossPageTableResolver` inspired by the structural signature concept;
* `rowspan`/`colspan` state during table fusion;
* repeated header analysis before concatenating tables;
* separation between normal effort and heavy recovery route.

### 7.6. What we should not copy

* mandatory VLM dependency for the MVP;
* formulas and images pipeline outside the textual objective;
* distributed services before we have evidence of operational need.

---

## 8. LiteParse

### 8.1. Why it is the most direct comparable

LiteParse is the project that most closely matches the initially proposed architecture. Its Rust core uses PDFium for native extraction, offers selective OCR, merge between OCR and native text, and spatial projection to reconstruct layout.

Conceptually:

```text
PDF
 ↓
PDFium native extraction
 ↓
ParsedPage
 ↓
complexity detection
 ├─ simple → native path
 └─ needs OCR → render + OCR
                     ↓
                 OCR merge
                     ↓
             grid projection
                     ↓
             text/structure
```

### 8.2. The most important component: complexity detection

LiteParse implements an explicit classification of reasons why a page needs more than the cheap text path.

Among the reasons present in the code are:

| Signal | Meaning for our product |
|---|---|
| `Scanned` | page covered by an image, practically no native text |
| `NoText` | page with no useful text layer |
| `SparseText` | native text exists, but its coverage appears insufficient |
| `EmbeddedImages` | there are relevant rasterized regions beyond the text |
| `Garbled` | text layer shows signs of broken CMap/Unicode |
| `VectorText` | visible content may be drawn as paths, not as text |
| `AnnotationText` | text appears visually in an annotation appearance stream, outside the normal text surface |

This model is extremely relevant. It shows that “does it have text or not?” is a weak question. Our `QualityAnalyzer` needs to answer **why** a page or region is not reliable.

### 8.3. Strengths

**PDFium as the foundation.** Validates our initial choice.

**Cheap path for simple documents.** Does not force models on every page.

**Selective OCR.** OCR is enrichment and recovery.

**Explicit merge.** Recognizes that native and OCR can coexist.

**Detection of cases invisible to the text API.** Vectors and annotations are especially relevant for unusual enterprise documents.

**Native implementation.** Rust avoids some of the overhead of many FFI calls per character.

### 8.4. Limitations

LiteParse's own results and documentation show that very complex layout, dense tables, old scans, and certain multi-column cases still benefit from heavier engines.

Grid projection is useful, but should not be our only reading solution. It is primarily a spatial representation and may lose region semantics.

### 8.5. Comparison with the previous architecture

The previous architecture was correct on the PDFium → geometry → reconstruction axis. What was missing was the explicit router.

The change is direct:

```text
BEFORE
PDFium → reconstruction → diagnosis → maybe OCR

BETTER
PDFium → cheap diagnosis → strategy per page/region
                             ├─ native reconstruction
                             ├─ selective OCR
                             └─ heavy visual route
```

### 8.6. What we should incorporate

* explicit complexity taxonomy;
* full-page image detection;
* textual coverage;
* image coverage;
* corrupted layer detection;
* detection of vectors not covered by text;
* annotation inspection when the page appears empty;
* `ExtractionDecision` with auditable reasons;
* native/OCR merge by geometry.

### 8.7. What we should improve compared to LiteParse

* not relying only on grid projection for complex layout;
* adding visual layout on complex pages/regions;
* creating a dedicated table pipeline;
* adding cross-page table continuity;
* preserving both `raw_text` and `reading_text` simultaneously.

---

## 9. Xberg

### 9.1. How the architecture works

Xberg follows a native and modular philosophy. For PDF, it has a Rust parser, OCR fallback, optional layout via ONNX models, table structure, and reading order strategies.

A particularly interesting aspect is that it models the extraction method as state:

```text
Native
Mixed
Ocr
```

This is superior to a simple `used_ocr` boolean, because pages and documents can genuinely be mixed.

### 9.2. The main lesson: prose and tables cannot blindly share the same order

Xberg's XY-cut code documents a real architectural flaw: attempts to make the column detector more sensitive to resolve two-column prose ended up interpreting table cell gaps as column splits and corrupting number sequences.

This lesson is decisive for our product:

> **Before applying XY-cut or another global reading heuristic, we must know whether the region represents prose, a table, or another structural type.**

Therefore:

```text
WRONG
entire page → XY-cut → text/table

CORRECT
page → layout regions
          ├─ prose → reading order resolver
          ├─ table → table pipeline
          ├─ figure → visual/OCR policy
          └─ marginalia → own policy
```

### 9.3. Cascaded tables

Xberg's native table subsystem uses multiple strategies in priority order:

1. strict grid detector;
2. relaxed detector for tables with lines and two or more columns;
3. heuristic reconstruction from the text layer for cases without borders or explicit grids.

A weaker strategy only needs to run when the previous one did not find a sufficient result.

This is a better architecture than choosing a single universal detector.

### 9.4. Optional and selective layout

The project also allows combining visual layout with PDF semantics. The current documentation emphasizes that layout can inform region, order, and table, while native signals remain relevant.

This stance aligns with our objective: using vision to complement, not automatically replace.

### 9.5. Comparison with the previous architecture

The old architecture had XY-cut as the initial reading strategy and left tables as a later concern. This needs to change.

The new sequence will be:

```text
LayoutRegionDetector
       ↓
RegionClassifier
       ↓
 ┌─────┼─────────────┐
 ▼     ▼             ▼
prose table          other
 │     │
 │     └─ TablePipeline
 └─────── ReadingOrderResolver
```

### 9.6. What we should incorporate

* `Native/Mixed/Ocr` as explicit provenance;
* XY-cut only in prose candidate regions;
* heading/wide line detection before column splits;
* cascade of table detectors;
* parser fallback and actionable warnings;
* separating `layout signal` from `content source`.

### 9.7. What we should not copy

There is no need to replace PDFium with a new Rust parser in the MVP. This would greatly increase the scope. Xberg's knowledge will be used primarily in reconstruction and fallback policies.

---

## 10. Unstructured

### 10.1. How the architecture works

Unstructured is more useful as a reference for **strategy routing** than as a reference for a low-level parser.

The current `partition_pdf` exposes explicit strategies:

```text
AUTO
FAST
HI_RES
OCR_ONLY
```

The `fast` path extracts text directly from the PDF, currently backed by pdfminer. The `hi_res` path uses a layout detector. `ocr_only` forces OCR. The `auto` mode observes whether the PDF text is extractable and chooses between the cheap path and the higher-resolution path.

The implementation also has specific pdfminer parameters for line margin, character, overlap, and word, showing that the project acknowledges that text reconstruction needs to be calibrated.

### 10.2. Strengths

**Understandable routing.** The user and the system know which strategy was used.

**Fallback when extraction fails.** An exception in the text parser does not need to abort the entire document.

**Cost separation.** A simple PDF does not need to pay for layout and OCR.

**Table structure integration in high-resolution mode.** Shows that structure and textual extraction are distinct concerns.

### 10.3. Limitations for our case

The pdfminer-based native path is not the choice I would make for our core, because we have already chosen PDFium to obtain geometry and Unicode in a way close to modern rendering engines.

Furthermore, the strategy in Unstructured is predominantly at the page/document level. Our objective requires additional granularity: good and bad regions can coexist on the same page.

### 10.4. Comparison with the previous architecture

Our architecture had a single main sequence and a late OCR fallback. Unstructured reinforces that we need to formalize strategy and fallback as part of the domain.

### 10.5. What we should incorporate

* `AUTO`, `NATIVE`, `HYBRID`, `OCR` as explicit modes;
* fallback without aborting the entire document;
* telemetry of the chosen strategy;
* separate quality versus speed configuration;
* future possibility of `fast` and `balanced` without changing the API.

### 10.6. What we should not incorporate

* pdfminer as the primary native source;
* decision only at the document level;
* coupling table structure only to high-resolution mode.

---

## 11. PaddleOCR

### 11.1. The correct role of PaddleOCR in our architecture

PaddleOCR is different from the previous parsers. It is primarily an OCR and visual Document AI platform. This makes it excellent precisely for the cases where the PDF does not offer us reliable native text.

The current family includes features such as:

* document orientation;
* deformation correction;
* line orientation;
* text detection and recognition;
* table recognition;
* formula recognition;
* document layout;
* structured pipelines such as PP-StructureV3;
* more complete visual models in the PaddleOCR-VL family.

There is explicit Portuguese support in multilingual models, which is essential for our pt-BR corpus.

### 11.2. Strengths

**Recovers content that does not exist in the text layer.** Scans, images, rasterized vectors, and text embedded in figures can be read.

**Portuguese.** Multilingual models include Portuguese and the Latin alphabet with diacritics.

**Orientation and unwarping.** Very relevant for scanned documents, photos, or misaligned pages.

**Layout and table.** We do not need to use OCR only as “image to string”; we can recover regions and structures.

**Local execution.** Keeps the possibility of on-premises processing.

### 11.3. Why it should not be the primary path

Performing OCR on a good digital PDF is generally a regression:

* transforms exact text into a prediction;
* may lose accents, punctuation, and small characters;
* may confuse similar-looking numbers;
* loses some font and content stream order information;
* costs more CPU/GPU;
* may generate a visually plausible structure that differs from the actual encoded text.

Therefore:

> **PaddleOCR should be our visual recovery sensor, not our default text source.**

### 11.4. Comparison with the previous architecture

The previous plan treated OCR as a future and generic step. The revision makes OCR a first-class component, but selective.

Suggested new contract:

```python
class OcrEngine(Protocol):
    def recognize_page(...): ...
    def recognize_region(...): ...
```

And, separately:

```python
class LayoutEngine(Protocol):
    def detect_regions(...): ...
```

Even if both are initially implemented with PaddleOCR components, they must remain distinct interfaces.

### 11.5. Recommended strategy for Portuguese

In the MVP:

* preserve native Unicode whenever reliable;
* configure OCR for an appropriate Latin/Portuguese model;
* normalize the final output to Unicode NFC only in the normalized view;
* never convert accents manually using fragile rules;
* keep the original OCR text and the normalized text separately;
* record confidence per token/line when the engine provides it.

### 11.6. What we should incorporate

* local multilingual OCR;
* orientation detection;
* optional unwarping for scans;
* pluggable visual layout;
* table structure model as a fallback from the deterministic cascade;
* OCR by region.

### 11.7. What we should not incorporate

* OCR of every digital page by default;
* dependency on the Markdown representation generated by the visual pipeline;
* use of a large VLM as an MVP requirement.

---

## 12. MarkItDown

### 12.1. How the architecture works for PDF

MarkItDown is intentionally lightweight and oriented toward conversion to Markdown. In the local PDF path, the current implementation imports pdfminer and pdfplumber. There is additional logic for tables/forms based on word positions and column alignment heuristics.

The project also allows plugins and integration with higher-capacity external services, such as vision OCR or Azure services.

### 12.2. Strengths

**Simple API.** The product does not expose backend complexity to the consumer.

**Plugins.** Heavy features can be opt-in.

**Fallback via external service.** Shows a clean way to keep the core lightweight.

**Specific heuristics for enterprise documents.** The current converter has rules for forms and partial numbering, showing the usefulness of corrections oriented toward real document patterns.

### 12.3. Limitations for our objective

MarkItDown optimizes for LLM-consumable Markdown, not for maximum auditable textual recovery.

The use of pdfminer/pdfplumber and form heuristics does not offer as rich a foundation as PDFium + per-character geometry + page objects.

There is also no strategy in the local core comparable to the detailed evidence fusion we want.

### 12.4. Comparison with the previous architecture

Our previous architecture was already technically deeper on the PDF axis. Therefore we should not change the core because of MarkItDown.

### 12.5. What we should incorporate

* simple API on top of a complex implementation;
* replaceable plugins/engines;
* Markdown renderer separated from the core;
* future possibility of an external `RecoveryProvider` without contaminating the local pipeline.

### 12.6. What we should not incorporate

* Markdown as an internal model;
* pdfplumber as the sole table mechanism;
* specific layout heuristics applied before we have a robust evidence structure.

---

## 13. Marker

### 13.1. The most relevant architecture for quality decisions

Marker has one of the most interesting architectures for our problem because it does not consider “the PDF has text” to be sufficient to trust that text.

The current pipeline builds document, layout, lines, OCR, and structure in separate components. The `LineBuilder` decides per page whether the embedded layer is good or whether the page needs OCR.

The decision uses signals such as:

* OCR/text error classifier;
* coverage of native lines relative to layout blocks;
* abnormal overlaps between lines, useful for detecting duplicated or bad OCR layers;
* bboxes outside the page;
* visual verification to discard lines whose rendered region is blank;
* per-block analysis on predominantly good pages.

The `fast` mode also implements a particularly good idea:

> **a good page may contain only a few bad blocks; those blocks are repaired individually. If many blocks are bad, the page is promoted to full OCR.**

### 13.2. Native order as a strong signal

Marker also provides important evidence against a common assumption: a learned reading order model should not automatically replace the native sequence.

In the current code, pages with reliable text can use the position of characters in the extracted stream as the main order signal. The implementation comment records that, in the project's benchmark, this order beat the learned head on multiple columns.

For our product the conclusion is not “extracted native sequence always wins”. The correct conclusion is:

> **extracted native sequence is a first-class piece of evidence and should compete with geometry and layout, not be discarded.**

### 13.3. Strengths

**Quality as an explicit decision.** Much better than simply testing for an empty string.

**Validation against the image.** The rendered page serves as evidence that a bbox actually contains visible ink.

**OCR by block.** Essential for hybrids.

**Adaptive promotion.** Avoids hundreds of crops when the entire page is bad.

**Layout and native text cooperate.** Layout validates coverage without needing to replace the characters.

### 13.4. Limitations for our case

Marker depends on its own models from the Datalab/Surya ecosystem for much of its advanced quality. The weights have terms of use that need to be analyzed separately for an enterprise product.

Furthermore, features such as sophisticated cross-page table merging may depend on LLM in hybrid mode. We want a first deterministic implementation for that case.

### 13.5. Comparison with the previous architecture

This is the largest design change caused by the study.

Before:

```text
QualityAnalyzer per page
  ↓
OCR fallback
```

After:

```text
PageQualityAnalyzer
  +
LayoutCoverageAnalyzer
  +
NativeTextVisualVerifier
  ↓
RegionQualityMap
  ↓
for each region:
    KEEP_NATIVE
    MERGE_OCR
    REPLACE_WITH_OCR
    ESCALATE_PAGE_OCR
```

### 13.6. What we should incorporate

* validation of the text layer against layout;
* detection of abnormal overlaps;
* ink verification in the image for invisible text;
* repair by block/region;
* promotion to page OCR when the fraction of bad regions exceeds a threshold;
* extracted native sequence as an explicit signal;
* `fast` mode and `balanced` mode in the future.

### 13.7. What we should not copy

* mandatory dependency on Surya models;
* mandatory LLM for table fusion;
* Marker-specific benchmark rules as if they were universal.

---

## 14. Comparison of the previous architecture with all approaches

The table below summarizes what changes in the original plan after this revision.

| Aspect | Previous architecture | Evidence from projects | Revised decision |
|---|---|---|---|
| PDF backend | PDFium | LiteParse and Docling validate the choice | keep PDFium |
| Basic unit | character | correct, but insufficient alone | keep characters + objects + raster |
| Diagnosis | later `QualityAnalyzer` | LiteParse and Marker gate early | move diagnosis to the start |
| OCR | predominantly page-level fallback | Marker/MinerU recover regions | OCR by region + page promotion |
| Layout | derived mainly by geometry | Docling/Marker/Paddle/Xberg use a dedicated detector | add `LayoutEngine` |
| Order | XY-cut as initial strategy | Xberg shows conflict with tables; Marker values extracted native sequence | use graph + multiple signals, only on prose |
| Table | later concern | Xberg/Paddle/MinerU treat as subsystem | create own `TablePipeline` |
| Cross-page table | outside the core | MinerU has structural merge | include in MVP |
| Invisible text | local rule | Marker validates bbox against image | add `VisualInkVerifier` |
| Vector simulating text | not very explicit | LiteParse detects uncovered vector area | add `vector_text` signal |
| Annotations | secondary | LiteParse detects appearance text | inspect when page appears empty |
| Provenance | partially planned | Xberg Native/Mixed/OCR reinforces value | provenance per token/region |
| Document | independent pages | Docling/MinerU have a document stage | add `DocumentAssembler` |
| Output | text/raw/JSON | market trends toward Markdown | keep structure as source of truth; Markdown is a renderer |

---

## 15. Which approach is the best?

### 15.1. If we had to choose a single project

For a **technical core similar to what we want to build**, the best comparable is **LiteParse**.

Reasons:

* PDFium;
* local extraction;
* complexity detection;
* selective OCR;
* native/OCR merge;
* preserved geometry;
* relatively small architecture.

However, choosing only LiteParse as inspiration would still leave gaps in precisely the most important classes for the company: complex tables, cross-page continuity, and highly irregular layouts.

### 15.2. If we choose the best approach per layer

The best solution becomes:

```text
                 PDFium / LiteParse approach
                           │
                           ▼
             Quality Gate LiteParse + Marker
                           │
                           ▼
        Layout Docling / PaddleOCR / Xberg
                           │
              ┌────────────┴────────────┐
              ▼                         ▼
       Text/prose                   Table
   Marker + Xberg             Xberg + PaddleOCR
              │                         │
              │                         ▼
              │                 cell structure
              │                         │
              └────────────┬────────────┘
                           ▼
                   selective OCR
                   Marker + MinerU
                           │
                           ▼
                    Evidence Fusion
                           │
                           ▼
              CrossPageTableResolver
                        MinerU
                           │
                           ▼
                  StructuredDocument
```

### 15.3. Comparison by architectural families

The eight projects can be grouped into four families, which helps explain why none of them alone meets our objective.

| Family | Projects | Advantage | Weakness for our case |
|---|---|---|---|
| adaptive native first | LiteParse, Xberg | speed, auditability, preserves digital text | needs visual reinforcement for extreme layouts/tables |
| hybrid document AI | Docling, MinerU, Marker | layout, OCR and structure integrated | higher cost and complexity; risk of using a model where exact text was sufficient |
| visual first | PaddleOCR | excellent on scans and content without a text layer | transforms exact digital text into probabilistic recognition if always used |
| orchestrator/converter | Unstructured, MarkItDown | API, routing, extensibility | does not offer the best PDF core for our fidelity goal |

Our architecture deliberately sits between the first two families: **native first in acquisition, hybrid in recovery**. PaddleOCR enters as a visual sensor, and the orchestration ideas from Unstructured/MarkItDown appear in the interfaces and modes, not in the textual core.

### 15.4. Recommendation

**The new architecture should be our own and based on evidence fusion.** We should not build “a PyMuPDF clone”, “a LiteParse in Python”, or “a smaller MinerU”.

We should build an engine that has a property that not all of these projects make central:

> **each piece of text knows where it came from, why it was accepted, and what competing evidence existed.**

This property will be our main tool for increasing robustness with real documents without accumulating magic corrections.

---

## 16. Revised architecture: Hybrid Evidence Fusion Pipeline

We will call the MVP architecture the **Hybrid Evidence Fusion Pipeline**.

The word *hybrid* does not mean that every document will be processed by OCR or a visual model. It means the engine can combine more than one source when necessary.

The word *evidence* is even more important: no OCR result, no spatial heuristic, and no layout decision should become final text unless the system can record where it came from.

### 16.1. Full view

```text
┌─────────────────────────────────────────────────────────────────────────────┐
│ 0. Document Intake                                                         │
│ bytes, password, limits, metadata, pages                                   │
└─────────────────────────────┬───────────────────────────────────────────────┘
                              ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ 1. Native Evidence Collector — PDFium                                      │
│ chars | text ranges | native extracted sequence | fonts | bboxes | paths | images       │
│ annotations | page geometry | rotations                                    │
└─────────────────────────────┬───────────────────────────────────────────────┘
                              │
                    render low resolution
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ 2. Page Evidence & Complexity Analyzer                                     │
│ text coverage | image coverage | garbling | duplicates | blank glyphs      │
│ vector text | annotation text | scan likelihood | rotation                 │
└─────────────────────────────┬───────────────────────────────────────────────┘
                              ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ 3. Layout Region Detector                                                  │
│ prose | title | list | table | figure | header | footer | marginalia       │
└─────────────────────────────┬───────────────────────────────────────────────┘
                              ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ 4. Native Reconstruction & Region Assignment                               │
│ character normalization → line candidates → native tokens → region mapping │
└─────────────────────────────┬───────────────────────────────────────────────┘
                              ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ 5. Region Quality Gate                                                     │
│ KEEP_NATIVE | MERGE_OCR | OCR_REGION | OCR_PAGE                            │
└──────────────┬───────────────────────────┬──────────────────────────────────┘
               │                           │
               ▼                           ▼
┌────────────────────────────┐  ┌────────────────────────────────────────────┐
│ 6A. Prose Reading Order    │  │ 6B. Table Pipeline                         │
│ stream + geometry + layout │  │ ruled → relaxed → text → model fallback   │
│ region graph + XY-cut      │  │ cell graph + confidence                    │
└──────────────┬─────────────┘  └────────────────┬───────────────────────────┘
               │                                 │
               └──────────────┬──────────────────┘
                              ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ 7. OCR Recovery                                                            │
│ region OCR or whole page OCR, orientation/unwarping when required          │
└─────────────────────────────┬───────────────────────────────────────────────┘
                              ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ 8. Evidence Fusion                                                         │
│ native + OCR + layout + table evidence, dedup, conflict resolution         │
└─────────────────────────────┬───────────────────────────────────────────────┘
                              ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ 9. Page Assembler                                                          │
│ regions, lines, paragraphs, tables, raw/reading views                      │
└─────────────────────────────┬───────────────────────────────────────────────┘
                              ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ 10. Document Assembler                                                     │
│ repeated header/footer, cross-page tables, page continuity, provenance     │
└─────────────────────────────┬───────────────────────────────────────────────┘
                              ▼
                raw_text | reading_text | JSON | Markdown
```

### 16.2. Golden rule

The pipeline must try to solve each problem at the cheapest and most deterministic level possible.

```text
1. Does the PDF contain correct text?              → use that text
2. Does geometry resolve the structure?            → use geometry
3. Does visual layout help separate regions?       → use layout
4. Is only one region bad?                         → region OCR
5. Is the entire page bad?                         → full page OCR
6. Does deterministic table detection work?        → use deterministic detector
7. Is the table still ambiguous?                   → use structure model
8. Is there still conflict?                        → preserve alternatives and confidence
```

The order matters. The later we enter probabilistic inference, the lower the chance of swapping exact text for “plausible” text.

---

## 17. Mandatory architectural principles

### 17.1. Raw evidence is immutable

`NativeEvidence` must never be altered by normalization, deduplication, or OCR.

If a character was extracted as `ã`, it remains available even if a later stage normalizes some other detail.

If two overlapping copies of a word are detected, both remain in the raw evidence, while the reading view chooses only one.

### 17.2. Provenance in all derived elements

Every relevant token must be able to indicate its origin.

```python
class SourceKind(Enum):
    NATIVE_PDF = "native_pdf"
    NATIVE_GENERATED = "native_generated"
    OCR_REGION = "ocr_region"
    OCR_PAGE = "ocr_page"
    TABLE_NATIVE = "table_native"
    TABLE_MODEL = "table_model"
    RECOVERED_UNICODE = "recovered_unicode"
```

Fused elements may carry more than one source.

### 17.3. OCR never silently destroys reliable text

If OCR and the native layer disagree, the system needs to know there was a conflict.

In the MVP the conservative rule will be:

```text
reliable native + divergent OCR     → keep native, record OCR
corrupted native + reliable OCR     → choose OCR
absent native + OCR                 → choose OCR
partial native + complementary OCR  → geometric merge
```

### 17.4. Table is not prose in columns

This principle becomes architectural, not just a heuristic.

No column reading algorithm should internally reorder a region already classified as a table.

### 17.5. Page is not the only unit of decision

We need the levels:

```text
document
  page
    region
      line/token/char
```

A page can be 80% perfect native text and 20% scan. Forcing a single method for the entire page loses information.

### 17.6. Structure precedes serialization

Do not build Markdown during detection.

Correct flow:

```text
evidence → structure → renderer
```

Not:

```text
evidence → Markdown → attempt to reconstruct structure
```

---

## 18. Revised data model

### 18.1. `BBox`

```python
@dataclass(frozen=True)
class BBox:
    x0: float
    y0: float
    x1: float
    y1: float
```

All internal geometries must use a single canonical system.

Suggestion:

* origin at the top-left corner;
* `x` grows to the right;
* `y` grows downward;
* internal unit in PDF points when the evidence comes from the PDF;
* OCR pixels always converted to this space before the merge.

The original object must maintain metadata for reversible conversion.

### 18.2. `NativeCharacter`

```python
@dataclass(frozen=True)
class NativeCharacter:
    page_index: int
    char_index: int
    text: str
    unicode_codepoint: int | None
    bbox: BBox
    origin: Point
    angle: float
    font_name: str | None
    font_size: float | None
    font_weight: int | None
    generated: bool
    hyphen: bool
    unicode_mapping_failed: bool
    visible_candidate: bool
```

`char_index` is important because it maintains the order exposed by PDFium. This order **must not be confused with the raw order of the `Tj/TJ` operators in the content stream**. `FPDFText_LoadPage` has already built a text page, may insert generated characters, and applies some of its own interpretation. Therefore, we will call this signal the **native extracted sequence**. It is valuable, but it is only one piece of evidence among others.

### 18.3. `NativeObjectEvidence`

```python
@dataclass(frozen=True)
class NativeObjectEvidence:
    images: tuple[ImageEvidence, ...]
    paths: tuple[PathEvidence, ...]
    annotations: tuple[AnnotationEvidence, ...]
    structure_tree: StructureTreeEvidence | None
    page_bbox: BBox
    crop_bbox: BBox
    rotation: int
```

This structure is necessary for detecting scans, table grids, vector text, and appearance text.

### 18.4. `OcrToken`

```python
@dataclass(frozen=True)
class OcrToken:
    text: str
    bbox: BBox
    confidence: float | None
    language: str | None
    source: SourceKind
```

### 18.5. `TextToken`

This is the first derived object capable of representing fusion.

```python
@dataclass
class TextToken:
    text: str
    bbox: BBox
    sources: list[EvidenceRef]
    confidence: float
    normalized_text: str | None
    flags: set[TokenFlag]
```

### 18.6. `TextLine`

```python
@dataclass
class TextLine:
    tokens: list[TextToken]
    bbox: BBox
    baseline: Baseline | None
    direction: WritingDirection
    native_order_min: int | None
    native_order_max: int | None
```

### 18.7. `LayoutRegion`

```python
class RegionKind(Enum):
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
    UNKNOWN = "unknown"
```

```python
@dataclass
class LayoutRegion:
    region_id: str
    kind: RegionKind
    bbox: BBox
    layout_confidence: float | None
    native_lines: list[TextLine]
    ocr_tokens: list[OcrToken]
    quality: RegionQuality
```

### 18.8. `TableCell`

```python
@dataclass
class TableCell:
    row: int
    col: int
    rowspan: int
    colspan: int
    bbox: BBox | None
    text: str
    tokens: list[TextToken]
    confidence: float
```

### 18.9. `StructuredTable`

```python
@dataclass
class StructuredTable:
    table_id: str
    page_fragments: list[TableFragment]
    cells: list[TableCell]
    column_count: int
    row_count: int
    confidence: float
    method: TableMethod
    continued_from_previous_page: bool
    continues_to_next_page: bool
```

### 18.10. `StructuredPage`

```python
@dataclass
class StructuredPage:
    page_index: int
    bbox: BBox
    regions: list[LayoutRegion]
    tables: list[StructuredTable]
    raw_text: str
    reading_text: str
    diagnostics: PageDiagnostics
```

### 18.11. `StructuredDocument`

```python
@dataclass
class StructuredDocument:
    pages: list[StructuredPage]
    tables: list[StructuredTable]
    raw_text: str
    reading_text: str
    metadata: DocumentMetadata
    diagnostics: DocumentDiagnostics
```

---

## 19. Stage 0 — Document Intake

### 19.1. Responsibilities

* validate the PDF header;
* open the document;
* handle password when configured;
* obtain page count;
* apply security limits;
* record the PDFium version and configured engines;
* create `DocumentContext`.

### 19.2. MVP security limits

Must be configurable:

```python
@dataclass
class SecurityLimits:
    max_pages: int = 5000
    max_file_size_bytes: int = 1_000_000_000
    max_render_pixels: int = 100_000_000
    document_timeout_seconds: float | None = None
```

The final values must be defined by the company's environment.

### 19.3. Partial failure

An error on one page must not necessarily invalidate the others.

Possible result:

```text
SUCCESS
PARTIAL_SUCCESS
FAILURE
```

---

## 20. Stage 1 — Native Evidence Collector

This is the high-fidelity foundation.

### 20.1. What to collect from PDFium

Per page:

* character count;
* Unicode of each character;
* bbox;
* origin;
* angle;
* font size;
* available font information;
* PDFium textual sequence index;
* generated character indicator;
* hyphen indicator;
* Unicode mapping error when exposed;
* text by range;
* images placed on the page;
* relevant paths;
* annotations;
* structure tree from tagged PDFs, when present;
* MCIDs/structural attributes that the API allows recovering;
* page rotation;
* MediaBox/CropBox;
* matrix needed to normalize coordinates.

### 20.2. Precision about the native sequence

The character index exposed by PDFium represents the `FPDF_TEXTPAGE` sequence, not a literal recording of the content stream operator order. PDFium can produce generated characters, including breaks, and exposes experimental APIs such as `FPDFText_IsGenerated`, `FPDFText_IsHyphen`, and `FPDFText_HasUnicodeMapError`.

Therefore, the engine must record separately:

```text
pdfium_char_index
is_generated
is_hyphen
has_unicode_map_error
```

When we can safely recover the page object order, it can enter as additional evidence, but we must not call `char_index` the “original PDF order”.

These APIs include experimental PDFium points. The implementation must perform **feature detection on the version packaged by pypdfium2** and use low-level bindings only when available. The absence of `IsGenerated`, `IsHyphen`, or `HasUnicodeMapError` in a version cannot block the entire extraction; the corresponding fields must accept `None`/`unknown` and diagnostics must record the effectively available capability.

### 20.3. Structure tree as optional evidence

Tagged PDFs may carry a logical tree with elements such as paragraph, heading, list, table, and cells. PDFium has a public structure tree API (`FPDF_StructTree_GetForPage` and the `FPDF_StructElement_*` family).

When present, this information must be collected as **high-value evidence, but not absolute truth**. Many PDFs are not tagged; others have incomplete or incorrect tags.

Suggested use:

```text
structure tree
  → region type signal
  → additional order signal
  → table/cell signal
  → headings/semantics when consistent
```

Do not block the MVP if the perfect association between MCID and token requires additional work. The first step can preserve the tree and its identifiers for progressive use.

### 20.4. Batch extraction

The initial implementation in pypdfium2 may call APIs per character. This is simple, but the FFI overhead needs to be measured.

The design must hide this detail behind:

```python
class NativeEvidenceSource(Protocol):
    def extract_page(self, page_index: int) -> NativePageEvidence:
        ...
```

If the overhead is relevant, we can swap the implementation for a Rust/PyO3 extension that returns arrays in batches without modifying the rest of the system.

### 20.5. Do not reconstruct yet

This stage must not decide:

* words;
* paragraphs;
* columns;
* tables;
* headers;
* final order.

It collects facts.

---

## 21. Stage 2 — Page Evidence & Complexity Analyzer

This stage is inspired primarily by LiteParse and Marker.

### 21.1. Output

```python
@dataclass
class PageComplexity:
    reasons: set[ComplexityReason]
    native_text_score: float
    visual_recovery_needed: bool
    layout_needed: bool
    full_page_ocr_candidate: bool
```

### 21.2. Initial reasons

```python
class ComplexityReason(Enum):
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
```

### 21.3. Cheap signals

Calculate first without models:

* `native_char_count`;
* useful text length;
* U+FFFD ratio;
* control character ratio;
* unlikely codepoint ratio;
* geometric text coverage;
* image count/area;
* largest image relative to page;
* filled path area not covered by text;
* abnormal line/character overlaps;
* angle distribution;
* bbox density.

### 21.4. Garbled text

Possible signals:

```text
high proportion of replacement chars
many control characters
private codepoint sequences without explanation
abnormal repetitions
large text with practically zero vocabulary
Unicode mapping marked as failed
```

Do not use a Portuguese dictionary as the sole proof. Names, processes, codes, and legitimate identifiers may look like “bad words”.

### 21.5. Duplicate OCR layer

Detect nearly identical bboxes with identical or highly similar strings.

Also detect many overlapping lines at incompatible positions, inspired by Marker.

### 21.6. Invisible text

This is an important area for PDFs with old OCR layers.

For suspicious lines:

1. map bbox to the rendered page;
2. measure whether ink/non-white pixel exists in the region;
3. if there is no visual content where the text claims to be, mark as `INVISIBLE_TEXT`.

Do not perform individual crops for thousands of characters. Do it by line or block and, if possible, use integral image/ink mask for cheap lookup.

### 21.7. Vector text

If the page has a significant area of filled paths that is not explained by text bboxes, mark as possible `VECTOR_TEXT`.

The objective is not to identify vector letters perfectly. The objective is to trigger visual inspection/OCR.

### 21.8. Annotation text

If the page appears empty but has relevant annotations/appearance streams, consider OCR or additional inspection.

### 21.9. The result must not be just a score

We do not want:

```text
quality = 0.63
```

without explanation.

We want:

```json
{
  “native_text_score”: 0.63,
  “reasons”: [“garbled_unicode”, “embedded_images”],
  “recommended_strategy”: “hybrid”
}
```

---

## 22. Stage 3 — Layout Region Detector

### 22.1. Why visual layout becomes part of the MVP

Textual geometry alone cannot safely answer whether two vertical strips represent:

* two article columns;
* two columns of a table;
* text and a caption;
* sidebar and body;
* form values;
* two independent areas.

The biggest change in the architecture is using layout as a signal prior to reading order.

### 22.2. Contract

```python
class LayoutEngine(Protocol):
    def detect(self, image: PageImage) -> list[LayoutRegionPrediction]:
        ...
```

The interface must not depend on PaddleOCR, Docling, or another vendor.

### 22.3. Recommended initial engine

For the MVP, the **first concrete candidate will be PP-DocLayoutV3**, or the equivalent standalone distribution made available by the PaddleOCR ecosystem in the version fixed by the project, for three reasons:

* direct Python integration;
* models prepared for documents;
* natural path for table and OCR in the same ecosystem.

A valid alternative is RT-DETR/ONNX in the Xberg style.

The definitive choice should consider:

* weight license;
* CPU performance;
* performance on GPU available in the company;
* recognized classes;
* accuracy on the real corpus;
* model size;
* ease of on-premises packaging.

### 22.4. Layout is not textual authority

The detector can say:

```text
bbox X = TABLE
```

but cannot invent or replace the textual content of that bbox.

Its role is:

```text
where is the region?
what structural type does it likely have?
how should we route it?
```

### 22.5. Minimum classes for the MVP

We do not need dozens of unused types. Minimum classes:

```text
TEXT
TITLE
LIST
TABLE
FIGURE
CAPTION
HEADER
FOOTER
FOOTNOTE
UNKNOWN
```

### 22.6. Layout on every page or only on complex pages?

To maximize quality, I propose two profiles from the MVP:

**`fast`**

* runs deterministic analysis;
* uses visual layout only when complexity indicates the need.

**`balanced`**

* runs low-resolution layout on all pages;
* continues avoiding OCR when the text layer is good.

During development with real company documents, `balanced` should be the evaluation default. Then we measure whether `fast` delivers sufficient quality in simple categories.

### 22.7. Assignment of native text to regions

After the visual regions, each native line/token is associated by intersection.

Do not use only the bbox center. We suggest:

```text
intersection_area(text_bbox, region_bbox) / text_bbox_area
```

With priority for the region with the greatest coverage.

Ambiguous cases are marked, not discarded.

---

## 23. Stage 4 — Native text reconstruction

The engine still needs to do the work that originally motivated the project: reconstructing characters into lines and tokens better than simply using a string returned by the parser.

### 23.1. Conservative normalization

Maintain two forms:

```python
raw_text: str
normalized_text: str
```

Normalizations allowed in the normalized view:

* Unicode NFC;
* equivalent whitespace to normal space when appropriate;
* removal of semantics-free control characters;
* line ending normalization.

Not:

* replacing accented characters with ASCII;
* “correcting” words by dictionary;
* altering numbers;
* guessing a letter from context without additional evidence.

### 23.2. Deduplication

Duplicates may arise from:

* text painted more than once;
* fake bold;
* duplicated OCR layer;
* shadow;
* repeated content streams.

Criteria:

```text
same text/codepoint
+ bbox with high overlap
+ very close origin
+ compatible font/size
```

The action will be to mark evidence as a derived duplicate, not delete it from raw evidence.

### 23.3. Line formation

Group first by orientation.

For horizontal characters, estimate baseline and median height.

Adaptive characteristics:

* perpendicular difference from baseline;
* vertical overlap;
* font size;
* direction;
* distance normalized by size/advance;
* native extracted sequence.

Do not use a universal fixed tolerance in points.

### 23.4. Spaces

Confidence hierarchy:

```text
1. explicit whitespace in the PDF
2. whitespace generated by the engine
3. inferred geometric gap
```

The geometric threshold should be learned per line/span.

Example:

```text
median_char_advance = median of advances
word_gap_candidate = gap / median_char_advance
```

The local distribution allows differentiating kerning from actual space better than a fixed value.

### 23.5. Words

A word is a derived view, not a fundamental unit.

Keep punctuation in the appropriate token as output, but preserve individual characters in the evidence.

### 23.6. Spans

A span can be created when adjacent characters share:

* font;
* compatible size;
* weight;
* style;
* color when available;
* orientation;
* evidence source.

Do not use spans to decide global reading.

---

## 24. Stage 5 — Region Quality Gate

After layout and native reconstruction, each region receives a decision.

### 24.1. States

```python
class RegionDecision(Enum):
    KEEP_NATIVE = "keep_native"
    MERGE_OCR = "merge_ocr"
    OCR_REGION = "ocr_region"
    ESCALATE_PAGE_OCR = "escalate_page_ocr"
```

### 24.2. `KEEP_NATIVE`

Use when:

* native text exists;
* Unicode is healthy;
* visual coverage is plausible;
* there is no destructive duplication;
* region has assigned lines;
* image does not suggest missing important text.

### 24.3. `MERGE_OCR`

Use when:

* region contains image/figure alongside text;
* native text covers only part of the visual content;
* there is suspicion of rasterized labels;
* table has some rasterized values.

### 24.4. `OCR_REGION`

Use when:

* layout detected a textual region without native text;
* text is garbled;
* native text is invisible or inconsistent with the image;
* there is a strong vector text signal;
* table/figure requires visual reading.

### 24.5. `ESCALATE_PAGE_OCR`

Use when:

* page is a scan;
* a large portion of textual regions is bad;
* the cost of dozens of crops exceeds full page OCR;
* orientation/unwarping needs to be handled globally.

### 24.6. Promotion threshold

Do not fix definitively before the corpus.

Start with a simple metric:

```text
bad_text_area / total_text_region_area
```

plus

```text
bad_text_regions / total_text_regions
```

If both are high, promote.

---

## 25. Stage 6A — Prose reading order

### 25.1. No single source is sufficient

We will use four families of evidence:

```text
NATIVE EXTRACTED SEQUENCE
sequence from the backend's text page

LOGICAL STRUCTURE
structure tree / tags / MCIDs when present and consistent

GEOMETRY
position, columns, gaps, alignment

VISUAL LAYOUT
detected regions and types
```

### 25.2. Build a graph, not just sort by `(y, x)`

Each prose region becomes a node.

Edges candidate relationships:

```text
A before B
```

with weight derived from:

* `A` above `B` with horizontal overlap;
* `A`'s column preceding `B`'s column;
* native extracted sequence;
* structure tree order/relationships when reliable;
* layout order when provided;
* baseline/paragraph continuity;
* spatial distance.

Then resolve a consistent order.

### 25.3. XY-cut

XY-cut remains useful, but now only within regions or groups classified as prose.

Recommended use:

```text
page prose regions
   ↓
separate full-width strips
   ↓
XY-cut on remaining groups
   ↓
columns
```

This prevents a wide title spanning two columns from being split incorrectly.

### 25.4. Native extracted sequence

If the native layer is healthy and the native extracted sequence already traverses the columns correctly, we should weight it heavily.

Create a score:

```text
native_order_consistency
```

measuring how many transitions of the native extracted sequence are geometrically plausible.

If high, native extracted sequence receives strong weight.

If low, geometry/layout dominate.

### 25.5. Two columns

Expected scenario:

```text
Full-width title

Column A          Column B
A1                B1
A2                B2
A3                B3

Full-width footer
```

Desired structure:

```text
Title
ColumnGroup
  Column A
  Column B
Footer
```

Not:

```text
Title
A1
B1
A2
B2
...
```

### 25.6. Rotated text

Group by canonical orientation close to:

```text
0°, 90°, 180°, 270°
```

Small rotations can be normalized by tolerance; arbitrary rotations must preserve quad/bbox and be treated as their own group.

Vertical marginal texts must not be inserted in the middle of the body simply because they share Y.

---

## 26. Stage 6B — Table Pipeline

Tables become an explicit part of the MVP.

### 26.1. Why a cascade

No single table detector is optimal in all cases.

We have at least these classes:

```text
A. table with complete borders
B. table with some lines
C. borderless table, aligned by columns
D. irregular table
E. digital table with bad text
F. table in a scan
G. table continued on another page
```

The strategy should be progressive.

### 26.2. Tier 1 — Strict vector grid

Use PDF paths/lines.

Detect:

* horizontal segments;
* vertical segments;
* intersections;
* rectangles;
* column/row tracks.

High precision.

If a coherent grid is found, assign tokens per cell.

### 26.3. Tier 2 — Relaxed grid

For:

* incomplete borders;
* horizontal lines only;
* main separators only;
* two-column label/value tables.

Use text alignment to complete the structure.

### 26.4. Tier 3 — Borderless table by text

Use native lines/tokens.

Signals:

* recurring X tracks;
* right-aligned numbers;
* labels in the first column;
* similar distribution across multiple lines;
* recurring horizontal gaps;
* vertical coherence;
* low “continuous prose”.

We need a deterministic `ProseVsTableClassifier` before accepting.

### 26.5. Tier 4 — Visual structure model

If layout detected `TABLE` but deterministic tiers failed or produced low confidence, run a structural model.

Evaluation candidates:

* PaddleOCR Table Recognition v2;
* SLANet/SLANeXT;
* TATR;
* another ONNX model with adequate license.

The contract must be ours:

```python
class TableStructureEngine(Protocol):
    def recognize(self, image: RegionImage) -> TableStructurePrediction:
        ...
```

### 26.6. Tier 5 — OCR inside cells/region

The model can recover structure without perfect text. Then:

* if there is reliable native text, map native tokens to cells;
* if there is none, use OCR;
* if there are both, merge.

### 26.7. Table confidence

Combine:

```text
grid coherence
row consistency
column track stability
cell assignment coverage
native text coverage
model confidence
OCR confidence
```

### 26.8. Do not transform table to Markdown early

Store cells first.

Markdown is just a renderer:

```text
StructuredTable → MarkdownTableRenderer
```

This is essential for `rowspan`, `colspan`, empty cells, and cross-page tables.

---

## 27. Cross-page continued tables

This requirement is now part of the MVP, because it was explicitly cited as an important real case.

### 27.1. Problem

Page 10:

```text
| Process | Party | Amount |
| ...     | ...   | ...    |
| 123     | João  | 900    |
```

Page 11:

```text
| Process | Party | Amount |
| 124     | Maria | 300    |
| ...     | ...   | ...    |
```

They may be:

* two independent tables;
* a single continued table with a repeated header;
* a table without a header on the second page;
* a table whose first row on the second page is a continuation of a cell from the previous page.

### 27.2. `TableSignature`

```python
@dataclass
class TableSignature:
    column_count: int
    normalized_x_tracks: tuple[float, ...]
    header_rows: tuple[RowSignature, ...]
    first_data_row: RowSignature | None
    last_data_row: RowSignature | None
    bbox: BBox
    page_index: int
    touches_top: bool
    touches_bottom: bool
```

### 27.3. `RowSignature`

Conceptually inspired by MinerU:

```python
@dataclass
class RowSignature:
    effective_columns: int
    colspans: tuple[int, ...]
    rowspans: tuple[int, ...]
    normalized_cells: tuple[str, ...]
```

### 27.4. Continuation evidence

Score:

* table A near the end of the page;
* table B near the start of the next;
* compatible column count;
* compatible X tracks;
* identical or similar header;
* second page starts directly with data;
* text “continuation”, “continues”, “cont.” or equivalents;
* no strong new title between pages;
* no drastic width change;
* similar cell types per column.

### 27.5. Repeated header

If B repeats A's header, do not duplicate the header in the table's logical structure, but preserve the per-page occurrence in `page_fragments`.

### 27.6. Cross-page rowspan

If the structure suggests an open cell at the end of the page, maintain logical occupancy for the next fragment.

Does not need to resolve every `rowspan` case in the MVP, but the data model needs to allow evolution.

### 27.7. Do not lose pagination

Even after merge:

```python
table.page_fragments
```

must indicate where each row/cell appeared.

This is important for auditing and eventual highlighting in the PDF.

---

## 28. Stage 7 — OCR Recovery

Despite the numbering, `OCR Recovery` should be implemented as a **service callable by region processors**, not as a mandatory linear pass after all prose and tables. A table region, for example, can call OCR before cell assembly; a prose region can remain 100% native. The stage diagram represents logical dependencies, not the need to execute each block in sequence for every page.

### 28.1. Recommended engine

PaddleOCR is the first engine to be evaluated for the MVP.

Reasons:

* Portuguese;
* modern OCR;
* detection + recognition;
* orientation;
* unwarping possibility;
* table and layout models close to the same ecosystem;
* local execution.

### 28.2. OCR by region

Flow:

```text
LayoutRegion bbox in points
      ↓
convert to pixel bbox
      ↓
add small margin
      ↓
crop at adequate resolution
      ↓
OCR
      ↓
convert tokens to canonical PDF coordinates
```

### 28.3. Resolution

Start with:

```text
150 to 200 DPI → normal text
250 to 300 DPI → small text or bad scan
```

Do not make 300 DPI the universal default before measuring cost and quality.

### 28.4. Full page OCR

When necessary:

1. detect orientation;
2. render;
3. apply geometric correction if enabled;
4. OCR;
5. map tokens to canonical space;
6. reconstruct layout/regions or use detected regions.

### 28.5. OCR must not silently “clean” the output

Store:

```python
ocr_raw_text
ocr_normalized_text
ocr_confidence
```

If OCR returns `Justica` and the native text brings a reliable `Justiça`, do not replace with OCR.

### 28.6. Portuguese OCR

Create a specific corpus with:

```text
ã õ ç á à â é ê í ó ô ú
```

Plus:

```text
Nº
§
R$
1ª
2º
```

And common terms from the company's domain.

The objective is not to train a model in the MVP, but to correctly choose/configure the engine.

---

## 29. Stage 8 — Evidence Fusion

This is the layer that differentiates the product from simply chaining libraries.

### 29.1. Inputs

For a region we may have:

```text
native tokens
OCR tokens
layout bbox/type
native extracted sequence
image ink map
font metadata
quality flags
```

### 29.2. Spatial alignment

For each OCR token, search for native candidates with:

* bbox overlap;
* center distance;
* line similarity;
* similar normalized text.

### 29.3. Cases

#### Same text, same region

```text
native: "Justiça"
ocr:    "Justiça"
```

Result:

```text
"Justiça"
sources = [native, ocr]
high confidence
```

#### OCR loses accent

```text
native: "Justiça"
ocr:    "Justica"
```

If native is healthy:

```text
use "Justiça"
record divergence
```

#### Corrupted native

```text
native: "Ju�ti�a"
ocr:    "Justiça"
```

Result:

```text
use OCR
flag = recovered_from_garbled_native
```

#### OCR finds missing text

```text
native: no corresponding bbox
ocr:    "TOTAL"
```

If visual region and confidence are sufficient:

```text
add OCR token
```

#### Duplicated invisible OCR layer

```text
native A: valid visual bbox
native B: same word shifted, no ink
OCR:      confirms A
```

Result:

```text
use A
mark B as invisible_duplicate
```

### 29.4. Text similarity

Use similarity only as one piece of evidence, never alone.

A number `1.234,56` cannot be joined with `1.284,56` just because strings look similar.

For predominantly numeric tokens, require stricter matching.

### 29.5. Confidence

Avoid false mathematical rigor. The initial score can be heuristic, as long as it is explainable.

Example:

```text
+ valid native Unicode
+ visual ink present
+ OCR agrees
+ consistent bbox
- layer marked garbled
- duplicated overlap
- OCR low confidence
```

### 29.6. Unresolved conflict

If two plausible pieces of evidence disagree:

```python
TokenConflict(
    chosen="...",
    alternatives=[...],
    reason="..."
)
```

This is better than hiding uncertainty.

---

## 30. Stage 9 — Page Assembler

After regions are resolved:

* assemble final lines;
* join tokens;
* form paragraphs;
* include tables as structural blocks;
* produce page order;
* generate `raw_text` and `reading_text`.

### 30.1. Paragraphs

Signals:

* vertical distance;
* indentation;
* typographic continuity;
* final punctuation;
* line width;
* region type;
* native extracted sequence;
* hyphenation.

### 30.2. Hyphenation

Do not automatically remove `-`.

Candidate when:

```text
line ends in letter + hyphen
next line starts with a lowercase letter
same paragraph/region
```

Maintain information:

```python
HyphenJoin(
    original="implemen-\ntação",
    joined="implementação",
)
```

### 30.3. Headers and footers

In `raw_text`: preserve.

In `reading_text`: configurable policy.

Flag repetition by:

* similar position;
* same/similar text on multiple pages;
* compatible font/structure.

---

## 31. Stage 10 — Document Assembler

### 31.1. Responsibilities

* concatenate pages without losing boundaries;
* detect repeated elements;
* resolve cross-page tables;
* preserve provenance links;
* generate document outputs.

### 31.2. Page boundaries

Never lose the relationship:

```text
character → token → line → region → page
```

### 31.3. Cross-page tables

Run `CrossPageTableResolver` after all pages are structured.

### 31.4. Cross-page paragraph continuity

Can be implemented after the table, but in the MVP should be conservative.

Example:

```text
page ends without punctuation
next starts with lowercase
same column pattern
```

Can signal continuity, but does not need to irreversibly merge into the primary structure.

---

## 32. Unicode and Brazilian Portuguese

### 32.1. Priority

For digital documents, PDFium must be the first source for Unicode.

OCR enters when the native mapping is inadequate.

### 32.2. Preserve diacritics

NFC in the normalized output:

```python
unicodedata.normalize("NFC", text)
```

But `raw_text` can maintain the original sequence if needed for auditing.

### 32.3. Problematic characters

Monitor:

```text
U+FFFD
Private Use Area
glyph without Unicode
CID exposed as text
unexpected controls
```

### 32.4. Progressive recovery

Suggested order:

```text
1. native Unicode
2. alternative information from the object/text range
3. glyph/font map when available
4. region OCR
5. replacement char + diagnostics
```

Do not invent characters by natural language in the MVP.

### 32.5. Normalizations NOT to do

Do not transform:

```text
ç → c
ã → a
º → o
ª → a
```

Do not automatically reformat:

```text
CPF
CNPJ
process number
monetary values
dates
```

---

## 33. Difficult cases and expected strategy

| Case | Preferred path |
|---|---|
| Simple digital PDF | native PDFium, no OCR |
| Two-column digital PDF | structure tree when useful + layout + native extracted sequence + XY-cut on prose |
| Full scan | page OCR |
| Hybrid page | native + region OCR |
| Garbled native text | region/page OCR depending on extent |
| Duplicated OCR layer | overlap + visual ink + dedup |
| Invisible text | visual verification and OCR if necessary |
| Text as vector | path signal + OCR |
| Text in annotation appearance | annotation signal + OCR/inspection |
| Table with borders | vector grid detector |
| Two-column table with border | relaxed grid detector |
| Borderless table | text track heuristic |
| Difficult visual table | table structure model + OCR |
| Table in scan | layout + table model + OCR |
| Cross-page table | cross page table resolver |
| Rotated text | orientation group + region handling |
| Repeated header/footer | preserve raw, classify reading |

---

## 34. Recommended technology stack

### 34.1. Language

**Python 3.12+** for the MVP.

Reasons:

* implementation speed;
* OCR and document model ecosystem;
* simple integration with PaddleOCR/ONNX/PyTorch when necessary;
* ease of creating diagnostic tooling;
* integration with pypdfium2;
* rapid prototyping of heuristics using real documents.

### 34.2. PDF backend

**pypdfium2 + PDFium**.

Do not change this decision now.

### 34.3. OCR

First option to evaluate and implement:

**PaddleOCR**, pinning a version of the OCR pipeline that offers multilingual recognition with `pt`/Portuguese. The current repository documents Portuguese among the supported languages; the exact model version will be frozen after the initial corpus benchmark.

Maintain a pluggable contract to test another engine without changing the pipeline.

### 34.4. Layout

First evaluation:

* layout model from the PaddleOCR ecosystem;
* ONNX RT-DETR/PP-DocLayout alternative compatible with our requirements.

The final selection should come from the enterprise corpus, not from an isolated public benchmark.

### 34.5. Visual table

Pluggable interface.

Candidates:

* Paddle table recognition;
* SLANet/SLANeXT;
* TATR.

### 34.6. NumPy/OpenCV

Useful for:

* overlap matrices;
* ink mask;
* image analysis;
* projections;
* geometric grouping;
* coordinate transformation.

Do not use OpenCV as a requirement for every page if an equivalent operation can be done more cheaply with arrays/Pillow.

### 34.7. Rust as a later optimization

LiteParse shows the value of a native core. We should not ignore this, but we should also not start by reimplementing the project in Rust.

Clear limit:

> If profiling shows that PDFium enumeration and array transformations dominate the time, create a Rust/PyO3 extension **only for acquisition/geometric batch**.

The rest of the pipeline remains Python.

---

## 35. Suggested repository structure

```text
structured-pdf-text/
├── pyproject.toml
├── README.md
├── docs/
│   ├── architecture.md
│   ├── decisions/
│   ├── corpus-guide.md
│   └── diagnostics.md
├── src/
│   └── structured_pdf_text/
│       ├── __init__.py
│       ├── api.py
│       ├── config.py
│       ├── document.py
│       ├── geometry.py
│       │
│       ├── native/
│       │   ├── source.py
│       │   ├── pdfium_source.py
│       │   ├── characters.py
│       │   ├── objects.py
│       │   └── coordinates.py
│       │
│       ├── evidence/
│       │   ├── model.py
│       │   ├── complexity.py
│       │   ├── garbled.py
│       │   ├── duplicates.py
│       │   ├── visual_ink.py
│       │   └── decision.py
│       │
│       ├── layout/
│       │   ├── engine.py
│       │   ├── paddle.py
│       │   ├── assign.py
│       │   └── regions.py
│       │
│       ├── text/
│       │   ├── normalize.py
│       │   ├── line_detector.py
│       │   ├── word_detector.py
│       │   ├── spans.py
│       │   ├── paragraphs.py
│       │   ├── hyphenation.py
│       │   └── reading_order.py
│       │
│       ├── ocr/
│       │   ├── engine.py
│       │   ├── paddle.py
│       │   ├── render.py
│       │   └── recovery.py
│       │
│       ├── fusion/
│       │   ├── align.py
│       │   ├── token_fusion.py
│       │   ├── conflicts.py
│       │   └── confidence.py
│       │
│       ├── tables/
│       │   ├── model.py
│       │   ├── detector.py
│       │   ├── ruled.py
│       │   ├── relaxed.py
│       │   ├── text_tracks.py
│       │   ├── visual_engine.py
│       │   ├── cells.py
│       │   └── cross_page.py
│       │
│       ├── assemble/
│       │   ├── page.py
│       │   ├── document.py
│       │   └── repeated_regions.py
│       │
│       ├── renderers/
│       │   ├── text.py
│       │   ├── markdown.py
│       │   └── json.py
│       │
│       ├── diagnostics/
│       │   ├── dump.py
│       │   ├── overlay.py
│       │   ├── compare.py
│       │   └── report.py
│       │
│       └── cli.py
├── corpus/
│   └── .gitkeep
└── scripts/
    ├── benchmark.py
    ├── inspect_pdf.py
    └── compare_extractors.py
```

### 35.1. Reason for the separation

We want to be able to replace:

```text
PDFium source
layout engine
OCR engine
table structure engine
```

without altering the structural model and the renderers.

---

## 36. MVP public API

### 36.1. Simple usage

```python
from structured_pdf_text import PdfTextExtractor

extractor = PdfTextExtractor()
result = extractor.extract("documento.pdf")

print(result.reading_text)
```

### 36.2. Maximum recovery

```python
print(result.raw_text)
```

### 36.3. Structure

```python
for page in result.pages:
    for region in page.regions:
        print(region.kind, region.bbox)
```

### 36.4. Tables

```python
for table in result.tables:
    print(table.table_id)
    print(table.cells)
```

### 36.5. Diagnostics

```python
for page in result.pages:
    print(page.diagnostics.strategy)
    print(page.diagnostics.reasons)
```

### 36.6. Configuration

```python
extractor = PdfTextExtractor(
    ExtractorConfig(
        mode="balanced",
        language="pt",
        enable_ocr=True,
        enable_layout=True,
        enable_tables=True,
        merge_cross_page_tables=True,
        preserve_headers_footers=True,
    )
)
```

### 36.7. Modes

```text
native
fast
balanced
ocr
```

**`native`**: PDFium + deterministic reconstruction, no models.

**`fast`**: complexity analysis, models only when triggered.

**`balanced`**: layout on every page and selective OCR. Recommended default for the quality objective.

**`ocr`**: forces page OCR, primarily for diagnostics.

---

## 37. MVP CLI

```bash
pdftext extract documento.pdf
```

Maximum recovery output:

```bash
pdftext extract documento.pdf --output raw
```

Structured JSON:

```bash
pdftext extract documento.pdf --output json
```

Markdown:

```bash
pdftext extract documento.pdf --output markdown
```

Mode:

```bash
pdftext extract documento.pdf --mode balanced
```

Portuguese:

```bash
pdftext extract documento.pdf --language pt
```

Diagnostics:

```bash
pdftext inspect documento.pdf --page 12
```

Overlay:

```bash
pdftext overlay documento.pdf --page 12 --out page-12.png
```

Comparison:

```bash
pdftext compare documento.pdf --against pymupdf,liteparse,marker,docling
```

---

## 38. Mandatory diagnostic tools

These tools are not “nice to have”. With real PDFs, they will be the main mechanism for evolution.

### 38.1. Character dump

CSV/JSON:

```text
index
unicode
text
bbox
origin
font
size
angle
generated
mapping_failed
visible
```

### 38.2. Visual overlay

Generate image with selectable layers:

```text
native chars
native lines
layout regions
OCR tokens
tables
cells
reading order
conflicts
```

### 38.3. Evidence report

Per page:

```text
strategy
complexity reasons
native chars
OCR chars
native coverage
OCR recovered count
conflicts
layout regions
tables
processing time
```

### 38.4. Text diff

Compare:

```text
our raw_text
our reading_text
PyMuPDF text
PyMuPDF sort=True
LiteParse
Docling
MinerU
Xberg
Marker
PaddleOCR visual
```

Not required to run all in every cycle. The script must accept available adapters.

### 38.5. Table viewer

Generate simple HTML showing:

* region image;
* detected grid;
* cells;
* text per cell;
* confidence;
* cross-page fragments.

---

## 39. Revised MVP scope

### 39.1. Mandatory

The MVP should only be considered complete when it has:

| Capability | Mandatory |
|---|---|
| native PDFium text | yes |
| bbox per character | yes |
| line reconstruction | yes |
| space/word inference | yes |
| pt-BR accentuation preserved | yes |
| duplication detection | yes |
| bad text layer detection | yes |
| page rendering | yes |
| layout regions | yes |
| two columns | yes |
| selective OCR | yes |
| scan OCR | yes |
| native/OCR merge | yes |
| table with borders | yes |
| basic borderless table | yes |
| table model fallback | yes |
| cross-page table | yes |
| raw_text | yes |
| reading_text | yes |
| structured JSON | yes |
| diagnostics/overlay | yes |

### 39.2. Out of scope for MVP

Can be deferred:

* formulas to LaTeX;
* figure descriptions;
* chart understanding;
* specialized handwriting beyond what OCR already provides;
* legal semantics;
* subject classification;
* RAG/chunking;
* mandatory generic VLM;
* DOCX/PPTX;
* training own models.

### 39.3. Scope awareness

Adding visual tables and layout to the MVP increases effort compared to the previous plan. This is justified because the requirement changed: “the maximum possible text in the most varied PDFs” is not honestly met by an engine based solely on the text layer.

---

## 40. Implementation plan

The sequence below prioritizes a usable result early, but avoids building an architecture that will need to be discarded when we get to tables and OCR.

### Milestone 0 — Baseline and corpus

**Objective:** know what we are competing against.

Implement:

* repository structure;
* minimal CLI;
* local private corpus;
* directory runner;
* available comparison adapters;
* time and output logging.

Separate documents into categories, without trying to create an academic benchmark.

Suggested categories:

```text
digital-simple
digital-multicolumn
digital-bad-font
duplicate-ocr-layer
hybrid-page
scan-clean
scan-poor
table-bordered
table-borderless
table-cross-page
table-bad-format
rotated
legacy-system
```

**Output:** raw report from existing extractors on real documents.

### Milestone 1 — Native Evidence Foundation

**Objective:** extract everything PDFium knows without losing data.

Implement:

* `BBox`/coordinates;
* `NativeCharacter`;
* `NativeObjectEvidence`;
* char index;
* Unicode;
* font/size/angle;
* PDFium flags;
* images;
* minimal paths;
* minimal annotations;
* raw page dump.

**Output:** raw JSON per page + trivial native text.

### Milestone 2 — Native Text Reconstruction

**Objective:** produce useful native text without models.

Implement:

* conservative normalization;
* deduplication;
* orientation groups;
* line detector;
* space detector;
* word/token detector;
* native extracted sequence diagnostics.

**Output:** `native_reading_text` and line/token overlays.

### Milestone 3 — Complexity Analyzer

**Objective:** know when not to trust the text layer.

Implement:

* text coverage;
* full page image;
* image coverage;
* garbled Unicode;
* duplicate layer;
* visible ink verification;
* vector area signal;
* annotation text signal;
* page decision report.

**Output:** `NATIVE`, `HYBRID_CANDIDATE`, `OCR_CANDIDATE` + reasons.

### Milestone 4 — Layout Regions

**Objective:** separate prose, table, and other types before resolving reading order.

Implement:

* `LayoutEngine`;
* first engine;
* low-res rendering;
* region normalization;
* native line assignment;
* region overlay.

**Output:** segmented page.

### Milestone 5 — Robust Reading Order

**Objective:** resolve one and two columns without corrupting tables.

Implement:

* region graph;
* full-width bands;
* source-order consistency;
* XY-cut only on prose;
* rotated groups;
* basic title/footnote/caption handling.

**Output:** good `reading_text` for complex digital PDFs.

### Milestone 6 — OCR Engine and scans

**Objective:** recover pages without text.

Implement:

* `OcrEngine`;
* PaddleOCR adapter;
* Portuguese;
* page render;
* page OCR;
* coordinate transform;
* OCR overlay;
* confidence.

**Output:** scans converted to structured tokens.

### Milestone 7 — Selective OCR and Evidence Fusion

**Objective:** hybrid pages.

Implement:

* `RegionQuality`;
* crop OCR;
* page OCR promotion;
* spatial alignment;
* native/OCR dedup;
* conflict handling;
* source provenance.

**Output:** real `Mixed` extraction.

### Milestone 8 — Deterministic Table Pipeline

**Objective:** resolve common digital tables without depending on a model.

Implement:

* path line extraction;
* strict grid;
* relaxed grid;
* cell assignment;
* borderless text tracks;
* prose rejection;
* table confidence.

**Output:** `StructuredTable` for digital tables.

### Milestone 9 — Visual table fallback

**Objective:** resolve tables that do not have sufficient geometry.

Implement:

* `TableStructureEngine`;
* first visual engine;
* table crop;
* predicted structure;
* native text → cell mapping;
* OCR → cell mapping;
* merge.

**Output:** scan tables and poorly formatted tables.

### Milestone 10 — Cross Page Tables

**Objective:** join fragments.

Implement:

* `TableSignature`;
* header signatures;
* X tracks;
* boundary proximity;
* repeated header detection;
* continuation markers;
* row/col compatibility;
* page fragments;
* merge.

**Output:** a single logical table preserving source pages.

### Milestone 11 — Document Assembly and renderers

Implement:

* `StructuredDocument`;
* raw text;
* reading text;
* structured JSON;
* Markdown;
* repeated headers/footers policy;
* page boundaries.

### Milestone 12 — Performance and hardening

Measure:

* PDFium time;
* FFI calls;
* render time;
* layout time;
* OCR time;
* table time;
* merge time;
* peak memory.

Only then:

* batching;
* multiprocessing;
* model reuse;
* cache;
* Rust/PyO3 batch extraction if justified.

---

## 41. Architectural adherence matrix

The table below is not a quality benchmark. It is a qualitative architectural assessment of how well each project covers the problems our product needs to solve. Scale: 1 = weak/not the focus, 5 = very strong.

| Project | Faithful native | Adaptive diagnosis | Layout | OCR | Tables | Cross-page | Explainability | Overall fit for inspiration |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Docling | 4 | 4 | 5 | 4 | 5 | 3 | 3 | 5 |
| MinerU | 4 | 5 | 5 | 5 | 5 | 5 | 3 | 5 |
| LiteParse | 5 | 5 | 3 | 4 | 3 | 2 | 5 | 5 |
| Xberg | 5 | 5 | 4 | 4 | 5 | 3 | 5 | 5 |
| Unstructured | 3 | 4 | 4 | 3 | 3 | 2 | 4 | 3 |
| PaddleOCR | 1 | 3 | 5 | 5 | 5 | 4 | 3 | 4 |
| MarkItDown | 3 | 2 | 2 | 2 | 3 | 1 | 4 | 2 |
| Marker | 4 | 5 | 5 | 5 | 5 | 4 | 4 | 5 |
| **Our target architecture** | **5** | **5** | **5** | **5** | **5** | **5** | **5** | **5** |

The last row represents a **design objective**, not already-implemented capability.

### 41.1. Ranking by contribution to our architecture

**LiteParse**: best validation of our PDFium core + selective OCR + complexity gate.

**Marker**: best decision pattern between a good native layer, a bad block, and full OCR.

**Xberg**: best lessons for deterministic reading order and table cascade.

**MinerU**: best reference for heavy hybrid fusion and cross-page tables.

**PaddleOCR**: best candidate for the local visual/OCR layer.

**Docling**: best reference for pipeline organization and document model.

**Unstructured**: good reference for automatic strategy and fallback.

**MarkItDown**: good reference for extensibility, not for the PDF core.

---

## 42. Evaluation with real company documents

The user of this project explicitly prefers validating with real documents instead of first building a formal test suite. The MVP strategy will follow this.

### 42.1. Initial corpus

We do not need thousands of PDFs. We need diversity.

Start with approximately 50 to 100 intentionally selected documents.

Example composition:

| Category | Initial target |
|---|---:|
| simple digital | 10 |
| multiple columns | 10 |
| problematic fonts/Unicode | 10 |
| digital tables | 15 |
| cross-page tables | 10 |
| scans | 10 |
| hybrid | 10 |
| known bad cases | 15 |

A PDF can belong to multiple categories.

### 42.2. Golden cases

For each recurring problem, maintain a known page or excerpt:

```text
"this paragraph must appear complete"
"this column must come before that one"
"this table has 7 columns"
"this cell is R$ 1.234,56"
"the table continues on page 14"
"the name contains Ç/Ã/Á"
```

Does not need to start as an automated test. Can be a YAML file of human observations:

```yaml
file: processo-001.pdf
page: 12
expectations:
  - contains: "Justiça"
  - contains: "São Cristóvão"
  - table_columns: 6
  - reading_order_note: "left column before the right"
```

Later these data can become automatic regressions once the problems stabilize.

### 42.3. Extractors to compare

When they can be run locally and in accordance with their licenses:

* PyMuPDF/MuPDF as the isolated technical reference;
* LiteParse;
* Docling;
* MinerU;
* Xberg;
* Marker;
* Unstructured;
* PaddleOCR for the visual route;
* MarkItDown only as a simple reference.

### 42.4. What to observe

Do not reduce everything to a single score.

Record at least:

```text
observed text recall
invented/duplicated text
Unicode/accents
reading order
detected tables
cell structure
cross-page table
OCR need
time
memory
```

### 42.5. Practical metrics

#### Character recovery

When reference text is available:

```text
CER = Character Error Rate
```

Especially useful for OCR.

#### Normalized text coverage

For expected excerpts:

```text
found_expected_fragments / expected_fragments
```

#### Accent correctness

Count divergences in words containing diacritics.

#### Reading order

We do not initially need a complex academic metric. Mark page as:

```text
OK
MINOR
WRONG
```

#### Table

Evaluate separately:

```text
detected?
correct number of rows?
correct number of columns?
correct text in cells?
correct order?
correct cross-page merge?
```

### 42.6. Victory criterion against MuPDF

For the enterprise corpus:

* do not lose cases that MuPDF extracts correctly;
* recover a relevant portion of cases that MuPDF misses due to absence/corruption of the text layer;
* improve order in selected multi-column documents;
* extract tables more usefully when MuPDF's plain text scrambles cells;
* maintain acceptable performance on the native path.

Do not require `balanced` to be as fast as MuPDF; require that `native/fast` is competitive and that additional cost has an auditable reason.

---

## 43. Specific comparison strategy with MuPDF/PyMuPDF

### 43.1. Compare different views

PyMuPDF has more than one relevant output.

Compare:

```text
page.get_text("text")
page.get_text("text", sort=True)
page.get_text("words")
page.get_text("rawdict")
```

Our product:

```text
raw_text
reading_text
tokens/lines
structured_document
```

### 43.2. Do not copy the implementation

MuPDF/PyMuPDF remains a reference for behavior and quality, not a source of code.

Do not copy:

* constants;
* thresholds;
* code;
* literal internal structures;
* heuristics under AGPL.

Use:

* PDF specification;
* public documentation;
* observed behavior;
* enterprise corpus;
* our own decisions.

### 43.3. Cases where we can surpass

**Scans:** selective/visual OCR.

**Bad Unicode layer:** region OCR.

**Table:** own structure instead of plain text.

**Hybrid page:** merge by region.

**Order:** layout + native extracted sequence + geometry.

**Cross-page table:** document representation.

---

## 44. Performance

### 44.1. Two performance budgets

There is no single “parser time”. We will have different paths.

**Native/fast:** must remain close to the cost of PDFium + heuristics.

**Balanced:** accepts the cost of layout.

**OCR/hybrid:** accepts higher cost because there is content that would not be recovered otherwise.

### 44.2. Measure time per stage

```text
open_pdf_ms
native_extract_ms
native_reconstruct_ms
complexity_ms
render_lowres_ms
layout_ms
render_ocr_ms
ocr_ms
table_ms
fusion_ms
assemble_ms
```

### 44.3. FFI Python ↔ PDFium

Real risk of the Python design.

If each character requires several FFI transitions:

```text
N chars × 5 calls
```

a large document can accumulate overhead.

Measure first.

If necessary:

```text
Python orchestrator
       ↓
Rust/PyO3 batch native extractor
       ↓
PDFium
```

### 44.4. Rendering

Avoid rendering every page at high resolution in advance.

Ideal flow:

```text
low-res for layout/ink
high-res only for pages/regions that need OCR/visual table
```

This idea is similar to the selective behavior observed in Marker.

### 44.5. Model reuse

Models must be initialized once per worker/process.

Not:

```text
page → load model → infer → unload
```

### 44.6. Batching

Layout and OCR must accept batches when the engine supports it.

### 44.7. Parallelism

PDFium requires caution with threads and shared objects.

First scaling strategy:

* one document/process or pages in independent workers according to binding safety;
* models shared in a way compatible with the runtime;
* evaluate multiprocessing before threads for PDFium operations.

### 44.8. Initial performance target

Do not fix an artificial number before the corpus.

Record percentiles:

```text
p50 ms/page
p95 ms/page
p99 ms/page
```

separated by strategy.

---

## 45. Revised effort estimate

The new scope is materially larger than the original document.

A “PDFium + lines + words + order” implementation can be done quickly. An MVP that honestly includes selective OCR, layout, visual tables, and cross-page continuity is a different product.

Indicative range for **an experienced developer with strong coding agent support**, working iteratively with a real corpus:

| Milestone | Indicative range |
|---|---:|
| Baseline + native evidence | 1 to 2 weeks |
| Reconstruction + complexity | 2 to 3 weeks |
| Layout + reading order | 2 to 4 weeks |
| OCR + evidence fusion | 2 to 4 weeks |
| Digital table pipeline | 2 to 4 weeks |
| Visual table + cross page | 3 to 5 weeks |
| Hardening/performance | 2 to 4 weeks |

The stages overlap and corpus learning changes the speed. Therefore do not add these up mechanically as a contractual schedule.

A more realistic expectation is:

```text
Strong proof of concept:          ~4 to 6 weeks
Technically demonstrable MVP:     ~8 to 12 weeks
Robust MVP for internal pilot:    ~12 to 20 weeks
```

This is an **engineering estimate**, not a deadline commitment.

### 45.1. Where the risk lies

Not in “reading characters from PDFium”.

It is in:

```text
gate quality
OCR/native merge
borderless tables
order in hybrid layouts
table continuity
edge cases of bad PDFs
```

---

## 46. Performance versus PyMuPDF

### 46.1. Native path

It is plausible that we will initially be slower than MuPDF due to:

* Python;
* FFI;
* more diagnostics;
* more intermediate structures.

This is acceptable if the gap is controlled.

### 46.2. Balanced path

Will inevitably be slower than `page.get_text()` because it runs visual layout.

The fair comparison is quality/cost, not just speed.

### 46.3. OCR path

Orders of magnitude more expensive than native extraction. Should only be used when necessary.

### 46.4. Potential advantage

Unlike a heavy pipeline always executed, our architecture can maintain a fast path:

```text
simple digital PDF
  ↓
native evidence
  ↓
complexity = clean
  ↓
no heavy layout in fast mode
  ↓
text
```

This maintains scalability for the common case.

---

## 47. Security, privacy, and deployment

### 47.1. Principle

All standard MVP components must be runnable locally.

No enterprise PDF should be sent to an external service by default.

### 47.2. Remote engines

If in the future there is:

```python
RemoteRecoveryEngine
```

it must be opt-in and subject to explicit policy.

### 47.3. Logs

Never automatically record:

* full document text;
* CPF;
* names;
* process number;
* cell content.

Operational logs use IDs/hashes and metrics.

Full dumps only in controlled diagnostic tooling.

### 47.4. Hostile PDFs

Defenses:

* page limit;
* size limit;
* timeout;
* rendered pixel limit;
* per-page exception capture;
* optional subprocess for future isolation.

---

## 48. Licensing

This section does not replace legal review.

### 48.1. Dependencies that motivate the architecture

The intent is to avoid incorporating MuPDF/PyMuPDF in the proprietary product when the company does not want to comply with AGPL or acquire the commercial license.

### 48.2. PDFium/pypdfium2

Remain the preferred foundation due to the ecosystem's permissive license and technical capability. Verify versions and notices before distribution.

### 48.3. Researched projects

The architectural research **does not mean we will copy code** from them.

Current observations:

* Docling: MIT on the code;
* LiteParse: Apache 2.0;
* Xberg: MIT;
* Unstructured: Apache 2.0;
* PaddleOCR: Apache 2.0 for the code, with separate verification of chosen weights;
* MarkItDown: MIT;
* Marker: Apache 2.0 code; weights have their own terms and need separate analysis;
* MinerU: current license based on Apache 2.0 with additional terms for commercial use and attribution for online services.

### 48.4. Project rule

Before adding a model:

```text
1. record name/version
2. record URL
3. record code license
4. record weights license
5. record relevant dependencies
6. approval for enterprise use
```

### 48.5. Conceptual clean room

When learning from permissive or copyleft projects, document the **architectural idea**, not porting literal heuristics without license review.

---

## 49. Main technical risks

### 49.1. PDFium not exposing all needed evidence

Some PDF information may not be available through the public text API in the desired way.

Mitigation:

* use page object APIs when available;
* use rendering as a visual sensor;
* keep `NativeEvidenceSource` replaceable;
* do not couple the algorithm directly to pypdfium2.

### 49.2. OCR false positive

An aggressive gate may send good pages to OCR and worsen exact text.

Mitigation:

* start conservative;
* selective OCR;
* keep native as competing evidence;
* compare divergence;
* measure by category.

### 49.3. Layout model misclassifying table/prose

If a table is classified as text, reading order may corrupt data.

Mitigation:

* deterministic grid/track signals can promote a region to table;
* table detector also runs on geometric indicators;
* never depend on a single class from the model.

### 49.4. Two columns being confused with a table

Risk already observed in market architectures.

Mitigation:

* `ProseVsTableClassifier`;
* line height/length;
* track recurrence;
* numeric density;
* layout signal;
* isolation of the TablePipeline.

### 49.5. Borderless table becoming prose

Mitigation:

* require recurrence across multiple lines;
* column coherence;
* simple semantics of numeric tracks;
* model fallback only when layout also indicates table.

### 49.6. Cross-page table being joined erroneously

Mitigation:

* never use only page proximity;
* require multiple structural pieces of evidence;
* preserve fragments even when merge occurs;
* confidence and diagnostics.

### 49.7. OCR getting numeric values wrong

Critical in enterprise documents.

Mitigation:

* prefer native when valid;
* stricter merge rules for numbers;
* keep the conflict;
* use higher-resolution OCR in tables when necessary.

### 49.8. Python becoming a bottleneck

Mitigation:

* measure per stage;
* vectorize geometry;
* avoid excessive Python objects in the hot path when necessary;
* PyO3/Rust only after profiling.

### 49.9. Excessive scope growth

Complete Document AI is a much larger problem than text extraction.

Mitigation:

The MVP will not do:

```text
image captioning
chart understanding
general VLM reasoning
semantic document QA
RAG
entity extraction
```

---

## 50. Observability

### 50.1. Every page records a decision

Example:

```json
{
  "page": 17,
  "strategy": "mixed",
  "reasons": ["embedded_images", "garbled_unicode"],
  "layout": true,
  "ocr_regions": 2,
  "page_ocr": false,
  "tables": 1,
  "native_tokens": 684,
  "ocr_tokens_added": 37,
  "conflicts": 3
}
```

### 50.2. Aggregated metrics

```text
native_pages
mixed_pages
ocr_pages
layout_pages
ocr_regions
tables_native
tables_visual
cross_page_tables
unicode_failures
conflicts
```

### 50.3. Reason for each fallback

Do not accept generic log:

```text
fallback to OCR
```

Prefer:

```text
page=17 region=table-2 OCR_REGION reason=native_text_missing visual_ink=0.42
```

---

## 51. MVP exit criteria

The MVP can be presented as ready for internal pilot when:

1. extracting common digital PDFs without OCR;
2. preserving accents and Unicode from the selected documents;
3. resolving two-column corpus pages without interleaving lines;
4. detecting when the text layer is absent or clearly bad;
5. recovering Portuguese scans with useful quality;
6. recovering visual regions without replacing good native text on the rest of the page;
7. extracting tables with borders in a cell structure;
8. extracting a significant portion of borderless tables representative of the corpus;
9. using visual fallback when the deterministic table fails;
10. joining the main real examples of cross-page continued tables;
11. producing `raw_text`, `reading_text`, and structured JSON;
12. explaining through diagnostics which strategies were used;
13. having an overlay that allows investigation of a problematic page;
14. being executable entirely in the company's environment;
15. not depending on MuPDF/PyMuPDF at runtime.

### 51.1. Comparative gate

On the acceptance corpus:

```text
No systematic severe regression against MuPDF on simple digital PDFs.

Demonstrable improvement in cases that require layout, OCR, or structured tables.

Remaining errors classifiable by category and visible in diagnostics.
```

---

## 52. First recommended increment

The first delivery must deliberately **not** start with the layout model.

Implement:

```text
PDFium NativeEvidenceSource
        ↓
NativeCharacter[]
        ↓
page JSON dump
        ↓
line reconstruction
        ↓
native reading text
        ↓
visual overlay
```

Choose five PDFs:

```text
1 simple
1 two columns
1 table
1 bad Unicode
1 scan
```

For the scan, an empty result in this first increment is expected. It serves to validate the `NO_TEXT/SCANNED` signal later.

### 52.1. Completion criterion

We can look at a page and answer exactly:

```text
which chars did PDFium see?
what Unicode?
where is each char?
what was its order?
how did our LineDetector group them?
```

---

## 53. Second recommended increment

Add `ComplexityAnalyzer` before OCR.

Work with real cases:

```text
scan
duplicated layer
bad Unicode layer
image with text
```

Implement only deterministic signals.

Expected result:

```text
page 1 clean
page 2 scanned
page 3 garbled
page 4 embedded_images
```

Do not perform OCR yet.

---

## 54. Third recommended increment

Add low-res layout and separate regions.

Objective:

```text
prose ≠ table
```

Use two-column and table PDFs first.

Only then enable XY-cut/reading order per region.

---

## 55. Fourth recommended increment

Add PaddleOCR for full scans.

Do not implement complex merge first.

Objective:

```text
scan → OCR tokens → reading text
```

Validate Portuguese and coordinates.

---

## 56. Fifth recommended increment

OCR by region + evidence fusion.

Use real hybrid pages.

Validate:

```text
good native text remains identical
absent visual text is added
no duplicates appear
```

---

## 57. Sixth recommended increment

Digital table.

Start with explicit borders.

Then:

```text
relaxed borders
borderless tracks
```

Avoid the table model while we still cannot inspect the deterministic grid well.

---

## 58. Seventh recommended increment

Table structure model + OCR per cell/region.

Only now add the heavy visual part of tables.

---

## 59. Eighth recommended increment

Cross page table resolver.

Start with the company's real examples, because continuity heuristics are highly dependent on the document type.

Build a generic rule from signals, not from fixed document names.

---

## 60. Decisions we should not make early

### 60.1. “Every complex PDF goes to OCR”

Wrong. Can lose exact text and increase cost.

### 60.2. “Layout model always defines order”

Wrong. Native extracted sequence can be superior in many digital PDFs.

### 60.3. “XY-cut resolves tables and columns”

Wrong. There is practical evidence of table corruption when the same mechanism tries to resolve both.

### 60.4. “PaddleOCR will be our architecture”

Wrong. It is an engine within the architecture.

### 60.5. “Markdown is the product”

Wrong. Markdown loses structural and provenance information.

### 60.6. “A single quality score is enough”

Wrong. We need the reasons.

### 60.7. “If OCR partially agrees, it is correct”

Wrong, especially for numbers.

### 60.8. “Header/footer should be removed”

Not from the evidence. At most from a reading rendering.

### 60.9. “Implement everything in Rust now”

Premature. Python + native engines provides development speed and performance sufficient to measure the real problem.

---

## 61. Roadmap after the MVP

### Phase 1 — Quality

* refine the complexity classifier;
* better region quality;
* better rules for legal/administrative documents without domain coupling;
* optional second OCR engine for critical divergences.

### Phase 2 — Performance

* batch native extraction;
* PyO3/Rust if necessary;
* model batching;
* cache;
* persistent workers.

### Phase 3 — Structure

* lists;
* headings;
* forms/key-value;
* footnotes;
* better paragraph continuation.

### Phase 4 — Optional multimodal

* formulas;
* charts;
* image text/description;
* VLM only for unresolved regions.

---

## 62. Final recommendation for presentation to the company

The proposal must not be presented as “we will reimplement PyMuPDF”.

The correct formulation is:

> **Build our own robust PDF extraction engine, with a permissively licensed backend, that preserves exact digital text when it exists and uses layout and OCR selectively to recover content that traditional parsers miss.**

The initial choice of PDFium remains valid and was reinforced by the study of current parsers. What changes is the layer above it.

The final MVP architecture is deliberately hybrid:

```text
PDFium                  → native truth when reliable
Layout detector         → understands regions
Quality gate            → decides where to trust
OCR                     → recovers what is not natively available
Table pipeline          → avoids treating tables as prose
Evidence fusion         → chooses without erasing alternatives
Document assembler      → resolves cross-page relationships
```

The differentiator will not be having “one more text heuristic”. The differentiator will be **orchestrating multiple pieces of evidence without sacrificing the correct content that was already in the PDF**.

This also creates a safe incremental path:

```text
first we are good at native text
        ↓
we add diagnostics
        ↓
we add layout
        ↓
we add selective OCR
        ↓
we add tables
        ↓
we add cross-page continuity
```

Each stage improves coverage without requiring the previous core to be replaced.

---

## 63. Technical sources consulted in this revision

### Docling

* Repository: <https://github.com/docling-project/docling>
* `docling/pipeline/standard_pdf_pipeline.py`
* `docling/backend/pypdfium2_backend.py`
* pipeline options documentation: <https://docling-project.github.io/docling/reference/pipeline_options/>
* commit analyzed on GitHub during this revision: `5ea6490ffdc57b2fd7de5cc436f2d0a22f2214d4`

### MinerU

* Repository: <https://github.com/opendatalab/MinerU>
* `mineru/backend/hybrid/hybrid_analyze.py`
* `mineru/backend/utils/runtime_utils.py`
* `mineru/utils/table_merge.py`
* current license: `LICENSE.md`
* commit analyzed: `4fe4bde114a23ee5dd637eae99b767f4669bf58c`

### LiteParse

* Repository: <https://github.com/run-llama/liteparse>
* `crates/liteparse/src/ocr_merge.rs`
* `crates/liteparse/src/projection.rs`
* `crates/liteparse/src/extract.rs`
* commit analyzed: `c999b5302ac903e8bed5ce047ac4a0122cd869b1`

### Xberg

* Repository: <https://github.com/xberg-io/xberg>
* `crates/xberg/src/extractors/pdf/mod.rs`
* `crates/xberg-native-pdf/src/pipeline/reading_order/xycut.rs`
* `crates/xberg/src/pdf/native/table.rs`
* layout documentation: <https://docs.xberg.io/guides/layout-detection/>
* commit analyzed: `606fa72230058da9b9d0a16e5c999b38b92dc847`

### Unstructured

* Repository: <https://github.com/Unstructured-IO/unstructured>
* `unstructured/partition/pdf.py`
* `LICENSE.md`
* commit analyzed: `ee2b3a350d314a2a4e0cb7dfe6e34a1d92fd426d`

### PaddleOCR

* Repository: <https://github.com/PaddlePaddle/PaddleOCR>
* PP-StructureV3 documentation/source
* Table Recognition v2 documentation
* multilingual recognition documentation
* commit analyzed: `2661c7c0ef5c613e8f93c6e93b2e052399f0f854`

### MarkItDown

* Repository: <https://github.com/microsoft/markitdown>
* `packages/markitdown/src/markitdown/converters/_pdf_converter.py`
* `LICENSE`
* commit analyzed: `5640da7142fb546da0ef712093f3e29d87c62e0b`

### Marker

* Repository: <https://github.com/datalab-to/marker>
* `marker/converters/pdf.py`
* `marker/builders/document.py`
* `marker/builders/line.py`
* project README and documentation for `fast`/`balanced` modes
* commit analyzed: `f6b072ad46a79026ee75d1777c4e3e798a2712a5`

### Previous references

* PyMuPDF: <https://github.com/pymupdf/PyMuPDF>
* MuPDF: <https://github.com/ArtifexSoftware/mupdf>
* pypdfium2: <https://github.com/pypdfium2-team/pypdfium2>
* PDFium public text API: <https://pdfium.googlesource.com/pdfium/>
* PDFium structure tree API: `public/fpdf_structtree.h`

---

## 64. Architectural decision record

**Decision:** replace the linear `PDFium → reconstruction → OCR fallback` architecture with a **Hybrid Evidence Fusion Pipeline**.

**Kept:** PDFium/pypdfium2 as the primary backend, Python for the MVP, own reconstruction, independent internal structure.

**Added to the MVP:**

* early complexity gate;
* layout regions;
* per-region quality gate;
* selective OCR;
* explicit native/OCR fusion;
* separate table pipeline;
* table model fallback;
* cross-page tables;
* per-evidence provenance and conflict.

**Removed as a premise:**

* XY-cut over the entire page;
* OCR only as the final per-page fallback;
* table as a post-MVP concern;
* `QualityAnalyzer` only after full reconstruction.

**Justification:** the set of most robust current architectures converges on adaptive and region-aware pipelines. The code analysis also showed concrete risks of applying the same spatial heuristic to both prose and tables. The revised architecture maximizes recovery capability without making OCR or a visual model the primary source for correct digital PDFs.

---

**End of document.**
