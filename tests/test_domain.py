"""Domain-layer evidence graphs over CPM-style crates.

Such a crate describes one computation twice: the *backbone* CPM standardizes
(connectors and receipt activities chaining organizations together) and the
*domain layer* — what actually ran, in each organization's own terms. The two
are joined only by `prov:specializationOf`, which points from the detailed
domain entity at its backbone stand-in, and the ordinary walk never follows
it. `build_domain` swaps the backbone entities for their specializations;
a domain entity with no provenance of its own inherits its generalization's.

The fixture is the CPM shape in miniature — two organizations, one transfer:

    sender org    raw -> tiles -> rois -> hdf5 --specializationOf--> conn
                  conn generatedBy prep (the mainActivity)
    receiver org  receipt used conn; extin generatedBy receipt
                  local --specializationOf--> extin  (bare: no provenance of
                  its own); train used local; model generatedBy train
"""

from __future__ import annotations

import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO, "artifacts"))

from fairscape_artifacts import evidence  # noqa: E402
from fairscape_artifacts.crate import Crate  # noqa: E402
from fairscape_artifacts.evidence import SpecializationView  # noqa: E402

EVI_OUTPUTS = "https://w3id.org/EVI#outputs"

RAW, TILES, ROIS, HDF5 = "#raw", "#tiles", "#rois", "#hdf5"
CONN, PREP = "#conn", "#prep"
RECEIPT, EXTIN, LOCAL, TRAIN, MODEL = "#receipt", "#extin", "#local", "#train", "#model"

DS = ["prov:Entity", "https://w3id.org/EVI#Dataset"]
COMP = ["prov:Activity", "https://w3id.org/EVI#Computation"]


@pytest.fixture
def crate():
    return Crate({"@graph": [
        {"@id": "ro-crate-metadata.json", "@type": "CreativeWork",
         "about": {"@id": "./"}},
        {"@id": "./", "@type": ["Dataset", "https://w3id.org/EVI#ROCrate"],
         "name": "two-org study", "description": "d",
         EVI_OUTPUTS: [{"@id": MODEL}]},
        # sender org — domain layer
        {"@id": RAW, "@type": DS, "name": "raw"},
        {"@id": TILES, "@type": COMP, "name": "tiles",
         "usedDataset": [{"@id": RAW}]},
        {"@id": ROIS, "@type": DS, "name": "rois",
         "generatedBy": [{"@id": TILES}]},
        {"@id": HDF5, "@type": DS, "name": "hdf5",
         "derivedFrom": [{"@id": ROIS}],
         "prov:specializationOf": [{"@id": CONN}]},
        # sender org — backbone
        {"@id": PREP, "@type": COMP, "name": "prep",
         "additionalType": "cpm:mainActivity"},
        {"@id": CONN, "@type": DS, "name": "conn",
         "additionalType": "cpm:senderConnector",
         "generatedBy": [{"@id": PREP}]},
        # receiver org — backbone
        {"@id": RECEIPT, "@type": "prov:Activity", "name": "receipt",
         "additionalType": "cpm:receiptActivity",
         "prov:used": [{"@id": CONN}]},
        {"@id": EXTIN, "@type": DS, "name": "extin",
         "additionalType": "cpm:externalInput",
         "generatedBy": [{"@id": RECEIPT}],
         "derivedFrom": [{"@id": CONN}]},
        # receiver org — domain layer
        {"@id": LOCAL, "@type": DS, "name": "local",
         "prov:specializationOf": [{"@id": EXTIN}]},
        {"@id": TRAIN, "@type": COMP, "name": "train",
         "usedDataset": [{"@id": LOCAL}]},
        {"@id": MODEL, "@type": DS, "name": "model",
         "generatedBy": [{"@id": TRAIN}]},
    ]}, path=None)


# -- the backbone view ----------------------------------------------------

def test_prov_used_is_read_as_used_dataset(crate):
    """A receipt activity states only `prov:used`; the walk must follow it and
    project it as `usedDataset`, the field the viewer draws."""
    graph = evidence.build(crate, node_id=EXTIN)["@graph"]
    assert graph[RECEIPT]["usedDataset"] == [{"@id": CONN}]
    assert CONN in graph


def test_derived_from_is_a_fallback_only(crate):
    """An entity with generatedBy ignores derivedFrom; one without follows it."""
    graph = evidence.build(crate, node_id=HDF5)["@graph"]
    assert graph[HDF5]["derivedFrom"] == [{"@id": ROIS}]
    assert set(graph) == {HDF5, ROIS, TILES, RAW}

    # extin has both: only the generatedBy edge is projected.
    graph = evidence.build(crate, node_id=EXTIN)["@graph"]
    assert graph[EXTIN]["generatedBy"] == {"@id": RECEIPT}
    assert "derivedFrom" not in graph[EXTIN]


def test_backbone_graph_stops_at_bare_specialized_entity(crate):
    """Without the domain view, `local` carries no provenance — a leaf."""
    assert set(evidence.build(crate, node_id=MODEL)["@graph"]) == {MODEL, TRAIN, LOCAL}


# -- the domain view ------------------------------------------------------

def test_domain_graph_expands_connectors_to_specializations(crate):
    graph = evidence.build_domain(crate, node_id=MODEL)

    # The full two-org chain, with backbone entities swapped for their
    # domain specializations (conn -> hdf5, extin -> local).
    assert set(graph["@graph"]) == {MODEL, TRAIN, LOCAL, RECEIPT, HDF5,
                                    ROIS, TILES, RAW}
    # local inherited extin's generatedBy…
    assert graph["@graph"][LOCAL]["generatedBy"] == {"@id": RECEIPT}
    # …and the receipt's used-ref to the connector resolves to hdf5.
    assert graph["@graph"][RECEIPT]["usedDataset"] == [{"@id": HDF5}]
    assert graph["@graph"][HDF5]["derivedFrom"] == [{"@id": ROIS}]
    assert "evidence-graph-domain" in graph["@id"] or graph["@id"].endswith("-domain")
    # Condensation is off by default for domain graphs.
    assert graph["condensation_stats"]["condensed"] is False


def test_domain_graph_from_the_crate_root(crate):
    """Rooted at the crate, it reaches the same chain through the outputs."""
    graph = evidence.build_domain(crate)["@graph"]
    assert {RECEIPT, HDF5, ROIS, TILES, RAW} <= set(graph)


def test_unspecialized_connector_is_kept(crate):
    """A connector nothing specializes (the chain start) stays in the graph."""
    view = SpecializationView(dict(crate.index))
    assert view.get(RECEIPT)["prov:used"] == [{"@id": HDF5}]
    # conn's own generatedBy is untouched by the view (prep has no spec).
    assert view.get(CONN)["generatedBy"] == [{"@id": PREP}]


def test_view_never_substitutes_a_node_into_itself(crate):
    """hdf5's generalization chain must not hand hdf5 a self-reference."""
    node = SpecializationView(dict(crate.index)).get(HDF5)
    assert HDF5 not in [r["@id"] for r in node.get("derivedFrom", [])]


def test_view_leaves_the_crate_untouched(crate):
    """The rewrite is a view: nothing is written back to the loaded crate."""
    before = crate.index[RECEIPT]["prov:used"]
    SpecializationView(dict(crate.index)).get(RECEIPT)
    evidence.build_domain(crate, node_id=MODEL)
    assert crate.index[RECEIPT]["prov:used"] == before
    assert "generatedBy" not in crate.index[LOCAL]
