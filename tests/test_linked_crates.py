"""Linked crates: a consumer crate carries a *stub* of an entity another crate
produced (same `@id`, no provenance, `isPartOf` -> that crate's root, and a
crate node whose `ro-crate-metadata` points at the upstream metadata file).
The evidence walk must cross into the upstream crate and keep going; the
composition and datasheet must say where inputs came from; and a crate
with no such pointers must render exactly as before.

The convention is written by `fairscape_conversion.core.linking`; the
fixtures here are hand-built copies of what it emits, so this package is
tested without depending on that one.
"""

import json
import os

import pytest

from fairscape_artifacts import composition, datasheet, evidence, render
from fairscape_artifacts.crate import Crate

EVI = "https://w3id.org/EVI#"

UP_ROOT = "ark:59853/rocrate-upstream-1111111"
UP_RUN = "ark:59853/computation-upstream-run-2222222"
UP_STEP = "ark:59853/computation-step-2222223"
UP_RAW = "ark:59853/dataset-raw-txt-0000000"
UP_MID = "ark:59853/dataset-mid-txt-2222224"
UP_FILE = "ark:59853/dataset-table-tsv-3333333"
UP_DIR = "ark:59853/dataset-outdir-4444444"

C_ROOT = "ark:59853/rocrate-consumer-9999999"
C_RUN = "ark:59853/computation-analysis-ddddddd"
C_OUT = "ark:59853/dataset-result-json-fffffff"
C_NESTED = "ark:59853/dataset-nested-txt-bbbbbbb"


def _write(path, crate):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        json.dump(crate, fh)
    return path


def _upstream_graph():
    """raw.txt -> STEP -> mid.txt -> RUN -> table.tsv, outdir/"""
    return {"@graph": [
        {"@id": "ro-crate-metadata.json", "@type": "CreativeWork", "about": {"@id": UP_ROOT}},
        {"@id": UP_ROOT, "@type": ["Dataset", EVI + "ROCrate"], "name": "Upstream run",
         "description": "the producer",
         "hasPart": [{"@id": UP_RAW}, {"@id": UP_STEP}, {"@id": UP_MID}, {"@id": UP_RUN},
                     {"@id": UP_FILE}, {"@id": UP_DIR}],
         EVI + "outputs": [{"@id": UP_FILE}, {"@id": UP_DIR}]},
        {"@id": UP_RAW, "@type": ["prov:Entity", EVI + "Dataset"], "name": "raw.txt",
         "format": "text/plain", "generatedBy": []},
        {"@id": UP_STEP, "@type": ["prov:Activity", EVI + "Computation"], "name": "STEP",
         "usedDataset": [{"@id": UP_RAW}], "generated": [{"@id": UP_MID}]},
        {"@id": UP_MID, "@type": ["prov:Entity", EVI + "Dataset"], "name": "mid.txt",
         "format": "text/plain", "generatedBy": [{"@id": UP_STEP}]},
        {"@id": UP_RUN, "@type": ["prov:Activity", EVI + "Computation"], "name": "RUN",
         "usedDataset": [{"@id": UP_MID}], "generated": [{"@id": UP_FILE}, {"@id": UP_DIR}]},
        {"@id": UP_FILE, "@type": ["prov:Entity", EVI + "Dataset"], "name": "table.tsv",
         "description": "File 'table.tsv' produced by RUN", "format": "text/tab-separated-values",
         "contentUrl": "table.tsv", "generatedBy": [{"@id": UP_RUN}]},
        {"@id": UP_DIR, "@type": ["prov:Entity", EVI + "Dataset"], "name": "outdir",
         "contentUrl": "outdir", "generatedBy": [{"@id": UP_RUN}]},
    ]}


def _consumer_graph(pointer):
    """table.tsv (stub) + outdir/nested.txt (own node, isPartOf the upstream
    directory stub) -> analysis -> result.json"""
    return {"@graph": [
        {"@id": "ro-crate-metadata.json", "@type": "CreativeWork", "about": {"@id": C_ROOT}},
        {"@id": C_ROOT, "@type": ["Dataset", EVI + "ROCrate"], "name": "Consumer analysis",
         "description": "the consumer",
         "hasPart": [{"@id": UP_FILE}, {"@id": C_NESTED}, {"@id": C_RUN}, {"@id": C_OUT}],
         EVI + "inputs": [{"@id": UP_FILE}, {"@id": C_NESTED}],
         EVI + "outputs": [{"@id": C_OUT}]},
        # the stub: upstream id, upstream description, no provenance
        {"@id": UP_FILE, "@type": ["prov:Entity", EVI + "Dataset"], "name": "table.tsv",
         "description": "File 'table.tsv' produced by RUN", "format": "text/tab-separated-values",
         "localPath": "/abs/upstream/results/table.tsv", "digest": "8f9eb48c",
         "isPartOf": [{"@id": UP_ROOT}]},
        {"@id": C_NESTED, "@type": ["prov:Entity", EVI + "Dataset"], "name": "nested.txt",
         "format": "text/plain", "localPath": "/abs/upstream/results/outdir/nested.txt",
         "generatedBy": [], "isPartOf": [{"@id": UP_DIR}]},
        {"@id": UP_DIR, "@type": ["prov:Entity", EVI + "Dataset"], "name": "outdir",
         "localPath": "/abs/upstream/results/outdir", "isPartOf": [{"@id": UP_ROOT}]},
        {"@id": C_RUN, "@type": ["prov:Activity", EVI + "Computation"], "name": "analysis",
         "usedDataset": [{"@id": UP_FILE}, {"@id": C_NESTED}], "generated": [{"@id": C_OUT}]},
        {"@id": C_OUT, "@type": ["prov:Entity", EVI + "Dataset"], "name": "result.json",
         "format": "application/json", "contentUrl": "result.json",
         "generatedBy": [{"@id": C_RUN}]},
        # the pointer: a crate node the root does NOT list in hasPart
        {"@id": UP_ROOT, "@type": ["Dataset", EVI + "ROCrate"], "name": "Upstream run",
         "hasPart": [{"@id": UP_FILE}, {"@id": UP_DIR}], "ro-crate-metadata": pointer},
    ]}


