"""Rendering: self-containment, escaping, and safe script embedding.

These pages get mailed around and dropped into Zenodo deposits, so they must
work with nothing beside them, and they carry crate-authored text, so that
text must not be able to inject markup.
"""

from __future__ import annotations

import json
import re

import pytest

from fairscape_artifacts import datasheet, evidence
from conftest import repo_path

from fairscape_artifacts import render
from fairscape_artifacts.crate import Crate


@pytest.fixture(scope="module")
def small_crate():
    return Crate.load(repo_path("wizards", "example-wf", "ro-crate-metadata.json"))


# -- safe script embedding -------------------------------------------------

def test_json_for_script_cannot_close_the_script_element():
    payload = render.json_for_script({"name": "</script><img src=x onerror=alert(1)>"})
    assert "</script" not in payload
    assert "<" not in payload and ">" not in payload
    assert json.loads(payload)["name"] == "</script><img src=x onerror=alert(1)>"


def test_json_for_script_escapes_line_separators():
    """U+2028/U+2029 are valid in JSON strings but terminate a JS line."""
    payload = render.json_for_script({"x": "a b c"})
    assert " " not in payload and " " not in payload
    assert json.loads(payload)["x"] == "a b c"


def test_graph_json_round_trips_through_the_page(small_crate):
    graph = evidence.build(small_crate)
    html = render.evidence_graph_html(graph, generated_at="now")
    embedded = re.search(r"window\.__EVIDENCE_GRAPH__ = (.*?);</script>", html, re.S)
    assert json.loads(embedded.group(1)) == graph


# -- escaping ---------------------------------------------------------------

def test_crate_text_is_escaped():
    crate = Crate({"@graph": [{
        "@id": "./",
        "@type": ["Dataset", "https://w3id.org/EVI#ROCrate"],
        "name": "<script>alert('x')</script>",
        "description": "a & b < c",
    }]}, path=None)
    html = render.datasheet_html(datasheet.build_context(crate))
    assert "<script>alert('x')</script>" not in html
    assert "&lt;script&gt;" in html
    assert "a &amp; b &lt; c" in html


# -- self-containment -------------------------------------------------------

@pytest.mark.parametrize("kind", ["datasheet", "evidence-graph"])
def test_pages_reference_nothing_external(small_crate, kind):
    if kind == "datasheet":
        html = render.datasheet_html(datasheet.build_context(small_crate))
    else:
        html = render.evidence_graph_html(evidence.build(small_crate),
                                          generated_at="now")
    assert "<link" not in html
    assert not re.search(r"<script[^>]+\bsrc=", html)
    assert not re.search(r'url\(\s*["\']?https?://', html)


def test_evidence_graph_page_carries_its_viewer(small_crate):
    html = render.evidence_graph_html(evidence.build(small_crate), generated_at="now")
    # The bundle self-mounts on this element reading this global.
    assert 'id="evidence-graph"' in html
    assert "window.__EVIDENCE_GRAPH__" in html
    assert "FairscapeEvidenceGraph" in html
    assert ".react-flow" in html


def test_datasheet_survives_a_crate_with_almost_nothing():
    crate = Crate({"@graph": [{"@id": "./", "@type": "Dataset"}]}, path=None)
    html = render.datasheet_html(datasheet.build_context(crate))
    assert "<h1>" in html
