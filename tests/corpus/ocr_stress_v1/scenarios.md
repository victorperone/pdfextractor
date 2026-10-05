# Document_OCR_Stress_V1 — scenarios

Deterministic synthetic corpus of 60 pages. All names, codes, values, and images are fictitious.
The manifest is the executable reference; this table is only a map for human inspection.

| Page | Block | Scenario | Inspection focus |
|---:|:---:|---|---|
| 1 | A | digital control text | native text — basic OCR |
| 2 | A | simple legible raster | OCR/raster/mixed — basic OCR |
| 3 | A | raster at moderate resolution | OCR/raster/mixed — basic OCR |
| 4 | A | low-resolution raster | OCR/raster/mixed — basic OCR |
| 5 | A | raster with small font | OCR/raster/mixed — basic OCR |
| 6 | A | raster with accents and Unicode | OCR/raster/mixed — basic OCR |
| 7 | A | raster with values and dates | OCR/raster/mixed — basic OCR |
| 8 | A | raster with reduced contrast | OCR/raster/mixed — basic OCR |
| 9 | A | raster with light noise and compression | OCR/raster/mixed — basic OCR |
| 10 | A | raster with slight skew | OCR/raster/mixed — basic OCR |
| 11 | A | text over texture and stamp | OCR/raster/mixed — basic OCR |
| 12 | A | raster with multiple blocks | OCR/raster/mixed — basic OCR |
| 13 | B | digital text and raster receipt | OCR/raster/mixed — mixed pages |
| 14 | B | digital text above and below raster | OCR/raster/mixed — mixed pages |
| 15 | B | continuous raster figure — part 1 | OCR/raster/mixed — mixed pages |
| 16 | B | continuous raster figure — part 2 | OCR/raster/mixed — mixed pages |
| 17 | B | two distant raster images | OCR/raster/mixed — mixed pages |
| 18 | B | raster over decorative element | OCR/raster/mixed — mixed pages |
| 19 | B | decorative image without text | native text — mixed pages |
| 20 | B | raster receipt and similar text | OCR/raster/mixed — mixed pages |
| 21 | B | raster marginal note | OCR/raster/mixed — mixed pages |
| 22 | B | raster stamp and signature | OCR/raster/mixed — mixed pages |
| 23 | B | two columns and nearby image | OCR/raster/mixed — mixed pages |
| 24 | B | textual raster and graphic without text | OCR/raster/mixed — mixed pages |
| 25 | C | digital table with grid | native text — tables |
| 26 | C | digital table without borders | native text — tables |
| 27 | C | financial raster table | OCR/raster/mixed — tables |
| 28 | C | raster table without borders | OCR/raster/mixed — tables |
| 29 | C | raster table with small font | OCR/raster/mixed — tables |
| 30 | C | raster table with multiline cells | OCR/raster/mixed — tables |
| 31 | C | digital table with merged cells | native text — tables |
| 32 | C | raster table with merged cells | OCR/raster/mixed — tables |
| 33 | C | digital table and image in cell | native text — tables |
| 34 | C | digital table followed by raster | OCR/raster/mixed — tables |
| 35 | C | raster table with many columns | OCR/raster/mixed — tables |
| 36 | C | continued digital table — part 1 | native text — tables |
| 37 | C | continued digital table — part 2 | native text — tables |
| 38 | C | continued raster table — part 1 | OCR/raster/mixed — tables |
| 39 | C | continued raster table — part 2 | OCR/raster/mixed — tables |
| 40 | C | landscape raster table | OCR/raster/mixed — tables |
| 41 | D | digital text in landscape | native text — orientation, layout, and figures |
| 42 | D | horizontal raster in landscape | OCR/raster/mixed — orientation, layout, and figures |
| 43 | D | text object rotated 90 degrees | native text — orientation, layout, and figures |
| 44 | D | textual image rotated within the page | OCR/raster/mixed — orientation, layout, and figures |
| 45 | D | three columns and side note | native text — orientation, layout, and figures |
| 46 | D | valid QR and separate label | OCR/raster/mixed — orientation, layout, and figures |
| 47 | D | valid barcode and label | OCR/raster/mixed — orientation, layout, and figures |
| 48 | D | multiple images, few with text | OCR/raster/mixed — orientation, layout, and figures |
| 49 | D | textual image partially outside the page | OCR/raster/mixed — orientation, layout, and figures |
| 50 | D | figure entirely outside the visible area | OCR/raster/mixed — orientation, layout, and figures |
| 51 | E | text, raster receipt, and table | OCR/raster/mixed — integration |
| 52 | E | text, raster table, and decorative figure | OCR/raster/mixed — integration |
| 53 | E | digital-only page between OCR pages | native text — integration |
| 54 | E | multiple raster regions and decoration | OCR/raster/mixed — integration |
| 55 | E | mixed landscape document | OCR/raster/mixed — integration |
| 56 | E | continued digital table with OCR | OCR/raster/mixed — integration |
| 57 | E | continuation with header and raster cell | OCR/raster/mixed — integration |
| 58 | E | QR, barcodes, text, and image | OCR/raster/mixed — integration |
| 59 | E | multiple figures and partial raster | OCR/raster/mixed — integration |
| 60 | E | dense integration | OCR/raster/mixed — integration |

Required continuations: 15–16, 36–37, 38–39, and 56–57.
Pages 46, 47, and 58 use ReportLab QR/Code128 when available; the manifest records `valid_generated`.
Raster pages do not receive selectable text in the final PDF; the text exists only in the image pixels.
