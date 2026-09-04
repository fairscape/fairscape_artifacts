"""The datasheet: composition cards, summary, overview rows, and the pages.

The in-memory crate below exercises every card feature on a crate with no
constituents; the CM4AI June release (skipped when absent) exercises the
release path: nine sub-crates loaded from disk, cross-crate input labels,
the index table and filter.
"""

from __future__ import annotations

import json
import os
import re
import shutil

import pytest

from conftest import repo_path

from fairscape_artifacts import cli, composition, datasheet, grading, preview, render
from fairscape_artifacts.crate import Crate

CM4AI = repo_path("CM4AIJuneRelease")


def _graph():
    return {"@graph": [
        {"@id": "ro-crate-metadata.json", "@type": "CreativeWork", "about": {"@id": "ark:1/root"},
         "conformsTo": {"@id": "https://w3id.org/ro/crate/1.2"}},
        {"@id": "ark:1/root", "@type": ["Dataset", "https://w3id.org/EVI#ROCrate"],
         "name": "Tiny crate", "description": "A" * 600, "version": "2.0",
         "datePublished": "2026-01-02", "author": [{"@id": "https://orcid.org/0000-0000"}],
         "keywords": ["one", "two"], "publisher": "https://repo.example",
         "contentUrl": "https://repo.example/download", "contentSize": "1500",
         "rai:dataUseCases": ["train", "test"], "prohibitedUses": "clinical use",
         "rai:dataCollectionTimeframe": ["1/1/2020", "2/2/2021"],
         "humanSubjectResearch": "None", "deidentified": True,
         "irb": {"name": "Some IRB", "contactPoint": {"email": "irb@example.org"}},
         "conformsTo": [{"@id": "https://w3id.org/ro/crate/1.2"}],
         "hasPart": []},
        {"@id": "https://orcid.org/0000-0000", "@type": "Person", "name": "Ada Lovelace"},
        {"@id": "ark:1/in", "@type": ["prov:Entity", "https://w3id.org/EVI#Dataset"],
         "name": "input", "format": "<evil>", "contentUrl": "https://x/in"},
        {"@id": "ark:1/out", "@type": ["prov:Entity", "https://w3id.org/EVI#Dataset"],
         "name": "output", "format": "text/csv", "contentSize": "2500",
         "generatedBy": [{"@id": "ark:1/comp"}]},
        {"@id": "ark:1/comp", "@type": ["prov:Activity", "https://w3id.org/EVI#Computation"],
         "name": "compute", "command": ["python", "run.py"], "dateCreated": "2026-01-01T10:00:00",
         "usedDataset": [{"@id": "ark:1/in"}], "usedSoftware": [{"@id": "ark:1/sw"}],
         "generated": [{"@id": "ark:1/out"}]},
        {"@id": "ark:1/sw", "@type": ["prov:Entity", "https://w3id.org/EVI#Software"],
         "name": "run.py", "format": "py", "contentUrl": "https://x/run.py"},
        {"@id": "ark:1/schema", "@type": "EVI:Schema", "name": "columns",
         "properties": {"b": {"type": "string", "index": 1}, "a": {"type": "integer", "index": 0,
                                                                   "description": "first"}}},
        {"@id": "ark:1/sample", "@type": ["prov:Entity", "https://w3id.org/EVI#Sample"],
         "name": "cells", "cellLineReference": {"@id": "ark:1/line"}},
        {"@id": "ark:1/line", "@type": "BioChemEntity", "name": "MDA-MB-468",
         "organism": {"name": "Homo sapiens"}},
        {"@id": "ark:1/exp", "@type": ["prov:Activity", "https://w3id.org/EVI#Experiment"],
         "name": "imaging", "experimentType": "IF", "usedSample": [{"@id": "ark:1/sample"}],
         "generated": [{"@id": "ark:1/img"}]},
        {"@id": "ark:1/img", "@type": ["prov:Entity", "https://w3id.org/EVI#Dataset"],
         "name": "image", "format": "jpg", "generatedBy": [{"@id": "ark:1/exp"}],
         "contentUrl": "Embargoed"},
        {"@id": "ark:1/protein", "@type": ["prov:Entity", "evi:BioChemEntity"], "name": "ATM"},
    ]}


