"""References that point into another crate.

A reference in a crate is a bare `@id`. Nothing in it says which crate holds
the entity, so an entity described next door reads as a dangling id and the
evidence graph stops there. Three ways the entity can be found:

* it is in a **constituent** of this crate — a release root and its parts;
* it is in a crate this one **links to** (see `test_linked_crates.py`);
* it is in a **sibling**, which neither crate points at. Two constituents of
  the same release referring to each other's entities is the real case: in
  the CM4AI release the perturb-seq cell-atlas crate's processing computation
  consumes 90 datasets described only in the SRA crate beside it. Nothing
  intrinsic connects them, so the caller supplies the candidates as `pool`.

Also here: a node a `DatasetGroup` replaced must read as summarized, not as
missing, wherever something still names it.
"""

import json
import os

import pytest

from fairscape_artifacts import evidence
from fairscape_artifacts.crate import Crate

EVI = "https://w3id.org/EVI#"

SRA_ROOT = "ark:59853/rocrate-sra"
ATLAS_ROOT = "ark:59853/rocrate-atlas"
ATLAS_COMP = "ark:59853/computation-atlas-processing"
ATLAS_OUT = "ark:59853/dataset-atlas-aggregated"
RELEASE_ROOT = "ark:59853/rocrate-release"


def _write(path, graph):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as handle:
        json.dump({"@graph": graph}, handle)
    return path


def _molecules(count, prefix="ark:59853/dataset-molecule-"):
    return [f"{prefix}{i}" for i in range(count)]


def _sra_graph(molecule_ids):
    """A crate describing raw molecule files and nothing else."""
    return [
        {"@id": "ro-crate-metadata.json", "@type": "CreativeWork", "about": {"@id": SRA_ROOT}},
        {"@id": SRA_ROOT, "@type": ["Dataset", EVI + "ROCrate"], "name": "SRA raw data",
         "hasPart": [{"@id": m} for m in molecule_ids],
         EVI + "outputs": [{"@id": m} for m in molecule_ids]},
    ] + [
        {"@id": m, "@type": ["prov:Entity", EVI + "Dataset"], "name": f"molecule {i}",
         "format": "h5", "generatedBy": []}
        for i, m in enumerate(molecule_ids)
    ]


def _atlas_graph(molecule_ids):
    """A crate whose computation consumes the SRA molecules by id alone —
    no stub, no pointer, nothing saying where they are described."""
    return [
        {"@id": "ro-crate-metadata.json", "@type": "CreativeWork", "about": {"@id": ATLAS_ROOT}},
        {"@id": ATLAS_ROOT, "@type": ["Dataset", EVI + "ROCrate"], "name": "Cell atlas",
         "hasPart": [{"@id": ATLAS_COMP}, {"@id": ATLAS_OUT}],
         EVI + "outputs": [{"@id": ATLAS_OUT}]},
        {"@id": ATLAS_COMP, "@type": ["prov:Activity", EVI + "Computation"],
         "name": "Cell atlas processing",
         "usedDataset": [{"@id": m} for m in molecule_ids],
         "generated": [{"@id": ATLAS_OUT}]},
        {"@id": ATLAS_OUT, "@type": ["prov:Entity", EVI + "Dataset"], "name": "aggregated.h5ad",
         "format": "h5ad", "generatedBy": [{"@id": ATLAS_COMP}]},
    ]


@pytest.fixture
def siblings(tmp_path):
    """Two crates side by side, neither pointing at the other."""
    ids = _molecules(3)
    sra = _write(str(tmp_path / "sra" / "ro-crate-metadata.json"), _sra_graph(ids))
    atlas = _write(str(tmp_path / "cell-atlas" / "ro-crate-metadata.json"), _atlas_graph(ids))
    return Crate.load(atlas), sra, ids


def _dangling(graph):
    return sorted(i for i, n in graph["@graph"].items()
                  if isinstance(n, dict) and "error" in n)


# -- siblings ------------------------------------------------------------------

