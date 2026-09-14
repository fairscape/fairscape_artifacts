"""EVI `MLModel` entities: trained models are leaf outputs, loaded models are
inputs, and they get their own display bucket.

Models used to be typed as datasets (with an `mls:Model` additionalType), so
the output rules and the composition buckets never had to know about them.
Now that converters emit `https://w3id.org/EVI#MLModel` nodes (the mlflow
plugin does), a model has to derive exactly like a dataset would have, or the
evidence graph — which starts from the crate's outputs — never reaches it.
"""

from __future__ import annotations

from fairscape_artifacts import evidence, fields, outputs
from fairscape_artifacts.crate import Crate

EVI = "https://w3id.org/EVI#"
ROOT = "ark:99999/crate"


def _crate(*nodes):
    root = {"@id": ROOT, "@type": ["Dataset", EVI + "ROCrate"], "name": "models"}
    descriptor = {"@id": "ro-crate-metadata.json", "@type": "CreativeWork",
                  "about": {"@id": ROOT}}
    return Crate({"@graph": [descriptor, root, *nodes]}, path=None)


TRAIN = {"@id": "ark:99999/computation-train", "@type": ["prov:Activity", EVI + "Computation"],
         "name": "train", "usedDataset": [{"@id": "ark:99999/dataset-cohort"}],
         "usedMLModel": [], "generated": [{"@id": "ark:99999/mlmodel-clf"}]}
COHORT = {"@id": "ark:99999/dataset-cohort", "@type": ["prov:Entity", EVI + "Dataset"],
          "name": "cohort", "generatedBy": []}
MODEL = {"@id": "ark:99999/mlmodel-clf", "@type": ["prov:Entity", EVI + "MLModel"],
         "name": "clf", "format": "sklearn", "contentSize": "1024",
         "generatedBy": [{"@id": TRAIN["@id"]}], "trainedOn": [{"@id": COHORT["@id"]}]}
EVAL = {"@id": "ark:99999/computation-eval", "@type": ["prov:Activity", EVI + "Computation"],
        "name": "eval", "usedDataset": [], "usedMLModel": [{"@id": MODEL["@id"]}],
        "generated": [{"@id": "ark:99999/dataset-scores"}]}
SCORES = {"@id": "ark:99999/dataset-scores", "@type": ["prov:Entity", EVI + "Dataset"],
          "name": "scores", "generatedBy": [{"@id": EVAL["@id"]}]}


def test_trained_model_is_a_leaf_output():
    inputs, outs = outputs.calculate(_crate(TRAIN, COHORT, MODEL).graph)
    assert {"@id": MODEL["@id"]} in outs
    assert {"@id": COHORT["@id"]} in inputs
    assert {"@id": MODEL["@id"]} not in inputs


def test_model_loaded_by_a_computation_is_not_an_output():
    inputs, outs = outputs.calculate(_crate(TRAIN, COHORT, MODEL, EVAL, SCORES).graph)
    assert [o["@id"] for o in outs] == [SCORES["@id"]]
    assert {"@id": MODEL["@id"]} not in inputs, "trained in this crate, so not an input"


def test_model_from_outside_the_crate_is_an_input():
    foreign = {**MODEL, "generatedBy": [], "trainedOn": []}
    inputs, outs = outputs.calculate(_crate(foreign, EVAL, SCORES).graph)
    assert {"@id": MODEL["@id"]} in inputs
    assert {"@id": MODEL["@id"]} not in outs


def test_evidence_graph_reaches_the_model_through_used_mlmodel():
    graph = evidence.build(_crate(TRAIN, COHORT, MODEL, EVAL, SCORES))
    seen = set()
    stack = [graph]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            if node.get("@id"):
                seen.add(node["@id"])
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    assert {MODEL["@id"], TRAIN["@id"], COHORT["@id"], EVAL["@id"]} <= seen


def test_mlmodel_bucket():
    assert fields.bucket(MODEL) == "mlmodel"
    assert "mlmodel" in fields.BUCKETS
    assert fields.bucket(COHORT) == "dataset"
