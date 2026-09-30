"""Agentic interpretation: the engine's pipeline with the model calls
answered by files an agent writes.

Stands in for the agent with canned answers, so it needs no key, no network
and no pydantic-ai model.
"""

from __future__ import annotations

import json
import os

import pytest

from fairscape_artifacts import cli
from test_interpretation import write_crate

pytest.importorskip("fairscape_graph_tools")


def _answer(comp_id, sw, inputs, outputs, status="clear"):
    return {
        "stepSummary": f"Copies {inputs[0]} to {outputs[0]}.",
        "codeAnalysis": [{"software_id": sw, "summary": "Reads a file and writes it back.",
                          "keyFunctions": ["main"], "assumptions": []}],
        "inputSummaries": [{"dataset_id": i, "role": "input"} for i in inputs],
        "outputSummaries": [{"dataset_id": o, "role": "output"} for o in outputs],
        "assumptions": [{"impact": "MINOR", "name": "Input is text",
                         "description": "The input decodes as text.",
                         "evidence": {"artifact": {"@id": sw}, "location": "main"}}],
        "errors": [],
        "computationStatus": status,
    }


def _run(*argv):
    assert cli.main(list(argv)) == 0


def test_three_stages_write_the_same_document_interpret_writes(tmp_path, capsys):
    crate_dir = str(tmp_path / "crate")
    write_crate(crate_dir)
    work = str(tmp_path / "work")

    _run("interpret", crate_dir, "--agentic", "prepare", "--work-dir", work,
         "--model-label", "agentic:test")
    state = json.load(open(os.path.join(work, "state.json")))
    assert len(state["steps"]) == 2
    packet = open(os.path.join(work, state["steps"][0], "packet.md")).read()
    assert "# Instructions" in packet and "## Computation" in packet
    assert "def main(inp, out)" in packet  # local source reached the packet

    # The steps stage refuses until every answer is there and complete.
    with pytest.raises(SystemExit, match="no annotation.json"):
        cli.main(["interpret", crate_dir, "--agentic", "steps", "--work-dir", work])

    sw = "ark:59852/software-step"
    flows = {"ark:59852/computation-a": (["ark:59852/dataset-raw"], ["ark:59852/dataset-mid"]),
             "ark:59852/computation-b": (["ark:59852/dataset-mid"], ["ark:59852/dataset-out"])}
    for rel in state["steps"]:
        comp = json.load(open(os.path.join(work, rel, "computation.json")))["@id"]
        answer = _answer(comp, sw, *flows[comp])
        if comp.endswith("-b"):
            answer["outputSummaries"] = []
        json.dump(answer, open(os.path.join(work, rel, "annotation.json"), "w"))
    with pytest.raises(SystemExit, match="outputSummaries missing"):
        cli.main(["interpret", crate_dir, "--agentic", "steps", "--work-dir", work])
    for rel in state["steps"]:
        comp = json.load(open(os.path.join(work, rel, "computation.json")))["@id"]
        json.dump(_answer(comp, sw, *flows[comp]),
                  open(os.path.join(work, rel, "annotation.json"), "w"))

    _run("interpret", crate_dir, "--agentic", "steps", "--work-dir", work)
    synthesis_packet = open(os.path.join(work, "synthesis", "packet.md")).read()
    assert "## Pipeline DAG Structure" in synthesis_packet

    with pytest.raises(SystemExit, match="no synthesis"):
        cli.main(["interpret", crate_dir, "--agentic", "assemble", "--work-dir", work])
    json.dump({"executiveSummary": "Two copies.", "narrativeSummary": "First.\n\nSecond.",
               "pipelineSteps": ["copy", "copy again"], "keyFindings": ["It copies."],
               "assumptions": []},
              open(os.path.join(work, "synthesis", "synthesis.json"), "w"))
    _run("interpret", crate_dir, "--agentic", "assemble", "--work-dir", work)

    aeg = json.load(open(os.path.join(crate_dir, "ro-crate-interpretation.json")))
    assert aeg["evi:llmModel"] == "agentic:test"
    assert aeg["evi:executiveSummary"] == "Two copies."
    steps = [aeg["@graph"][r["@id"]] for r in aeg["evi:stepAnnotations"]]
    assert {s["evi:annotates"]["@id"] for s in steps} == set(flows)
    assert aeg["@graph"]["ark:59852/computation-a"]["evi:annotatedBy"]
    assert os.path.exists(os.path.join(crate_dir, "ro-crate-interpretation.html"))
