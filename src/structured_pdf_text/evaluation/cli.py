"""Shared command line for the canonical evaluator; no extraction or OCR.

``scripts/compute_metrics.py`` and ``scripts/regrade_existing.py`` differ only
in defaults (output directory, run ID, primary filenames). Both evaluate once,
then publish the canonical metrics and error report under one journal.
"""
from __future__ import annotations

import argparse
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path

from .artifacts import (
    Artifact, ArtifactPublicationError, artifact_namespace, json_text,
    publication_identity, publish_artifacts, reserved_filenames,
)
from .evaluator import EvaluationRequest, evaluate_document
from .reference_inputs import load_reference_text, load_run_metadata
from .report_renderer import render_error_report


@dataclass(frozen=True)
class CliProfile:
    """Defaults of one entry point. Filename templates take engine/slug/run_id."""

    prog: str
    description: str
    default_output_dir: Path
    hypothesis_stem_run_id: bool
    metrics_name: str
    metrics_fallback: str | None
    errors_name: str
    errors_fallback: str | None


COMPUTE_PROFILE = CliProfile(
    prog="compute_metrics.py",
    description="Compute canonical evaluation-3.0.0 metrics for extracted Markdown.",
    default_output_dir=Path("output/fase8"),
    hypothesis_stem_run_id=True,
    metrics_name="metrics_v3_{slug}_{run_id}.json", metrics_fallback=None,
    errors_name="errors_v3_{slug}_{run_id}.md", errors_fallback=None,
)
REGRADE_PROFILE = CliProfile(
    prog="regrade_existing.py",
    description="Regrade hypothesis Markdown with canonical metrics (no OCR invoked).",
    default_output_dir=Path("output/evaluation_v2"),
    hypothesis_stem_run_id=False,
    metrics_name="metrics_{engine}_{run_id}.json",
    metrics_fallback="metrics_v3_{slug}_{run_id}.json",
    errors_name="errors_{engine}_{run_id}.md",
    errors_fallback="errors_v3_{slug}_{run_id}.md",
)


def _parser(profile: CliProfile) -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog=profile.prog, description=profile.description)
    ap.add_argument("--hypothesis", type=Path, required=True, help="Extracted Markdown file to evaluate")
    ap.add_argument("--reference", type=Path, default=None, help="Reference ground-truth Markdown file")
    ap.add_argument("--manifesto", type=Path, default=None,
                    help="Corpus manifesto JSON (ground truth when --reference is absent; otherwise labels)")
    ap.add_argument("--run-manifest", type=Path, default=None,
                    help="E2E run manifest carrying PDF hash, mode, selected pages and run outcomes")
    ap.add_argument("--engine", default="unknown", help="Engine/configuration name")
    ap.add_argument("--run-id", default=None, help="Run identifier")
    ap.add_argument("--output-dir", type=Path, default=profile.default_output_dir)
    ap.add_argument("--quiet", action="store_true")
    return ap


def main(profile: CliProfile, argv: list[str] | None = None) -> int:
    args = _parser(profile).parse_args(argv)
    if not args.hypothesis.is_file():
        print(f"ERROR: hypothesis not found: {args.hypothesis}", file=sys.stderr)
        return 1
    if args.reference is None and args.manifesto is None:
        print("ERROR: provide --reference or --manifesto", file=sys.stderr)
        return 1
    run_id = args.run_id
    if run_id is None:
        run_id = args.hypothesis.stem if profile.hypothesis_stem_run_id else uuid.uuid4().hex[:12]
    try:
        namespace = artifact_namespace(args.engine, run_id)
    except ArtifactPublicationError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    # Evaluate before creating any file; failures never leave partial output.
    try:
        run_metadata = load_run_metadata(args.run_manifest)
        hyp_text = args.hypothesis.read_text(encoding="utf-8")
        ref_text = load_reference_text(args.reference, args.manifesto)
        doc = evaluate_document(EvaluationRequest(
            ref_text=ref_text, hyp_text=hyp_text, ref_path=args.reference,
            hyp_path=args.hypothesis, manifesto_path=args.manifesto,
            engine=args.engine, run_id=run_id, run_metadata=run_metadata,
        ))
    except Exception as exc:
        print(f"ERROR: canonical evaluation failed: {exc}", file=sys.stderr)
        return 1

    result = doc.to_dict()
    names = {"engine": args.engine, "slug": args.engine.replace("-", "_"), "run_id": run_id}

    def name(template: str | None) -> str | None:
        return template.format(**names) if template is not None else None

    report = render_error_report(result, run_id=run_id, engine=args.engine,
                                 ref_path=args.reference, hyp_path=args.hypothesis)
    try:
        published = publish_artifacts(
            args.output_dir, namespace,
            publication_identity(result, ref_text=ref_text, hyp_text=hyp_text, run_metadata=run_metadata),
            [Artifact("canonical_metrics", name(profile.metrics_name), json_text(result),
                      name(profile.metrics_fallback)),
             Artifact("canonical_errors", name(profile.errors_name), report,
                      name(profile.errors_fallback))],
            reserved_names=reserved_filenames(args.engine, run_id),
        )
    except (ArtifactPublicationError, OSError, ValueError, TypeError) as exc:
        print(f"ERROR: artifact publication failed: {exc}", file=sys.stderr)
        return 1

    if not args.quiet:
        evaluation = result["evaluation"]
        print(f"Metrics:      {published['canonical_metrics']}")
        print(f"Error report: {published['canonical_errors']}")
        print(f"schema_version: {result['schema_version']}")
        print(f"status:         {evaluation['status']}")
        print(f"rank_eligible:  {evaluation['rank_eligible']}")
        print(f"applicable metrics: {evaluation['applicable_metric_count']}")
        if evaluation["rank_ineligibility_reasons"]:
            print(f"ineligibility reasons: {evaluation['rank_ineligibility_reasons']}")
    return 0