def test_without_a_pool_a_siblings_entities_are_dangling(siblings):
    atlas, _, ids = siblings
    graph = evidence.build(atlas, condense_threshold=None)
    assert _dangling(graph) == sorted(ids)


def test_a_pool_resolves_them(siblings):
    atlas, sra_path, ids = siblings
    graph = evidence.build(atlas, condense_threshold=None, pool=[sra_path])
    assert _dangling(graph) == []
    for molecule_id in ids:
        assert graph["@graph"][molecule_id]["name"].startswith("molecule")


def test_a_node_from_another_crate_says_where_it_came_from(siblings):
    atlas, sra_path, ids = siblings
    graph = evidence.build(atlas, condense_threshold=None, pool=[sra_path])
    assert graph["@graph"][ids[0]]["crate"] == {"@id": SRA_ROOT, "name": "SRA raw data"}
    # the crate's own nodes are not annotated
    assert "crate" not in graph["@graph"][ATLAS_OUT]


def test_the_pool_takes_loaded_crates_too(siblings):
    atlas, sra_path, ids = siblings
    graph = evidence.build(atlas, condense_threshold=None, pool=[Crate.load(sra_path)])
    assert _dangling(graph) == []


def test_an_unreadable_pool_entry_is_skipped(siblings, tmp_path, capsys):
    atlas, sra_path, ids = siblings
    graph = evidence.build(atlas, condense_threshold=None,
                           pool=[str(tmp_path / "gone" / "ro-crate-metadata.json"), sra_path])
    assert _dangling(graph) == []
    assert "gone" in capsys.readouterr().err


def test_the_pool_never_overrides_a_crate_that_was_pointed_at(tmp_path):
    """A constituent's copy wins; the pool only fills what is still missing."""
    ids = _molecules(2)
    _write(str(tmp_path / "release" / "sra" / "ro-crate-metadata.json"), _sra_graph(ids))
    decoy = _write(str(tmp_path / "decoy" / "ro-crate-metadata.json"),
                   [{"@id": "ro-crate-metadata.json", "@type": "CreativeWork",
                     "about": {"@id": "ark:59853/rocrate-decoy"}},
                    {"@id": "ark:59853/rocrate-decoy", "@type": ["Dataset", EVI + "ROCrate"],
                     "name": "Decoy", "hasPart": [{"@id": ids[0]}]},
                    {"@id": ids[0], "@type": ["prov:Entity", EVI + "Dataset"],
                     "name": "WRONG", "format": "h5"}])
    release = _write(str(tmp_path / "release" / "ro-crate-metadata.json"), [
        {"@id": "ro-crate-metadata.json", "@type": "CreativeWork", "about": {"@id": RELEASE_ROOT}},
        {"@id": RELEASE_ROOT, "@type": ["Dataset", EVI + "ROCrate"], "name": "Release",
         "hasPart": [{"@id": SRA_ROOT}], EVI + "outputs": [{"@id": ids[0]}]},
        {"@id": SRA_ROOT, "@type": ["Dataset", EVI + "ROCrate"], "name": "SRA raw data",
         "ro-crate-metadata": "sra/ro-crate-metadata.json"},
    ])
    graph = evidence.build(Crate.load(release), condense_threshold=None, pool=[decoy])
    assert graph["@graph"][ids[0]]["name"] == "molecule 0"


# -- constituents --------------------------------------------------------------