@pytest.fixture
def tiny(tmp_path):
    path = tmp_path / "ro-crate-metadata.json"
    path.write_text(json.dumps(_graph()))
    return Crate.load(str(path))


@pytest.fixture(scope="module")
def release():
    if not os.path.exists(os.path.join(CM4AI, "ro-crate-metadata.json")):
        pytest.skip("CM4AI June release not present")
    return Crate.load(CM4AI)


# -- composition, single crate ------------------------------------------------

def test_single_crate_is_its_own_card(tiny, tmp_path):
    comp = composition.build(tiny, out_dir=str(tmp_path))
    assert not comp.is_release
    assert len(comp.items) == 1
    card = comp.items[0].context
    d = card["details"]
    assert card["name"] == "Tiny crate"
    assert card["preview_href"] == "ro-crate-preview.html"
    assert card["authors"] == "Ada Lovelace"
    assert (d["files"], d["software"], d["computations"], d["schemas"],
            d["samples"], d["experiments"], d["other"]) == (3, 1, 1, 1, 1, 1, 3)
    assert d["with_provenance"] == 2
    assert {row["label"]: row["n"] for row in d["file_access"]} == {
        "Available": 1, "Embargoed": 1, "No link": 1}
    assert {row["label"] for row in d["file_formats"]} == {"<evil>", "csv", "jpg"}
    assert d["cell_lines"] == [{"id": "ark:1/line", "name": "MDA-MB-468",
                                "organism": "Homo sapiens"}]
    assert d["experiment_types"] == [{"label": "IF", "n": 1}]
    assert d["experiment_patterns"]["rows"] == [
        {"inputs": [{"crate": None, "formats": ["Sample"]}], "outputs": ["jpg"], "n": 1}]
    assert d["computation_patterns"]["rows"] == [
        {"inputs": [{"crate": None, "formats": ["<evil>"]}], "outputs": ["csv"], "n": 1}]


def test_derived_inputs_skip_references_outside_the_crate(tiny, tmp_path):
    """A computation that read a scratch file not described in the crate
    must not turn that file into a declared input."""
    tiny.index["ark:1/comp"]["usedDataset"].append({"@id": "#task/scratch"})
    d = composition.build(tiny, out_dir=str(tmp_path)).items[0].context["details"]
    labels = {row["label"] for row in d["input_datasets"]}
    assert "unresolved reference" not in labels
    assert "Sample" in labels


def test_pattern_text_is_escaped_in_the_page(tiny, tmp_path):
    comp = composition.build(tiny, out_dir=str(tmp_path))
    html = render.datasheet_html(datasheet.build_context(tiny, composition=comp))
    assert "<evil>" not in html
    assert "&lt;evil&gt;" in html
    assert "Some IRB" in html and "irb@example.org" in html


# -- composition, release crate -------------------------------------------------

def test_release_loads_every_sub_crate(release, tmp_path):
    subs = release.sub_crates()
    assert len(subs) == 9
    assert [s.rel_dir for s in subs][:2] == ["AP-MS/apms-paclitaxel-rocrate",
                                             "AP-MS/apms-vorinostat-rocrate"]
    assert all(len(s.crate) > 1 for s in subs)


def test_release_cards_match_the_old_datasheet(release, tmp_path):
    comp = composition.build(release, out_dir=str(tmp_path))
    assert comp.is_release and len(comp.items) == 9
    apms = comp.items[0].context
    d = apms["details"]
    # The old generator counted the sub-crate's root as a file; 555 is right.
    assert (d["files"], d["experiments"], d["samples"], d["instruments"],
            d["schemas"], d["other"]) == (555, 118, 117, 1, 2, 59)
    assert d["file_formats"][0] == {"label": "raw", "n": 549}
    assert d["experiment_types"] == [{"label": "AP-MS LFQ", "n": 118}]
    assert d["cell_lines"][0]["name"] == "MDA-MB-468"
    assert apms["doi_href"] == "https://doi.org/10.18130/V3/HIGT4C"   # inherited from root
    assert apms["preview_href"] == "AP-MS/apms-paclitaxel-rocrate/ro-crate-preview.html"
    assert apms["evidence_label"] == "ro-crate-prov-graph.html"

    atlas = comp.items[-1].context
    pattern = atlas["details"]["computation_patterns"]["rows"][0]
    assert pattern["inputs"][0]["crate"].startswith("Perturbation Cell Atlas")
    assert pattern["inputs"][0]["formats"] == ["h5"]
    assert atlas["details"]["input_datasets"][0]["n"] == 90
    assert atlas["statistics"]["name"].endswith("Cell Ranger Statistics")


