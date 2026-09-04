"""PROV-converted crates: `prov:Activity` / `prov:Entity` and `derivedFrom`.

Crates imported from PROV bundles spell provenance differently: activities and
entities may carry only their PROV type, and an entity's ancestry may survive
as an entity-to-entity `wasDerivedFrom` because the activity that made it was
never recorded. The upstream builder walks neither, so such a crate renders as
a handful of disconnected nodes.

The fixture mirrors the CPM crate the recovered `evidence-graph.html` was
generated from — a `prov:Activity` transfer receipt with no EVI type, and a
`derivedFrom` chain with no generating computation.

The last section goes further, to crates with *no EVI vocabulary at all*: PROV
types, PROV edge names, an ordinary `Dataset` root and no declared outputs.
Those exercise the entry point rather than the walk — deriving outputs and
recognizing the crate root without an `EVI#ROCrate` type.
"""

from __future__ import annotations

import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO, "artifacts"))

from fairscape_artifacts import evidence, outputs  # noqa: E402
from fairscape_artifacts.crate import (Crate, evi_type,  # noqa: E402
                                       provenance_edge, used_dataset_ids)

EVI_OUTPUTS = "https://w3id.org/EVI#outputs"


def _crate(*nodes):
    root = {
        "@id": "ark:99999/crate",
        "@type": ["Dataset", "https://w3id.org/EVI#ROCrate"],
        "name": "PROV fixture",
        EVI_OUTPUTS: [{"@id": nodes[0]["@id"]}],
    }
    return Crate({"@graph": [root, *nodes]}, path=None)


def _entity(node_id, **fields):
    return {"@id": node_id, "@type": ["prov:Entity", "https://w3id.org/EVI#Dataset"],
            "name": node_id, "description": "", **fields}


# -- type reading ---------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("prov:Activity", "Computation"),
    ("prov:Entity", "Dataset"),
    (["prov:Activity", "https://w3id.org/EVI#Computation"], "Computation"),
    (["prov:Entity", "https://w3id.org/EVI#Dataset"], "Dataset"),
    ("http://schema.org/Dataset", None),
])
def test_prov_types_read(raw, expected):
    assert evi_type({"@type": raw}) == expected


def test_evi_type_wins_over_prov():
    """An explicit EVI type is never overridden by the PROV fallback."""
    assert evi_type({"@type": ["prov:Entity", "https://w3id.org/EVI#Software"]}) == "Software"


# -- edge precedence ------------------------------------------------------

def test_generated_by_wins_over_derived_from():
    edge, ids = provenance_edge({"generatedBy": {"@id": "c"}, "derivedFrom": {"@id": "d"}})
    assert (edge, ids) == ("generatedBy", ["c"])


def test_derived_from_is_the_fallback():
    assert provenance_edge({"derivedFrom": {"@id": "d"}}) == ("derivedFrom", ["d"])


def test_prov_was_generated_by_alias():
    assert provenance_edge({"prov:wasGeneratedBy": {"@id": "c"}}) == ("generatedBy", ["c"])


# -- traversal ------------------------------------------------------------

def test_derived_from_chain_is_followed():
    """No computation anywhere — the chain exists only as entity derivation."""
    crate = _crate(
        _entity("ark:99999/c", derivedFrom=[{"@id": "ark:99999/b"}]),
        _entity("ark:99999/b", derivedFrom=[{"@id": "ark:99999/a"}]),
        _entity("ark:99999/a"),
    )
    graph = evidence.build(crate)["@graph"]

    assert set(graph) >= {"ark:99999/a", "ark:99999/b", "ark:99999/c"}
    # Emitted as a list, matching the recovered wire format.
    assert graph["ark:99999/c"]["derivedFrom"] == [{"@id": "ark:99999/b"}]
    assert "generatedBy" not in graph["ark:99999/c"]
    assert "derivedFrom" not in graph["ark:99999/a"]