def test_a_constituents_entities_resolve_without_any_pool(tmp_path):
    """A release root refers to what its own parts describe; the parts are
    reachable by pointer, so nothing extra is needed."""
    ids = _molecules(2)
    _write(str(tmp_path / "release" / "sra" / "ro-crate-metadata.json"), _sra_graph(ids))
    release = _write(str(tmp_path / "release" / "ro-crate-metadata.json"), [
        {"@id": "ro-crate-metadata.json", "@type": "CreativeWork", "about": {"@id": RELEASE_ROOT}},
        {"@id": RELEASE_ROOT, "@type": ["Dataset", EVI + "ROCrate"], "name": "Release",
         "hasPart": [{"@id": SRA_ROOT}, {"@id": "ark:59853/dataset-example"}],
         EVI + "outputs": [{"@id": "ark:59853/dataset-example"}]},
        {"@id": SRA_ROOT, "@type": ["Dataset", EVI + "ROCrate"], "name": "SRA raw data",
         "ro-crate-metadata": "sra/ro-crate-metadata.json"},
        # an example excerpt of one of the constituent's files
        {"@id": "ark:59853/dataset-example", "@type": ["prov:Entity", EVI + "Dataset"],
         "name": "example excerpt", "format": "h5",
         "derivedFrom": [{"@id": ids[0]}]},
    ])
    graph = evidence.build(Crate.load(release), condense_threshold=None)
    assert _dangling(graph) == []
    assert graph["@graph"][ids[0]]["crate"]["@id"] == SRA_ROOT


# -- condensation --------------------------------------------------------------

def _fan_in_graph(molecule_ids):
    """One crate whose outputs are both the eight raw files and what the
    computation that consumed all eight produced from them."""
    return [
        {"@id": "ro-crate-metadata.json", "@type": "CreativeWork", "about": {"@id": SRA_ROOT}},
        {"@id": SRA_ROOT, "@type": ["Dataset", EVI + "ROCrate"], "name": "Fan-in",
         "hasPart": [{"@id": m} for m in molecule_ids] + [{"@id": ATLAS_COMP}, {"@id": ATLAS_OUT}],
         EVI + "outputs": [{"@id": m} for m in molecule_ids] + [{"@id": ATLAS_OUT}]},
        {"@id": ATLAS_COMP, "@type": ["prov:Activity", EVI + "Computation"], "name": "processing",
         "usedDataset": [{"@id": m} for m in molecule_ids],
         "generated": [{"@id": ATLAS_OUT}]},
        {"@id": ATLAS_OUT, "@type": ["prov:Entity", EVI + "Dataset"], "name": "aggregated.h5ad",
         "format": "h5ad", "generatedBy": [{"@id": ATLAS_COMP}]},
    ] + [
        {"@id": m, "@type": ["prov:Entity", EVI + "Dataset"], "name": f"molecule {i}",
         "format": "h5", "generatedBy": []}
        for i, m in enumerate(molecule_ids)
    ]


def test_a_grouped_member_reads_as_summarized_not_missing(tmp_path):
    """The crate's outputs include the very datasets condensation collapses.

    Before the group replaced them they were projected one by one; after, the
    output list has to name the group instead, or every collapsed member
    would be reported as a node that could not be found.
    """
    ids = _molecules(8)
    path = _write(str(tmp_path / "crate" / "ro-crate-metadata.json"), _fan_in_graph(ids))
    graph = evidence.build(Crate.load(path), condense_threshold=5)
    assert _dangling(graph) == []
    groups = [n for n in graph["@graph"].values()
              if "DatasetGroup" in str(n.get("@type"))]
    assert len(groups) == 1
    assert groups[0]["evi:memberCount"] == 8
    # the outputs name the group, once, in place of the seven it replaced
    output_ids = [r["@id"] for r in graph["outputs"]]
    assert groups[0]["@id"] in output_ids
    assert sum(1 for i in output_ids if i in ids) == 1        # only the representative
    assert graph["condensation_stats"]["condensed"] is True


def test_condensation_off_keeps_every_member(tmp_path):
    ids = _molecules(8)
    path = _write(str(tmp_path / "crate" / "ro-crate-metadata.json"), _fan_in_graph(ids))
    graph = evidence.build(Crate.load(path), condense_threshold=None)
    assert _dangling(graph) == []
    assert all(i in graph["@graph"] for i in ids)


# -- saying so on the page -----------------------------------------------------

