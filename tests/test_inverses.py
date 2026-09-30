"""`inverses`: the rule on small graphs, the pair table against the ontology,
and parity with the old CLI on the fixture crates while it is importable.

The rule was ported from `fairscape_cli.entailments.inverse`, which is being
retired with the rest of that CLI. The in-memory cases below hold the rule in
place on their own; the CLI comparison and the ontology check are extra
assurance that skip themselves when their sources are gone.
"""

from __future__ import annotations

import copy
import json
import os
import shutil
import sys

import pytest

from conftest import REPO

sys.path.insert(0, os.path.join(REPO, "fairscape-cli", "src"))

from fairscape_artifacts import inverses  # noqa: E402


def _sibling(*parts):
    """A path under the checkout that holds the other fairscape repos: the
    directory above this one, or a `fairscape/` folder inside it."""
    for root in (REPO, os.path.join(REPO, "fairscape")):
        candidate = os.path.join(root, *parts)
        if os.path.exists(candidate):
            return candidate
    return os.path.join(REPO, *parts)


FIXTURES = ["fairscape_conversion/examples/wdl-variant-calling",
            "fairscape_conversion/examples/snakemake-variant-calling",
            "CM4AIReleases/CM4AIJuneRelease"]

CLI_ONTOLOGY = _sibling("fairscape-cli", "src", "fairscape_cli", "entailments", "evi.xml")
sys.path.insert(0, _sibling("fairscape-cli", "src"))


def _graph(*nodes):
    return [dict(n) for n in nodes]


def test_adds_the_missing_side():
    graph = _graph(
        {"@id": "c", "@type": "Computation", "usedDataset": [{"@id": "d"}]},
        {"@id": "d", "@type": "Dataset", "generatedBy": {"@id": "c"}},
    )
    added = inverses.apply(graph)
    assert sorted(added) == [("c", "generated", "d"), ("d", "datasetUsedBy", "c")]
    assert graph[0]["generated"] == [{"@id": "d"}]
    assert graph[1]["datasetUsedBy"] == [{"@id": "c"}]


def test_existing_values_are_kept_and_never_duplicated():
    graph = _graph(
        {"@id": "c", "usedDataset": [{"@id": "d"}, {"@id": "e"}],
         "generated": {"@id": "x"}},
        {"@id": "d", "datasetUsedBy": [{"@id": "c"}]},
        {"@id": "e"},
        {"@id": "x", "generatedBy": {"@id": "c"}},
    )
    added = inverses.apply(graph)
    assert added == [("e", "datasetUsedBy", "c")]
    assert graph[0]["generated"] == {"@id": "x"}      # already consistent, untouched
    assert graph[1]["datasetUsedBy"] == [{"@id": "c"}]
    assert inverses.apply(graph) == []                 # idempotent


def test_scalar_becomes_list_when_a_second_link_arrives():
    graph = _graph(
        {"@id": "c", "generated": {"@id": "d1"}},
        {"@id": "d1"},
        {"@id": "d2", "generatedBy": {"@id": "c"}},
    )
    inverses.apply(graph)
    assert graph[0]["generated"] == [{"@id": "d1"}, {"@id": "d2"}]
    assert graph[1]["generatedBy"] == [{"@id": "c"}]


def test_links_to_undescribed_ids_and_plain_strings_are_left_alone():
    graph = _graph(
        {"@id": "d", "generatedBy": {"@id": "ark:elsewhere"}, "usedDataset": "d2"},
        {"@id": "d2"},
    )
    assert inverses.apply(graph) == []
    assert "datasetUsedBy" not in graph[1]


def test_prefixed_spelling_is_mirrored_not_mixed():
    graph = _graph(
        {"@id": "c"},
        {"@id": "d", "evi:generatedBy": {"@id": "c"}},
    )
    assert inverses.apply(graph) == [("c", "evi:generated", "d")]
    assert "generated" not in graph[0]