def test_release_summary_trusts_root_counters(release, tmp_path):
    comp = composition.build(release, out_dir=str(tmp_path))
    context = datasheet.build_context(release, composition=comp)
    stats = {t["label"]: t["n"] for t in context["summary"]["stats"]}
    assert stats["Total size"] == "19.9 TB"
    assert stats["Datasets"] == "53,877"
    assert {"label": "sub-crates", "n": "9"} in context["summary"]["extra"]
    assert context["summary"]["truncated"]
    assert context["composition"]["show_index"]
    html = render.datasheet_html(context)
    assert 'id="dataset-filter"' in html
    assert html.count('class="idx-row"') == 9
    assert html.count('<details class="card"') == 9
    assert 'id="subcrate-9"' in html


# -- datasheet context ----------------------------------------------------------

def test_overview_rows_flag_missing_doi_and_license(tiny, tmp_path):
    context = datasheet.build_context(tiny)
    rows = {r["label"]: r for r in context["overview"]}
    assert rows["DOI"]["kind"] == "missing"
    assert rows["License"]["kind"] == "missing"
    assert rows["Authors"]["value"] == "Ada Lovelace"
    assert rows["Publisher"]["href"] == "https://repo.example"
    assert rows["Conforms To"]["kind"] == "list"
    assert "Copyright" not in rows


def test_regulatory_reads_fields_without_inventing_defaults(tiny):
    reg = datasheet.regulatory(tiny)
    assert reg["human_subject_research"] == "None"
    assert reg["deidentified"] == "Yes"
    assert reg["fda_regulated"] == ""            # absent stays absent
    assert reg["irb"]["name"] == "Some IRB"


def test_use_cases_join_lists_and_fall_back(tiny):
    rows = {r["label"]: r["value"] for r in datasheet.use_cases(tiny)}
    assert rows["Intended Uses"] == "train, test"
    assert rows["Prohibited Uses"] == "clinical use"
    assert rows["Collection Timeframe"] == "1/1/2020 – 2/2/2021"
    assert "Limitations" not in rows


def test_summary_counts_the_graph_when_no_counters(tiny):
    context = datasheet.build_context(tiny)
    stats = [(t["label"], t["n"]) for t in context["summary"]["stats"]]
    assert stats == [("Total size", "1.5 KB"), ("Datasets", "3"), ("Computations", "1"),
                     ("Software", "1")]
    assert context["summary"]["extra"] == [{"label": "samples", "n": "1"},
                                           {"label": "experiments", "n": "1"},
                                           {"label": "schemas", "n": "1"}]
    assert context["summary"]["access_href"] == "https://repo.example/download"
    assert context["badges"][0]["label"] == "Version 2.0"
    assert context["review"] is None
    assert not context["composition"]["show_index"]


def test_review_panel_geometry_counts_criteria_not_points():
    presentation = {"rubric": "r", "rubric_version": "1.5", "generated": "", "network_checks": False,
                    "sections": [{"number": 0, "title": "FAIRness", "gating": True, "criteria": [
                        {"id": "0.a", "name": "Findable", "estimate": {"score": "2"}, "evidence": []},
                        {"id": "0.b", "name": "Accessible", "estimate": {"score": "1"}, "evidence": []},
                        {"id": "0.c", "name": "Interoperable", "estimate": None, "evidence": []},
                        {"id": "0.d", "name": "Reusable", "estimate": {"score": "0"}, "evidence": []},
                    ]}]}
    panel = datasheet.review_panel(grading.summarize(presentation))
    assert panel["estimated"] == 3 and panel["criteria_total"] == 4
    # Absent draws nothing: three slices, a quarter of the ring left empty.
    assert [s["cls"] for s in panel["donut"]] == ["s2", "s1", "sna"]
    assert sum(s["length"] for s in panel["donut"]) == pytest.approx(0.75 * panel["circumference"], abs=0.1)
    assert panel["donut"][1]["offset"] == pytest.approx(-panel["donut"][0]["length"])
    # Points follow the rubric's 2/1/0 over estimated criteria only.
    assert (panel["points"], panel["possible"]) == (3, 6)
    section = panel["sections"][0]
    assert section["estimated"] == 3 and section["total"] == 4
    assert (section["points"], section["possible"]) == (3, 6)
    assert [s["pct"] for s in section["segments"]] == [25.0, 25.0, 25.0]
    assert panel["legend"][-1]["label"] == "No points"


