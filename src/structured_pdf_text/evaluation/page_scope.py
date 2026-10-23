"""Page-set selection and classification (spec §3.1).

This module implements the canonical page-scope computation so that all
metric groups use the *same* selected set without ad-hoc intersections.

Usage
-----
    scope = build_page_scope(
        ref_page_nums=set(ref_pages.keys()),
        hyp_page_nums=set(hyp_pages.keys()),
        run_manifest_pages=run_manifest_pages,  # None → use ref pages
    )
    # scope.selected_missing → pages to penalise as absent
    # scope.selected_present → pages to evaluate normally
"""
from __future__ import annotations

from structured_pdf_text.evaluation.models import PageScope


def build_page_scope(
    ref_page_nums: set[int],
    hyp_page_nums: set[int],
    run_manifest_pages: set[int] | None = None,
) -> PageScope:
    """Classify pages into the canonical scope groups (spec §3.1).

    Parameters
    ----------
    ref_page_nums       : Set of page numbers present in the reference.
    hyp_page_nums       : Set of page numbers present in the hypothesis.
    run_manifest_pages  : Pages the engine was asked to process, from the
                          run manifest.  When None, the reference page set
                          is used as the selected set (conservative fallback).

    Returns
    -------
    PageScope with six disjoint or overlapping groups as defined in spec §3.1.
    """
    selected: set[int] = (
        run_manifest_pages if run_manifest_pages is not None else ref_page_nums
    )

    # Pages that are both selected and have a reference entry
    selected_reference: set[int] = selected & ref_page_nums

    # Of those, which are also present in the hypothesis
    selected_present: set[int] = selected_reference & hyp_page_nums

    # Selected+reference pages that are absent from the hypothesis (penalise)
    selected_missing: set[int] = selected_reference - hyp_page_nums

    # Hypothesis pages that were not part of the selected scope (out-of-scope)
    selected_extra: set[int] = hyp_page_nums - selected

    # Selected pages with no reference counterpart (config error / N/A)
    selected_without_ref: set[int] = selected - ref_page_nums

    return PageScope(
        selected             = selected,
        selected_reference   = selected_reference,
        selected_present     = selected_present,
        selected_missing     = selected_missing,
        selected_extra       = selected_extra,
        selected_without_ref = selected_without_ref,
    )