def test_write_rewrites_only_when_something_changed(tmp_path):
    crate = {"@context": {}, "@graph": [
        {"@id": "ro-crate-metadata.json", "about": {"@id": "./"}},
        {"@id": "./", "@type": ["Dataset", "ROCrate"], "hasPart": [{"@id": "d"}]},
        {"@id": "c", "@type": "Computation", "usedDataset": [{"@id": "d"}]},
        {"@id": "d", "@type": "Dataset", "author": None},
    ]}
    path = tmp_path / "ro-crate-metadata.json"
    path.write_text(json.dumps(crate))
    ok, message = inverses.write(str(tmp_path))
    assert ok and message.startswith("Added 1 inverse link")
    data = json.loads(path.read_text())
    assert data["@graph"][3]["datasetUsedBy"] == [{"@id": "c"}]
    assert "author" in data["@graph"][3]         # nulls are preserved, not pruned
    before = path.stat().st_mtime_ns
    ok, message = inverses.write(str(path))
    assert ok and message.startswith("No inverse links missing")
    assert path.stat().st_mtime_ns == before


# -- the pair table against the ontology -------------------------------------

def test_pair_table_matches_the_evi_ontology():
    rdflib = pytest.importorskip("rdflib")
    if not os.path.exists(CLI_ONTOLOGY):
        pytest.skip("EVI ontology not available (fairscape-cli checkout absent)")
    g = rdflib.Graph()
    g.parse(CLI_ONTOLOGY, format="xml")
    rows = g.query("PREFIX owl: <http://www.w3.org/2002/07/owl#> "
                   "SELECT ?a ?b WHERE { ?a owl:inverseOf ?b }")
    pairs = set()
    for a, b in rows:
        a, b = str(a), str(b)
        assert a.startswith(inverses.EVI_NAMESPACE) and b.startswith(inverses.EVI_NAMESPACE)
        pairs.add(tuple(sorted((a[len(inverses.EVI_NAMESPACE):],
                                b[len(inverses.EVI_NAMESPACE):]))))
    assert pairs == set(inverses.INVERSE_PAIRS)


# -- parity with the old CLI --------------------------------------------------

@pytest.mark.parametrize("name", FIXTURES)
def test_parity_with_cli(name, tmp_path):
    cli = pytest.importorskip(
        "fairscape_cli.entailments.inverse",
        reason="fairscape-cli not importable (it is being retired)")
    src = _sibling(name, "ro-crate-metadata.json")
    if not os.path.exists(src):
        pytest.skip(f"fixture missing: {name}")
    if not os.path.exists(CLI_ONTOLOGY):
        pytest.skip("EVI ontology not available")

    with open(src, encoding="utf-8") as handle:
        original = json.load(handle)
    # strip one side so both implementations have work to do
    stripped = copy.deepcopy(original)
    for node in stripped["@graph"]:
        for key in ("generated", "datasetUsedBy", "softwareUsedBy", "derivedTo"):
            node.pop(key, None)

    cli_dir = tmp_path / "cli"
    cli_dir.mkdir()
    cli_file = cli_dir / "ro-crate-metadata.json"
    cli_file.write_text(json.dumps(stripped))
    import pathlib
    assert cli.augment_rocrate_with_inverses(pathlib.Path(cli_dir), pathlib.Path(CLI_ONTOLOGY))
    cli_graph = json.loads(cli_file.read_text())["@graph"]

    ours = copy.deepcopy(stripped)
    inverses.apply(ours["@graph"])

    def normal(graph):
        # the CLI prunes nulls on write and the order of appended links can
        # differ; compare the set of (entity, key, ids) for the paired keys
        out = {}
        for node in graph:
            for key in list(inverses.INVERSE_OF) + ["evi:" + k for k in inverses.INVERSE_OF]:
                if key in node:
                    out[(node["@id"], key)] = frozenset(inverses._ids(node[key]))
        return out

    assert normal(ours["@graph"]) == normal(cli_graph)
