"""AI-Ready review, via the grader in `fairscape-grader`.

Scoring is not reimplemented here. `aireadiness_evidence` (the
`fairscape-wizard` distribution) owns the rubric — "Rubric for Human Review
of AI-readiness Evaluation Criteria" v1.5 — and produces the presentation
document: per-criterion evidence plus a *mechanical estimate* where the
scoring rules apply unambiguously, and `None` where the rubric asks for human
judgment. This module only calls it and reshapes the result for the datasheet.

That reshaping is deliberately conservative. The rubric produces no single
number and this package does not invent one: a criterion the rubric leaves to
a reviewer is shown as awaiting review, never as a zero, and the summary
counts are counts of criteria, not a score out of anything.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

#: Score vocabulary, matching the rubric's `scoring` keys.
SCORE_LABELS = {0: "Absent", 1: "Partial", 2: "Substantive"}

AWAITING = "Human review"


class GraderUnavailable(RuntimeError):
    """`aireadiness_evidence` is not importable."""


def _import_grader():
    try:
        from aireadiness_evidence import build_presentation, render_review
    except ImportError as err:  # pragma: no cover - depends on the environment
        raise GraderUnavailable(
            "the AI-Ready grader is not installed; install the "
            "'fairscape-wizard' distribution (fairscape-grader repo) to score "
            "crates"
        ) from err
    return build_presentation, render_review


def review(crate_dir: str, *, network: bool = False,
           progress: Optional[Callable[[str], None]] = None) -> Dict[str, Any]:
    """Build the presentation document for a crate directory.

    `network` defaults to off: these artifacts are meant to be generatable
    offline, and the grader's network checks resolve URLs and registries.
    Pass `network=True` for a fuller review when connectivity is available.
    """
    build_presentation, _ = _import_grader()
    return build_presentation(crate_dir, network=network, progress=progress)


def review_html(presentation: Dict[str, Any], link_base: str = "") -> str:
    """The grader's own human-review page for a presentation document."""
    _, render = _import_grader()
    return render(presentation, link_base=link_base)


def _as_score(estimate: Optional[Dict[str, Any]]) -> Optional[int]:
    """The estimate's score as an int, or None when awaiting human review.

    The grader writes scores as strings ("2"/"1"/"0"), so a plain truthiness
    or `isinstance(..., int)` check silently reads every estimate as missing.
    """
    if not estimate:
        return None
    raw = estimate.get("score")
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def summarize(presentation: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Reshape a presentation document for the datasheet's review section."""
    if not presentation:
        return None

    sections: List[Dict[str, Any]] = []
    tally = {"Substantive": 0, "Partial": 0, "Absent": 0, AWAITING: 0}

    for section in presentation.get("sections", []):
        criteria = []
        section_tally = {"Substantive": 0, "Partial": 0, "Absent": 0, AWAITING: 0}
        for criterion in section.get("criteria", []):
            score = _as_score(criterion.get("estimate"))
            label = SCORE_LABELS.get(score, AWAITING) if score is not None else AWAITING
            tally[label] += 1
            section_tally[label] += 1
            criteria.append({
                "id": criterion.get("id", ""),
                "name": criterion.get("name", ""),
                "gating": bool(criterion.get("gating")),
                "score": score,
                "label": label,
                "basis": list((criterion.get("estimate") or {}).get("basis") or []),
                "evidence_count": len(criterion.get("evidence") or []),
                "error": criterion.get("error"),
            })
        if criteria:
            sections.append({
                "number": section.get("number"),
                "title": section.get("title", ""),
                "gating": bool(section.get("gating")),
                "criteria": criteria,
                "tally": section_tally,
                "total": len(criteria),
                "estimated": len(criteria) - section_tally[AWAITING],
            })

    total = sum(tally.values())
    return {
        "rubric": presentation.get("rubric", ""),
        "rubric_version": presentation.get("rubric_version", ""),
        "generated": presentation.get("generated", ""),
        "network_checks": bool(presentation.get("network_checks")),
        "sections": sections,
        "tally": tally,
        "criteria_total": total,
        "estimated": total - tally[AWAITING],
    }
