"""The adapter over the `aireadiness_evidence` grader.

Scoring itself belongs to that package; what is tested here is the reshaping,
and in particular that a criterion the rubric leaves to a human reviewer is
never presented as a zero.
"""

from __future__ import annotations

import pytest

from fairscape_artifacts import grading


def test_scores_are_read_from_strings():
    """The grader writes scores as strings, so an int check reads them all as
    missing and silently blanks the whole review."""
    assert grading._as_score({"score": "2"}) == 2
    assert grading._as_score({"score": "0"}) == 0
    assert grading._as_score({"score": 1}) == 1


@pytest.mark.parametrize("estimate", [None, {}, {"score": None}, {"score": ""},
                                      {"score": "n/a"}])
def test_missing_scores_are_none_not_zero(estimate):
    assert grading._as_score(estimate) is None


def _presentation():
    return {
        "rubric": "Rubric …, v1.5 (2026-08-29)",
        "rubric_version": "1.5",
        "generated": "2026-08-31 00:00 UTC",
        "network_checks": False,
        "sections": [
            {"number": 0, "title": "FAIRness", "gating": True, "criteria": [
                {"id": "0.a", "name": "Findable", "gating": True,
                 "estimate": {"score": "2", "basis": ["PID present"]},
                 "evidence": [1, 2, 3]},
                {"id": "0.b", "name": "Accessible", "gating": True,
                 "estimate": None, "evidence": [1]},
            ]},
            {"number": 1, "title": "Provenance", "gating": True, "criteria": [
                {"id": "1.a", "name": "Transparent",
                 "estimate": {"score": "0", "basis": []}, "evidence": []},
            ]},
        ],
    }


def test_summarize_counts_criteria_not_points():
    summary = grading.summarize(_presentation())
    assert summary["criteria_total"] == 3
    assert summary["estimated"] == 2
    assert summary["tally"] == {"Substantive": 1, "Partial": 0, "Absent": 1,
                                grading.AWAITING: 1}


def test_awaiting_review_is_labelled_not_scored():
    summary = grading.summarize(_presentation())
    accessible = summary["sections"][0]["criteria"][1]
    assert accessible["score"] is None
    assert accessible["label"] == grading.AWAITING


def test_summarize_keeps_basis_and_gating():
    summary = grading.summarize(_presentation())
    findable = summary["sections"][0]["criteria"][0]
    assert findable["basis"] == ["PID present"]
    assert findable["evidence_count"] == 3
    assert summary["sections"][0]["gating"] is True


def test_summarize_of_nothing_is_nothing():
    assert grading.summarize(None) is None


def test_no_total_score_is_invented():
    """The rubric produces no single number and neither does this package."""
    summary = grading.summarize(_presentation())
    assert "percentage" not in summary
    assert "total" not in summary
    assert "possible" not in summary