def test_datasheet_page_has_every_section(tiny, tmp_path):
    html = render.datasheet_html(datasheet.build_context(
        tiny, links={"evidence_graph": "ro-crate-evidence-graph.html"}))
    for section in ("datasheet-summary", "dataset-details", "ai-readiness", "composition",
                    "distribution", "sidebar", "subcrate-1"):
        assert f'id="{section}"' in html
    assert 'id="dataset-filter"' not in html          # one card: nothing to filter
    assert 'href="ro-crate-evidence-graph.html"' in html
    assert "read full description" in html
    assert "Not specified" in html


# -- preview -------------------------------------------------------------------------

def test_preview_context_tabs_and_details(tiny, tmp_path):
    comp = composition.build(tiny, out_dir=str(tmp_path))
    context = preview.build_context(tiny, comp.index, comp.owner)
    tabs = {t["key"]: t for t in context["tabs"]}
    assert [t["key"] for t in context["tabs"]] == ["datasets", "software", "computations",
                                                   "samples", "experiments", "schemas", "other"]
    comp_item = tabs["computations"]["entries"][0]
    assert comp_item["computation"]["command"] == "python run.py"
    assert comp_item["computation"]["inputs"][0]["name"] == "input"
    assert comp_item["computation"]["outputs"][0]["format"] == "csv"
    assert comp_item["computation"]["software"][0]["id"] == "ark:1/sw"
    assert comp_item["date"] == "2026-01-01"
    props = tabs["schemas"]["entries"][0]["properties"]
    assert [p["name"] for p in props] == ["a", "b"]         # ordered by index
    assert tabs["datasets"]["entries"][0]["access"] in ("Available", "Embargoed", "No link")
    html = render.preview_html(context)
    assert 'data-tab="computations"' in html
    assert 'class="toggle"' in html
    assert "<evil>" not in html and "&lt;evil&gt;" in html


# -- CLI -----------------------------------------------------------------------------

def test_cli_datasheet_writes_the_preview_beside_it(tmp_path, capsys):
    src = repo_path("wizards", "example-wf", "ro-crate-metadata.json")
    if not os.path.exists(src):
        pytest.skip("example crate not present")
    shutil.copy(src, tmp_path / "ro-crate-metadata.json")
    (tmp_path / "ro-crate-evidence-graph.html").write_text("<html></html>")
    assert cli.main(["datasheet", str(tmp_path), "--no-review"]) == 0
    assert (tmp_path / "ro-crate-datasheet.html").exists()
    assert (tmp_path / "ro-crate-preview.html").exists()
    sheet = (tmp_path / "ro-crate-datasheet.html").read_text()
    assert 'href="ro-crate-evidence-graph.html"' in sheet
    assert 'href="ai-ready-review.html"' not in sheet
    page = (tmp_path / "ro-crate-preview.html").read_text()
    assert 'href="ro-crate-datasheet.html"' in page
    assert not re.search(r"<script[^>]+\bsrc=", page)


def test_cli_no_previews(tmp_path):
    src = repo_path("wizards", "example-wf", "ro-crate-metadata.json")
    if not os.path.exists(src):
        pytest.skip("example crate not present")
    shutil.copy(src, tmp_path / "ro-crate-metadata.json")
    assert cli.main(["datasheet", str(tmp_path), "--no-review", "--no-previews"]) == 0
    assert not (tmp_path / "ro-crate-preview.html").exists()
    assert "ro-crate-preview.html" not in (tmp_path / "ro-crate-datasheet.html").read_text()