def test_the_graph_page_names_the_crates_it_reached_into(siblings):
    """The viewer bundle is a recovered build that ignores the `crate`
    annotation, so the page itself has to say the chain left this crate."""
    from fairscape_artifacts import render

    atlas, sra_path, _ = siblings
    graph = evidence.build(atlas, condense_threshold=None, pool=[sra_path])
    page = render.evidence_graph_html(graph, source="test", generated_at="now")
    assert "This chain leaves this crate" in page
    assert "3</strong> entities described in SRA raw data" in page


def test_a_self_contained_graph_says_nothing(tmp_path):
    ids = _molecules(2)
    path = _write(str(tmp_path / "sra" / "ro-crate-metadata.json"), _sra_graph(ids))
    from fairscape_artifacts import render

    graph = evidence.build(Crate.load(path), condense_threshold=None)
    page = render.evidence_graph_html(graph, source="test", generated_at="now")
    assert "This chain leaves this crate" not in page


# -- what the viewer can reach -------------------------------------------------
#
# The viewer bundle resolves a fixed set of edge fields and expands a
# DatasetGroup through `evi:memberIds`. Both are checked here against the
# bundle's own rules, because it is a recovered build that cannot be changed
# to meet the data half way.

VIEWER_EDGES = ("generatedBy", "derivedFrom", "usedDataset", "usedSoftware",
                "usedSample", "usedInstrument", "usedMLModel", "hasOutputs", "createdBy")


def _ids(value):
    if isinstance(value, dict):
        return [value["@id"]] if value.get("@id") else []
    if isinstance(value, list):
        return [v["@id"] for v in value if isinstance(v, dict) and v.get("@id")]
    return []


def _viewer_reachable(graph, expand_groups=True):
    """The nodes the viewer could ever show, by its own two rules.

    It follows `VIEWER_EDGES` on click, and expands a `DatasetGroup` through
    `evi:memberIds` — keeping only ids that are strings, start with `ark:` and
    resolve in the graph, which is exactly the filter in the bundle.
    """
    nodes = graph["@graph"]
    seen, stack = set(), [r["@id"] for r in graph["outputs"] if r.get("@id")]
    while stack:
        nid = stack.pop()
        if nid in seen or nid not in nodes:
            continue
        seen.add(nid)
        node = nodes[nid]
        for field in VIEWER_EDGES:
            stack.extend(_ids(node.get(field)))
        if expand_groups and "DatasetGroup" in str(node.get("@type")):
            stack.extend(m for m in node.get("evi:memberIds") or []
                         if isinstance(m, str) and m.startswith("ark:") and m in nodes)
    return seen


def _fan_in_with_producers(molecule_ids):
    """Eight raw files, each made by its own identically-shaped computation."""
    graph = [
        {"@id": "ro-crate-metadata.json", "@type": "CreativeWork", "about": {"@id": SRA_ROOT}},
        {"@id": SRA_ROOT, "@type": ["Dataset", EVI + "ROCrate"], "name": "Fan-in",
         "hasPart": [{"@id": ATLAS_OUT}], EVI + "outputs": [{"@id": ATLAS_OUT}]},
        {"@id": ATLAS_COMP, "@type": ["prov:Activity", EVI + "Computation"], "name": "processing",
         "usedDataset": [{"@id": m} for m in molecule_ids],
         "generated": [{"@id": ATLAS_OUT}]},
        {"@id": ATLAS_OUT, "@type": ["prov:Entity", EVI + "Dataset"], "name": "aggregated.h5ad",
         "format": "h5ad", "generatedBy": [{"@id": ATLAS_COMP}]},
    ]
    for i, m in enumerate(molecule_ids):
        maker = f"ark:59853/computation-make-{i}"
        graph += [
            {"@id": m, "@type": ["prov:Entity", EVI + "Dataset"], "name": f"molecule {i}",
             "format": "h5", "generatedBy": [{"@id": maker}]},
            {"@id": maker, "@type": ["prov:Activity", EVI + "Computation"],
             "name": f"make {i}", "generated": [{"@id": m}]},
        ]
    return graph