@pytest.fixture
def pair(tmp_path):
    up = _write(str(tmp_path / "upstream" / "results" / "ro-crate-metadata.json"),
                _upstream_graph())
    con = _write(str(tmp_path / "consumer" / "ro-crate-metadata.json"),
                 _consumer_graph("../upstream/results/ro-crate-metadata.json"))
    return Crate.load(con), Crate.load(up)


# -- loading ----------------------------------------------------------------

def test_pointer_outside_haspart_is_a_linked_crate_not_a_constituent(pair):
    consumer, _ = pair
    assert consumer.sub_crates() == []
    linked = consumer.linked_crates()
    assert [s.crate.root_id for s in linked] == [UP_ROOT]
    assert linked[0].rel_dir == "../upstream/results"


def test_constituents_in_haspart_are_still_sub_crates(tmp_path):
    """A release crate lists its constituents in hasPart: the old reading."""
    up = _write(str(tmp_path / "sub" / "ro-crate-metadata.json"), _upstream_graph())
    release = {"@graph": [
        {"@id": "ro-crate-metadata.json", "@type": "CreativeWork", "about": {"@id": "ark:1/rel"}},
        {"@id": "ark:1/rel", "@type": ["Dataset", EVI + "ROCrate"], "name": "Release",
         "hasPart": [{"@id": UP_ROOT}]},
        {"@id": UP_ROOT, "@type": ["Dataset", EVI + "ROCrate"], "name": "Upstream run",
         "ro-crate-metadata": "sub/ro-crate-metadata.json"},
    ]}
    crate = Crate.load(_write(str(tmp_path / "ro-crate-metadata.json"), release))
    assert [s.crate.root_id for s in crate.sub_crates()] == [UP_ROOT]
    assert crate.linked_crates() == []
    assert composition.build(crate, out_dir=str(tmp_path)).is_release


def test_absolute_localpath_is_the_fallback_when_the_relative_pointer_breaks(tmp_path):
    up = _write(str(tmp_path / "elsewhere" / "ro-crate-metadata.json"), _upstream_graph())
    graph = _consumer_graph("../moved/ro-crate-metadata.json")
    graph["@graph"][-1]["localPath"] = up
    crate = Crate.load(_write(str(tmp_path / "consumer" / "ro-crate-metadata.json"), graph))
    assert [s.crate.root_id for s in crate.linked_crates()] == [UP_ROOT]


def test_a_missing_linked_crate_is_a_warning_not_a_crash(tmp_path, capsys):
    crate = Crate.load(_write(str(tmp_path / "consumer" / "ro-crate-metadata.json"),
                              _consumer_graph("../nowhere/ro-crate-metadata.json")))
    assert crate.linked_crates() == []
    assert "linked crate" in capsys.readouterr().err
    # and the graph still builds, ending at the stub
    graph = evidence.build(crate)["@graph"]
    assert UP_FILE in graph and UP_RUN not in graph


# -- evidence graph ------------------------------------------------------------

def test_evidence_graph_crosses_into_the_upstream_crate(pair):
    consumer, _ = pair
    graph = evidence.build(consumer)["@graph"]
    # this crate's chain
    assert {C_ROOT, C_OUT, C_RUN, UP_FILE, C_NESTED} <= set(graph)
    # ... continues through the stub into the upstream's chain, to its raw input
    assert {UP_RUN, UP_MID, UP_STEP, UP_RAW} <= set(graph)
    assert graph[UP_FILE]["generatedBy"] == {"@id": UP_RUN}
    assert graph[UP_RUN]["usedDataset"] == [{"@id": UP_MID}]


def test_nodes_from_the_upstream_crate_say_so(pair):
    consumer, _ = pair
    graph = evidence.build(consumer)["@graph"]
    assert graph[UP_RUN]["crate"] == {"@id": UP_ROOT, "name": "Upstream run"}
    assert graph[UP_FILE]["crate"] == {"@id": UP_ROOT, "name": "Upstream run"}
    assert "crate" not in graph[C_RUN] and "crate" not in graph[C_OUT]