def test_prov_activity_traverses_as_a_computation():
    """A `prov:Activity` with no EVI type still pulls in what it used."""
    crate = _crate(
        _entity("ark:99999/out", generatedBy=[{"@id": "ark:99999/receipt"}]),
        {"@id": "ark:99999/receipt", "@type": "prov:Activity", "name": "receipt",
         "description": "", "usedDataset": [{"@id": "ark:99999/in"}]},
        _entity("ark:99999/in"),
    )
    graph = evidence.build(crate)["@graph"]

    assert "ark:99999/receipt" in graph
    assert graph["ark:99999/receipt"]["usedDataset"] == [{"@id": "ark:99999/in"}]
    assert "ark:99999/in" in graph, "the activity's input must be reached"


def test_generated_by_emitted_as_single_reference():
    crate = _crate(
        _entity("ark:99999/out", generatedBy=[{"@id": "ark:99999/comp"}]),
        {"@id": "ark:99999/comp", "@type": ["prov:Activity", "https://w3id.org/EVI#Computation"],
         "name": "comp", "description": "", "usedDataset": [{"@id": "ark:99999/in"}]},
        _entity("ark:99999/in"),
    )
    graph = evidence.build(crate)["@graph"]
    assert graph["ark:99999/out"]["generatedBy"] == {"@id": "ark:99999/comp"}


def test_upstream_cannot_walk_these_crates():
    """The capability is real: upstream stops where this builder continues."""
    gt = pytest.importorskip("fairscape_graph_tools.evidence_graph_builder")
    sys.path.insert(0, os.path.join(REPO, "artifacts", "tests"))
    from test_evidence_parity import CaptureSink, DictSource

    crate = _crate(
        _entity("ark:99999/c", derivedFrom=[{"@id": "ark:99999/b"}]),
        _entity("ark:99999/b", derivedFrom=[{"@id": "ark:99999/a"}]),
        _entity("ark:99999/a"),
    )
    sink = CaptureSink()
    gt.EvidenceGraphBuilder(DictSource(dict(crate.index)), sink).build(
        crate.root_id, owner_email="local")

    theirs = set(sink.graph.graph or {})
    mine = set(evidence.build(crate)["@graph"])
    assert "ark:99999/a" in mine and "ark:99999/a" not in theirs
    assert mine > theirs


# -- PROV-only crates -----------------------------------------------------
#
# No EVI vocabulary anywhere: the root is a plain schema.org `Dataset`, the
# entities are `prov:Entity`, the activities `prov:Activity`, and every edge
# is spelled the PROV way. Nothing declares `EVI:outputs`.

PROV_NS = "http://www.w3.org/ns/prov#"


def prov_only_crate(prefix="prov:"):
    """raw -> clean -> clean.csv -> train -> model.pkl, spelled entirely in PROV.

    `prefix` switches between the prefixed spelling and full IRIs, which are
    the same terms and must read the same.
    """
    ent, act = f"{prefix}Entity", f"{prefix}Activity"
    gen, used = f"{prefix}wasGeneratedBy", f"{prefix}used"
    return Crate({"@graph": [
        {"@id": "ro-crate-metadata.json", "@type": "CreativeWork",
         "about": {"@id": "./"}},
        {"@id": "./", "@type": "Dataset", "name": "prov-only study",
         "description": "no EVI anywhere"},
        {"@id": "raw.csv", "@type": ["File", ent], "name": "raw"},
        {"@id": "#clean", "@type": act, "name": "clean",
         used: [{"@id": "raw.csv"}]},
        {"@id": "clean.csv", "@type": ["File", ent], "name": "clean.csv",
         gen: {"@id": "#clean"}},
        {"@id": "#train", "@type": act, "name": "train",
         used: [{"@id": "clean.csv"}]},
        {"@id": "model.pkl", "@type": ["File", ent], "name": "model",
         gen: {"@id": "#train"}},
    ]}, path=None)


