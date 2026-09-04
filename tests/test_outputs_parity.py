"""`outputs.calculate`: pinned by in-repo goldens, cross-checked against the
old CLI while it still exists.

Which datasets land in `EVI:outputs` decides where the evidence graph starts
its traversal, so the rules have to stay put. They were ported from
`fairscape_cli.entailments.find_outputs.calculate_inputs_outputs`, and that
CLI is being retired — so the goldens here, not the CLI, are the thing that
holds the rules in place. The comparison against the CLI is kept for as long
as it is importable, with the one known divergence stated exactly.

Inputs are additionally emitted in sorted order rather than the CLI's set
order, so that `outputs.write` produces a stable file.

**The known divergence.** The CLI additionally drops from `outputs` every
entity whose `isPartOf` resolves to another entity in the graph, so that
"files inside an expanded directory output" do not each count as a top-level
crate output. In every crate in this corpus that rule fires against the crate
*root* — being part of the crate is what `isPartOf` means for an ordinary
RO-Crate file — and so it deletes every output the crate has: 131 of 131 for
`wizards/bigidea`, 1062 of 1062 for `wizards/thingslee`. A crate with no
outputs has no evidence graph, so this port does not apply the rule.
Containment within a *non-root* entity is the case the rule was written for
and is not exercised by any fixture here; if it ever needs implementing, it
belongs behind that non-root condition.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys

import pytest

from conftest import REPO, repo_path

sys.path.insert(0, os.path.join(REPO, "fairscape-cli", "src"))

from fairscape_artifacts.outputs import calculate  # noqa: E402

GOLDEN = json.loads(
    (open(os.path.join(os.path.dirname(__file__), "outputs-golden.json"))).read())

CRATES = sorted(GOLDEN)


def _graph(name):
    path = repo_path(name, "ro-crate-metadata.json")
    if not os.path.exists(path):
        pytest.skip(f"fixture missing: {name}")
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)["@graph"]


def _digest(refs):
    return hashlib.sha1("\n".join(r["@id"] for r in refs).encode()).hexdigest()[:12]


@pytest.mark.parametrize("name", CRATES)
def test_matches_golden(name):
    """The rules, pinned in this repo — the check that outlives the CLI."""
    inputs, outputs = calculate(_graph(name))
    expected = GOLDEN[name]
    assert (len(inputs), _digest(inputs)) == (expected["inputs"], expected["inputsDigest"])
    assert (len(outputs), _digest(outputs)) == (expected["outputs"], expected["outputsDigest"])


@pytest.mark.parametrize("name", CRATES)
def test_matches_old_cli_modulo_containment(name):
    """Identical to the CLI on inputs, and on outputs once the CLI's
    containment rule is applied to ours — so the port differs in exactly one
    documented way and nothing else has drifted."""
    old = pytest.importorskip(
        "fairscape_cli.entailments.find_outputs",
        reason="fairscape-cli not importable (it is being retired)")
    graph = _graph(name)
    inputs, outputs = calculate(graph)
    old_inputs, old_outputs = old.calculate_inputs_outputs(graph)

    contained = old.extract_contained_entities_from_graph(graph)
    # Inputs compare as sets: the CLI emits them in set-iteration order, which
    # varies per process. This port sorts them so the persisted file is stable.
    assert {r["@id"] for r in inputs} == {r["@id"] for r in old_inputs}
    assert [r for r in outputs if r["@id"] not in contained] == old_outputs


def test_containment_rule_would_empty_real_crates():
    """Why the containment rule is not ported: in this corpus it fires against
    the crate root, and a crate with no outputs has no evidence graph."""
    old = pytest.importorskip(
        "fairscape_cli.entailments.find_outputs",
        reason="fairscape-cli not importable (it is being retired)")
    graph = _graph("wizards/bigidea")
    _, outputs = calculate(graph)
    _, old_outputs = old.calculate_inputs_outputs(graph)

    assert len(outputs) == 131 and old_outputs == []


def test_root_typed_rocrate_is_not_its_own_output():
    """`@type` is read last-element-first, so a `[... , "ROCrate"]` root is not
    collected as a dataset. If it were, every crate would list itself."""
    graph = [
        {"@id": "./", "@type": ["Dataset", "https://w3id.org/EVI#ROCrate"]},
        {"@id": "d1", "@type": "https://w3id.org/EVI#Dataset"},
    ]
    inputs, outputs = calculate(graph)
    assert [r["@id"] for r in outputs] == ["d1"]
    assert [r["@id"] for r in inputs] == ["d1"]


def test_plain_dataset_root_is_not_its_own_output():
    """A PROV-only crate's root is an ordinary `Dataset`, so the type rule
    above cannot exclude it; the metadata descriptor's `about` does."""
    graph = [
        {"@id": "ro-crate-metadata.json", "@type": "CreativeWork",
         "about": {"@id": "./"}},
        {"@id": "./", "@type": "Dataset"},
        {"@id": "d1", "@type": "prov:Entity"},
    ]
    inputs, outputs = calculate(graph)
    assert [r["@id"] for r in outputs] == ["d1"]
    assert [r["@id"] for r in inputs] == ["d1"]


def test_used_but_undefined_dataset_is_an_input():
    graph = [
        {"@id": "c1", "@type": "EVI:Computation",
         "usedDataset": [{"@id": "elsewhere"}]},
    ]
    inputs, outputs = calculate(graph)
    assert [r["@id"] for r in inputs] == ["elsewhere"]
    assert outputs == []


def test_generated_dataset_is_output_only():
    graph = [
        {"@id": "raw", "@type": "EVI:Dataset"},
        {"@id": "c1", "@type": "EVI:Computation", "usedDataset": [{"@id": "raw"}]},
        {"@id": "out", "@type": "EVI:Dataset", "generatedBy": {"@id": "c1"}},
    ]
    inputs, outputs = calculate(graph)
    assert [r["@id"] for r in inputs] == ["raw"]
    assert [r["@id"] for r in outputs] == ["out"]


def test_prov_only_generation_and_usage_are_read():
    """The same rules, spelled entirely in PROV."""
    graph = [
        {"@id": "raw", "@type": "prov:Entity"},
        {"@id": "c1", "@type": "prov:Activity", "prov:used": [{"@id": "raw"}]},
        {"@id": "out", "@type": "prov:Entity", "prov:wasGeneratedBy": {"@id": "c1"}},
    ]
    inputs, outputs = calculate(graph)
    assert [r["@id"] for r in inputs] == ["raw"]
    assert [r["@id"] for r in outputs] == ["out"]