def test_a_group_keeps_its_members_so_the_viewer_can_expand_it(tmp_path):
    """`evi:memberIds` is how the viewer expands a group: it keeps the ids it
    can resolve as the group's children and offers the control only when at
    least one resolved. Dropping the members left it with nothing to show."""
    ids = _molecules(8)
    path = _write(str(tmp_path / "crate" / "ro-crate-metadata.json"), _fan_in_graph(ids))
    graph = evidence.build(Crate.load(path), condense_threshold=5)
    group = next(n for n in graph["@graph"].values() if "DatasetGroup" in str(n.get("@type")))
    members = group["evi:memberIds"]
    assert len(members) == 8
    assert all(m in graph["@graph"] for m in members), "the viewer resolves each member by id"
    assert all(m.startswith("ark:") for m in members), "and keeps only ids starting with ark:"


def test_a_groups_members_carry_no_provenance_of_their_own(tmp_path):
    """Their producers were collapsed with them, and a member pointing back at
    one would be redirected to the group and close a loop."""
    ids = _molecules(8)
    path = _write(str(tmp_path / "crate" / "ro-crate-metadata.json"),
                  _fan_in_with_producers(ids))
    graph = evidence.build(Crate.load(path), condense_threshold=5)
    group = next(n for n in graph["@graph"].values() if "DatasetGroup" in str(n.get("@type")))
    representative = _ids(group["evi:representativeDataset"])[0]
    for member_id in group["evi:memberIds"]:
        if member_id == representative:
            continue
        member = graph["@graph"][member_id]
        assert not any(field in member for field in VIEWER_EDGES), member_id


def test_a_group_carries_the_representatives_upstream_edge(tmp_path):
    """`evi:representativeDataset` is not an edge the viewer resolves, so
    without this the chain stopped dead at the group."""
    ids = _molecules(8)
    path = _write(str(tmp_path / "crate" / "ro-crate-metadata.json"),
                  _fan_in_with_producers(ids))
    graph = evidence.build(Crate.load(path), condense_threshold=5)
    group = next(n for n in graph["@graph"].values() if "DatasetGroup" in str(n.get("@type")))
    representative = graph["@graph"][_ids(group["evi:representativeDataset"])[0]]
    assert _ids(group.get("generatedBy")) == _ids(representative.get("generatedBy"))
    assert _ids(group["generatedBy"])[0] in graph["@graph"]


def test_nothing_in_the_graph_is_stranded(tmp_path):
    """Every node the graph carries can be arrived at in the viewer, whether
    by following an edge or by expanding the group that holds it."""
    ids = _molecules(8)
    path = _write(str(tmp_path / "crate" / "ro-crate-metadata.json"),
                  _fan_in_with_producers(ids))
    graph = evidence.build(Crate.load(path), condense_threshold=5)
    assert set(graph["@graph"]) - _viewer_reachable(graph) == set()


def test_without_expanding_the_group_only_its_members_are_out_of_sight(tmp_path):
    """Which is the point of condensing them: the chain past the group still
    has to be walkable without opening it."""
    ids = _molecules(8)
    path = _write(str(tmp_path / "crate" / "ro-crate-metadata.json"),
                  _fan_in_with_producers(ids))
    graph = evidence.build(Crate.load(path), condense_threshold=5)
    group = next(n for n in graph["@graph"].values() if "DatasetGroup" in str(n.get("@type")))
    unopened = set(graph["@graph"]) - _viewer_reachable(graph, expand_groups=False)
    assert unopened <= set(group["evi:memberIds"])
    # and the representative's producer, which the group inherited, is not
    assert _ids(group["generatedBy"])[0] not in unopened


def test_a_graph_without_groups_is_wholly_reachable(siblings):
    atlas, sra_path, _ = siblings
    graph = evidence.build(atlas, condense_threshold=None, pool=[sra_path])
    assert set(graph["@graph"]) == _viewer_reachable(graph)