def test_upstream_copy_wins_over_the_stub(pair):
    """The stub carries the consumer's extras but no provenance; the graph
    takes the description from whichever copy it has and the edges from the
    upstream."""
    consumer, _ = pair
    index, owner = consumer.provenance_index()
    assert "generatedBy" in index[UP_FILE]
    assert owner[UP_FILE] == UP_ROOT and owner[C_RUN] == C_ROOT


def test_directory_match_stops_at_the_directory(pair):
    """nested.txt is the consumer's own node inside an upstream directory
    Dataset; the walk does not follow isPartOf, so it ends there, and the
    directory stub is not pulled in as a phantom producer."""
    consumer, _ = pair
    graph = evidence.build(consumer)["@graph"]
    assert C_NESTED in graph
    assert "generatedBy" not in graph[C_NESTED]


def test_linked_crates_chain_onward(tmp_path):
    """upstream links to its own upstream: the closure follows it once."""
    deeper = {"@graph": [
        {"@id": "ro-crate-metadata.json", "@type": "CreativeWork", "about": {"@id": "ark:1/deep"}},
        {"@id": "ark:1/deep", "@type": ["Dataset", EVI + "ROCrate"], "name": "Deep",
         "hasPart": [{"@id": "ark:1/deepcomp"}, {"@id": UP_RAW}]},
        {"@id": "ark:1/deepcomp", "@type": ["prov:Activity", EVI + "Computation"],
         "name": "DEEP", "generated": [{"@id": UP_RAW}]},
        {"@id": UP_RAW, "@type": ["prov:Entity", EVI + "Dataset"], "name": "raw.txt",
         "generatedBy": [{"@id": "ark:1/deepcomp"}]},
    ]}
    _write(str(tmp_path / "deep" / "ro-crate-metadata.json"), deeper)
    up = _upstream_graph()
    up["@graph"][2]["generatedBy"] = []               # raw.txt: stub in the upstream
    up["@graph"][2]["isPartOf"] = [{"@id": "ark:1/deep"}]
    up["@graph"].append({"@id": "ark:1/deep", "@type": ["Dataset", EVI + "ROCrate"],
                         "name": "Deep", "ro-crate-metadata": "../../deep/ro-crate-metadata.json"})
    _write(str(tmp_path / "upstream" / "results" / "ro-crate-metadata.json"), up)
    con = Crate.load(_write(str(tmp_path / "consumer" / "ro-crate-metadata.json"),
                            _consumer_graph("../upstream/results/ro-crate-metadata.json")))
    assert [s.crate.root_id for s in con.linked_closure()] == [UP_ROOT, "ark:1/deep"]
    graph = evidence.build(con)["@graph"]
    assert "ark:1/deepcomp" in graph
    assert graph["ark:1/deepcomp"]["crate"]["name"] == "Deep"


def test_a_crate_with_no_pointers_builds_exactly_as_before(tmp_path):
    up = Crate.load(_write(str(tmp_path / "ro-crate-metadata.json"), _upstream_graph()))
    before = json.dumps(evidence.EvidenceGraph(dict(up.index), 5, crate_root_id=up.root_id)
                        .build(up.root_id), sort_keys=True)
    after = json.dumps(evidence.build(up), sort_keys=True)
    assert before == after
    assert "crate" not in json.loads(after)["@graph"][UP_RUN]


# -- composition and datasheet -------------------------------------------------

def test_consumer_is_not_rendered_as_a_release(pair, tmp_path):
    consumer, _ = pair
    comp = composition.build(consumer, out_dir=str(tmp_path / "consumer"))
    assert not comp.is_release
    assert len(comp.items) == 1
    assert comp.items[0].crate is consumer


def test_composition_names_the_upstream_crate_as_input_origin(pair, tmp_path):
    consumer, _ = pair
    comp = composition.build(consumer, out_dir=str(tmp_path / "consumer"))
    rows = comp.items[0].context["details"]["computation_patterns"]["rows"]
    origins = {i["crate"] for row in rows for i in row["inputs"]}
    assert "Upstream run" in origins


def test_datasheet_lists_linked_crates(pair, tmp_path):
    consumer, _ = pair
    comp = composition.build(consumer, out_dir=str(tmp_path / "consumer"))
    context = datasheet.build_context(consumer, composition=comp)
    linked = next(r for r in context["overview"] if r["id"] == "linked-crates")
    assert linked["kind"] == "list"
    assert linked["entries"][0]["text"].startswith("Upstream run (ark:59853/rocrate-upstream-1111111)")
    assert "2 entities" in linked["entries"][0]["text"]
    assert {"label": "linked crates", "n": "1"} in context["summary"]["extra"]
    html = render.datasheet_html(context)
    assert "Linked Crates" in html and "Upstream run" in html


def test_datasheet_without_links_has_no_linked_row(tmp_path):
    up = Crate.load(_write(str(tmp_path / "ro-crate-metadata.json"), _upstream_graph()))
    context = datasheet.build_context(up)
    assert not [r for r in context["overview"] if r["id"] == "linked-crates"]
    assert not [e for e in context["summary"]["extra"] if e["label"] == "linked crates"]
