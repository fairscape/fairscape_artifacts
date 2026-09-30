"""Interpretation: the adapters over a crate on disk, and the page.

The engine belongs to `fairscape_graph_tools` and is not tested here. What
is tested is the seam: that a crate on disk reaches the engine's ports
correctly, that its output lands as a sidecar, and that the page reads that
output in pipeline order. The end-to-end run uses pydantic-ai's built-in
`test` model, so it needs no key and no network.
"""

from __future__ import annotations

import json
import os

import pytest

from fairscape_artifacts import interpretation, render
from fairscape_artifacts.crate import Crate
from conftest import PACKAGE_ROOT

# The sibling checkout of a real interpretation (a nine-step CM4AI pipeline).
EXAMPLE_AEG = os.path.join(os.path.dirname(PACKAGE_ROOT), "intrepretation_example",
                           "interpretation", "annotated_evidence_graph.json")


def _node(node_id, types, **fields):
    return {"@id": node_id, "@type": types, **fields}


def write_crate(directory, *, root_types=("Dataset", "https://w3id.org/EVI#ROCrate"),
                software_url="file:///step.py"):
    """Two computations in a chain sharing one local script."""
    os.makedirs(directory, exist_ok=True)
    with open(os.path.join(directory, "step.py"), "w") as fh:
        fh.write("def main(inp, out):\n    open(out, 'w').write(open(inp).read())\n")
    graph = [
        _node("ro-crate-metadata.json", "CreativeWork", about={"@id": "ark:59852/rocrate-t"}),
        _node("ark:59852/rocrate-t", list(root_types), name="Toy crate",
              description="Two steps", keywords=["toy"],
              hasPart=[{"@id": i} for i in ("ark:59852/dataset-raw", "ark:59852/dataset-mid",
                                             "ark:59852/dataset-out", "ark:59852/software-step",
                                             "ark:59852/computation-a", "ark:59852/computation-b")]),
        _node("ark:59852/dataset-raw", "https://w3id.org/EVI#Dataset", name="raw.csv", format="csv",
              descriptiveStatistics={"columns": {"a": {"statistics": {"count": 3}}}}),
        _node("ark:59852/software-step", "https://w3id.org/EVI#Software", name="step.py",
              contentUrl=software_url),
        _node("ark:59852/computation-a", "https://w3id.org/EVI#Computation", name="first",
              usedSoftware=[{"@id": "ark:59852/software-step"}],
              usedDataset=[{"@id": "ark:59852/dataset-raw"}],
              generated=[{"@id": "ark:59852/dataset-mid"}]),
        _node("ark:59852/dataset-mid", "https://w3id.org/EVI#Dataset", name="mid.csv", format="csv",
              generatedBy={"@id": "ark:59852/computation-a"}),
        _node("ark:59852/computation-b", "https://w3id.org/EVI#Computation", name="second",
              usedSoftware=[{"@id": "ark:59852/software-step"}],
              usedDataset=[{"@id": "ark:59852/dataset-mid"}],
              generated=[{"@id": "ark:59852/dataset-out"}]),
        _node("ark:59852/dataset-out", "https://w3id.org/EVI#Dataset", name="out.csv", format="csv",
              generatedBy={"@id": "ark:59852/computation-b"}),
    ]
    path = os.path.join(directory, "ro-crate-metadata.json")
    with open(path, "w") as fh:
        json.dump({"@context": {"@vocab": "https://schema.org/"}, "@graph": graph}, fh)
    return path


# -- adapters ---------------------------------------------------------------

@pytest.fixture
def adapters():
    pytest.importorskip("fairscape_graph_tools")
    from fairscape_artifacts import interpret
    return interpret


def test_graph_source_walks_the_whole_crate(tmp_path, adapters):
    crate = Crate.load(write_crate(tmp_path / "c"))
    source = adapters.CrateGraphSource(crate)
    assert source.primary_root_id == "ark:59852/rocrate-t"
    ids = {n["@id"] for n in source.build_full_graph(source.primary_root_id)}
    assert ids == {"ark:59852/rocrate-t", "ark:59852/dataset-raw", "ark:59852/dataset-mid",
                   "ark:59852/dataset-out", "ark:59852/software-step",
                   "ark:59852/computation-a", "ark:59852/computation-b"}
    assert "ro-crate-metadata.json" not in ids