@pytest.mark.parametrize("prefix", ["prov:", PROV_NS])
def test_prov_only_crate_derives_its_own_outputs(prefix):
    """`model.pkl` is the only thing nothing consumed; `raw.csv` the only
    thing nothing produced. The root is neither."""
    crate = prov_only_crate(prefix)
    inputs, out = outputs.calculate(crate.graph)
    assert out == [{"@id": "model.pkl"}]
    assert inputs == [{"@id": "raw.csv"}]


@pytest.mark.parametrize("prefix", ["prov:", PROV_NS])
def test_prov_only_crate_walks_the_whole_chain(prefix):
    """The whole point: a crate with no EVI vocabulary must not render as one
    lonely node."""
    crate = prov_only_crate(prefix)
    graph = evidence.build(crate)["@graph"]

    assert set(graph) == {"./", "raw.csv", "#clean", "clean.csv",
                          "#train", "model.pkl"}
    assert graph["model.pkl"]["generatedBy"] == {"@id": "#train"}
    assert graph["#train"]["usedDataset"] == [{"@id": "clean.csv"}]
    assert graph["./"]["hasOutputs"] == [{"@id": "model.pkl"}]


def test_plain_dataset_root_is_recognized_as_the_crate_root():
    """A PROV-only root carries no `EVI#ROCrate` type, so the crate root is
    identified by the descriptor's `about`, not by `@type`."""
    crate = prov_only_crate()
    from fairscape_artifacts.crate import is_rocrate

    assert not is_rocrate(crate.root)
    assert crate.root_id == "./"
    # Rooted there, the graph starts from what the crate produced...
    assert "model.pkl" in evidence.build(crate)["@graph"]
    # ...but an explicit node still roots the walk at that node.
    assert set(evidence.build(crate, node_id="clean.csv")["@graph"]) == {
        "clean.csv", "#clean", "raw.csv"}


def test_root_is_never_its_own_output():
    """A plain `Dataset` root would otherwise match the dataset scan and end
    up listed among the crate's own outputs."""
    crate = prov_only_crate()
    _, out = outputs.calculate(crate.graph)
    assert {"@id": "./"} not in out


def test_prov_only_bare_term_spellings():
    """A crate whose @context maps the PROV terms bare (`used`,
    `wasGeneratedBy`) reads the same as the prefixed form."""
    crate = Crate({"@graph": [
        {"@id": "ro-crate-metadata.json", "@type": "CreativeWork",
         "about": {"@id": "./"}},
        {"@id": "./", "@type": "Dataset", "name": "bare terms", "description": "d"},
        {"@id": "in", "@type": "prov:Entity", "name": "in"},
        {"@id": "#act", "@type": "prov:Activity", "name": "act",
         "used": [{"@id": "in"}]},
        {"@id": "out", "@type": "prov:Entity", "name": "out",
         "wasGeneratedBy": {"@id": "#act"}},
    ]}, path=None)

    assert used_dataset_ids(crate.index["#act"]) == ["in"]
    assert provenance_edge(crate.index["out"]) == ("generatedBy", ["#act"])
    assert set(evidence.build(crate)["@graph"]) == {"./", "out", "#act", "in"}


def test_evi_spelling_still_wins_over_prov():
    """Hybrid crates (EVI types plus empty PROV mirrors) are the common real
    shape; the EVI reading must not be displaced by the PROV fallback."""
    node = {"@id": "d", "@type": ["prov:Entity", "https://w3id.org/EVI#Dataset"],
            "generatedBy": [{"@id": "comp"}], "prov:wasGeneratedBy": []}
    assert evi_type(node) == "Dataset"
    assert provenance_edge(node) == ("generatedBy", ["comp"])

    activity = {"@id": "c", "@type": ["prov:Activity", "https://w3id.org/EVI#Computation"],
                "usedDataset": [{"@id": "x"}], "prov:used": [{"@id": "y"}]}
    assert used_dataset_ids(activity) == ["x"]
