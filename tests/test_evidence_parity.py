"""Parity: the lite evidence builder vs `fairscape_graph_tools`.

The upstream `EvidenceGraphBuilder` is the oracle. It drives the same
algorithm through storage ports, so feeding both an identical in-memory index
must produce an identical graph — same nodes, same edges, same condensation
stats.

Parity is asserted only on crates where the two are *supposed* to agree. The
lite builder deliberately goes further on PROV-converted crates (see
`test_prov_support.py`): it reads `prov:Activity` / `prov:Entity` nodes that
upstream ignores, and falls back to `derivedFrom` where upstream stops. On a
crate exercising those paths the two must differ, and that difference is what
the other module pins down.
"""

from __future__ import annotations

import glob
import json
import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO, "artifacts"))
sys.path.insert(0, os.path.join(REPO, "fairscape_graph_tools", "src"))

from fairscape_artifacts import evidence  # noqa: E402
from fairscape_artifacts.crate import Crate  # noqa: E402

gt_builder = pytest.importorskip(
    "fairscape_graph_tools.evidence_graph_builder",
    reason="fairscape_graph_tools not importable",
)

# Crates whose provenance is spelled the way upstream understands it.
PARITY_CRATES = [
    "wizards/example-wf",
    "wizards/single-cell-breast-cancer",
    "wizards/bigidea",
    "wizards/thingslee",
]


class DictSource:
    """The `GraphSource` port over a plain `{@id: node}` index."""

    def __init__(self, index):
        self.index = index

    def find_entity(self, ark_id):
        return self.index.get(ark_id)

    def find_many(self, ark_ids):
        return {i: self.index[i] for i in ark_ids if i in self.index}

    def find_dataset_stats(self, ark_ids):
        return {}

    def build_full_graph(self, rocrate_id):
        return list(self.index.values())


class CaptureSink:
    """The `ResultSink` port, keeping the graph instead of storing it."""

    def __init__(self):
        self.graph = None

    def persist_evidence_graph(self, evidence_graph, node_id):
        self.graph = evidence_graph
        return evidence_graph.guid

    def persist_condensed(self, *a, **k):
        raise NotImplementedError

    def persist_aeg(self, *a, **k):
        raise NotImplementedError


def _upstream(crate, outputs_patch):
    index = dict(crate.index)
    if outputs_patch:
        root = dict(crate.root)
        root["https://w3id.org/EVI#outputs"] = outputs_patch
        index[crate.root_id] = root
    sink = CaptureSink()
    gt_builder.EvidenceGraphBuilder(DictSource(index), sink).build(
        crate.root_id, owner_email="local")
    return json.loads(sink.graph.model_dump_json(by_alias=True))


def _crate_path(name):
    return os.path.join(REPO, name, "ro-crate-metadata.json")


@pytest.mark.parametrize("name", PARITY_CRATES)
def test_matches_upstream_builder(name):
    path = _crate_path(name)
    if not os.path.exists(path):
        pytest.skip(f"fixture missing: {name}")

    crate = Crate.load(path)

    # Both sides start from the same declared outputs; deriving them is the
    # lite builder's own feature and is covered separately.
    from fairscape_artifacts import outputs as io

    _, declared = io.stored(crate.root)
    patch = declared or io.calculate(crate.graph)[1]

    mine = evidence.build(crate)
    theirs = _upstream(crate, patch)

    assert mine["condensation_stats"] == theirs["condensation_stats"]
    assert set(mine["@graph"]) == set(theirs["@graph"])
    assert {r["@id"] for r in mine["outputs"]} == {r["@id"] for r in theirs["outputs"]}
    for node_id, node in mine["@graph"].items():
        assert node == theirs["@graph"][node_id], f"node mismatch: {node_id}"


def test_graph_id_derivation():
    assert evidence.graph_id_for("ark:59853/x") == "ark:59853/evidence-graph-x"
    assert evidence.graph_id_for("local-thing") == "local-thing-evidence-graph"