def test_graph_source_matches_arks_loosely_but_batches_exactly(tmp_path, adapters):
    source = adapters.CrateGraphSource(Crate.load(write_crate(tmp_path / "c")))
    assert source.find_entity("ark:59852/datasetraw")["name"] == "raw.csv"
    assert source.find_entity("ark:59852/nope") is None
    assert set(source.find_many(["ark:59852/datasetraw", "ark:59852/dataset-raw"])) == {
        "ark:59852/dataset-raw"}
    assert source.find_dataset_stats(["ark:59852/dataset-raw", "ark:59852/dataset-mid"]) == {
        "ark:59852/dataset-raw": {"descriptiveStatistics": {"columns": {"a": {"statistics": {"count": 3}}}},
                                  "splitStatistics": {}}}


def test_graph_source_types_an_untyped_root_for_the_engine(tmp_path, adapters):
    """The engine finds the root by `ROCrate` in `@type`. A PROV-style crate
    whose root is a plain Dataset must still give it one — in the index only."""
    path = write_crate(tmp_path / "c", root_types=("Dataset",))
    crate = Crate.load(path)
    source = adapters.CrateGraphSource(crate)
    assert "https://w3id.org/EVI#ROCrate" in source.find_entity(crate.root_id)["@type"]
    assert crate.root["@type"] == ["Dataset"]
    with open(path) as fh:
        assert json.load(fh)["@graph"][1]["@type"] == ["Dataset"]


def test_software_is_read_from_the_owning_crate(tmp_path, adapters):
    source = adapters.CrateGraphSource(Crate.load(write_crate(tmp_path / "c")))
    fetcher = adapters.LocalSoftwareFetcher(source)
    code = fetcher.fetch(source.find_entity("ark:59852/software-step"))
    assert code.startswith("def main(")


def test_missing_software_is_a_placeholder_not_an_error(tmp_path, adapters):
    path = write_crate(tmp_path / "c", software_url="file:///gone.py")
    source = adapters.CrateGraphSource(Crate.load(path))
    code = adapters.LocalSoftwareFetcher(source).fetch(source.find_entity("ark:59852/software-step"))
    assert code == adapters.SOURCE_PLACEHOLDER


def test_tracker_reports_through_the_callable_and_traces_to_jsonl(tmp_path, adapters):
    lines = []
    trace = tmp_path / "trace.jsonl"
    tracker = adapters.ProgressTracker(lines.append, str(trace))
    tracker.update({"current_step": "PROMPTING", "total_computations": 2})
    tracker.update({"current_step": "PROMPTING"})
    tracker.increment_completed()
    tracker.push_llm_result("synthesis", {"a": 1})
    assert lines == ["[PROMPTING]", "  2 computation(s)", "  1/2 annotated"]
    assert json.loads(trace.read_text())["label"] == "synthesis"


# -- end to end -------------------------------------------------------------

def test_run_writes_an_annotated_evidence_graph_beside_the_crate(tmp_path):
    pytest.importorskip("fairscape_graph_tools")
    crate = Crate.load(write_crate(tmp_path / "c"))
    out = tmp_path / "c" / interpretation.INTERPRETATION_JSON
    progress = []
    aeg = interpretation.run(crate, output_path=str(out), model="test",
                             progress=progress.append, rate_limit_requests=100)
    assert out.exists()
    with open(out) as fh:
        assert json.load(fh)["@id"] == aeg["@id"]
    assert aeg["evi:annotates"] == {"@id": "ark:59852/rocrate-t"}
    assert len(aeg["evi:stepAnnotations"]) == 2
    assert any(line.startswith("[SYNTHESIZING]") for line in progress)

    context = interpretation.summarize(aeg)
    assert [s["name"] for s in context["steps"]] == ["first", "second"]
    html = render.interpretation_html({**context, "source": "c", "generated_at": ""})
    assert 'id="step-2"' in html


def test_api_key_lands_in_the_providers_variable(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    interpretation.configure_api_key("groq:llama", "k")
    assert os.environ["GROQ_API_KEY"] == "k"
    interpretation.configure_api_key("groq:llama", None)
    with pytest.raises(ValueError):
        interpretation.configure_api_key("uvarc:Kimi", "k")


# -- page context from a real interpretation ---------------------------------

@pytest.fixture(scope="module")
def example():
    if not os.path.exists(EXAMPLE_AEG):
        pytest.skip("example interpretation not checked out")
    return interpretation.summarize(interpretation.load(EXAMPLE_AEG),
                                    link_base="https://example.org")


def test_steps_come_out_in_pipeline_order(example):
    names = [s["raw_name"] for s in example["steps"]]
    assert names.index("Computation_convert_manifest") < names.index("Computation_cellmaps_image_embeddingcmd")
    assert names.index("Computation_secms_to_edgelist") < names.index("Computation_cellmaps_ppi_embeddingcmd")
    assert names.index("Computation_split_combined_embeddings") < names.index("Computation_cellmaps_coembeddingcmd")
    assert names[-1] == "Computation_visualize_hierarchy"
    assert [s["number"] for s in example["steps"]] == list(range(1, 10))


def test_graph_assumptions_are_ranked_and_point_at_their_step(example):
    impacts = [a["impact"] for a in example["assumptions"]]
    assert impacts == sorted(impacts, key=interpretation.IMPACT_ORDER.index)
    assert all(a["source_anchor"].startswith("step-") for a in example["assumptions"])
    assert example["assumptions"][0]["evidence"]["href"].startswith("https://example.org")


def test_example_page_is_self_contained(example):
    html = render.interpretation_html({**example, "source": "x", "generated_at": ""})
    assert "<link" not in html and 'src="http' not in html
    assert html.count('<details class="step') == 9
    # Graph first: the viewer mounts on the whole document, and every step
    # can be located in it from the text below.
    assert '<div id="annotated-graph"' in html
    assert "window.__ANNOTATED_GRAPH__ = {" in html
    assert html.count('class="locate" data-node=') >= 9


def test_step_names_read_as_words_but_keep_the_original(example):
    step = next(s for s in example["steps"] if "_" in s["raw_name"])
    assert "_" not in step["name"]
    assert interpretation._human_name("IMAGE_EMBEDDING (U2OS)") == "Image Embedding (U2OS)"
    assert interpretation._human_name("PPI_DOWNLOAD (apms)") == "PPI Download (apms)"


def test_every_step_has_a_headline_and_a_short_gist(example):
    for step in example["steps"]:
        assert step["headline"], step["raw_name"]
        assert len(step["headline"]) <= interpretation.HEADLINE_CHARS + 1
        assert step["gist"] and len(step["gist"]) <= len(step["summary"])
    assert example["gist"]
    assert example["gist"] in example["executive"]


def test_a_whole_run_computation_is_listed_last_and_unnumbered():
    def comp(i, used, made):
        return {"@id": f"c{i}", "@type": "Computation", "name": f"STEP_{i}",
                "usedDataset": [{"@id": d} for d in used],
                "generated": [{"@id": d} for d in made]}
    graph = {
        "root": {"@id": "root", "@type": "ROCrate"},
        "raw": {"@id": "raw", "@type": "Dataset", "name": "raw"},
        "mid": {"@id": "mid", "@type": "Dataset", "name": "mid", "generatedBy": [{"@id": "c1"}]},
        "out": {"@id": "out", "@type": "Dataset", "name": "out", "generatedBy": [{"@id": "c2"}]},
        "c1": comp(1, ["raw"], ["mid"]),
        "c2": comp(2, ["mid"], ["out"]),
        # The run claims both stages' outputs as its own.
        "run": {"@id": "run", "@type": "Computation", "name": "Workflow run",
                "usedDataset": [{"@id": "raw"}], "generated": [{"@id": "mid"}, {"@id": "out"}]},
    }
    for i in ("c1", "c2", "run"):
        graph[f"{i}-a"] = {"@id": f"{i}-a", "@type": "AnnotatedComputation",
                           "evi:annotates": {"@id": i}, "evi:stepSummary": f"Does {i}. Then more.",
                           "evi:computationStatus": "clear"}
    aeg = {"@id": "aeg", "@graph": graph, "evi:annotates": {"@id": "root"},
           "evi:stepAnnotations": [{"@id": "run-a"}, {"@id": "c1-a"}, {"@id": "c2-a"}],
           "evi:executiveSummary": "Two steps. And a run."}
    context = interpretation.summarize(aeg)
    assert [(s["raw_name"], s["number"], s["umbrella"]) for s in context["steps"]] == [
        ("STEP_1", 1, False), ("STEP_2", 2, False), ("Workflow run", None, True)]
    viewer = context["graph"]["@graph"]
    assert viewer["c1-a"]["_number"] == 1 and viewer["c1-a"]["_headline"] == "Does c1."
    assert "_number" not in viewer["run-a"]
    assert context["gist"] == "Two steps."
