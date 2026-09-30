"""Agentic interpretation: the host agent stands in for the LLM calls.

`fairscape-artifacts interpret` has `fairscape_graph_tools` call a model
through pydantic-ai. This module runs the same pipeline with the two model
calls cut out and handed to an agent (Claude Code and its subagents):

    prepare   condense, prefetch code and stats, then write one work packet
              per computation: the engine's system prompt, the exact user
              prompt it would send, and the JSON schema of the answer
    steps     validate each agent-written `annotation.json` against that
              schema, convert it the way the engine does, and write the
              synthesis packet
    assemble  validate `synthesis.json`, build the AnnotatedEvidenceGraph
              with the engine's own `build_aeg`, and write it beside the
              crate as `ro-crate-interpretation.json`

Everything other than the two model calls is the engine's code, so the
output is the same document `interpret` writes and the same one the web
client's annotated graph viewer reads.

Work directory layout:

    <work>/state.json
    <work>/condensed.json
    <work>/steps/<NN>/packet.md         what the agent reads
    <work>/steps/<NN>/schema.json       what the agent's answer must match
    <work>/steps/<NN>/computation.json  {"@id": ...}
    <work>/steps/<NN>/annotation.json   written by the agent
    <work>/synthesis/packet.md
    <work>/synthesis/schema.json
    <work>/synthesis/synthesis.json     written by the agent
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, Iterable, List, Optional

from fairscape_artifacts.crate import Crate

DEFAULT_MODEL_LABEL = "agentic:claude-code"

STATE = "state.json"
CONDENSED = "condensed.json"
STEPS = "steps"
SYNTHESIS = "synthesis"
PACKET = "packet.md"
SCHEMA = "schema.json"
COMPUTATION = "computation.json"
ANNOTATION = "annotation.json"
SYNTHESIS_OUT = "synthesis.json"
ANNOTATED = "annotated.json"


class AgenticError(RuntimeError):
    """A stage cannot run: missing or invalid agent output."""


def _engine():
    from fairscape_graph_tools.condenser import Condenser
    from fairscape_graph_tools.models.annotated_computation import (
        AnnotatedComputation,
        LLMComputationAnnotation,
    )
    from fairscape_graph_tools.pipeline import annotate, build, synthesize
    from fairscape_graph_tools.pipeline.graph_utils import (
        _build_index,
        _is_computation,
        _resolve_refs,
    )
    from fairscape_graph_tools.prompts import (
        DATASCI_SYNTHESIS_PROMPT,
        DATASCI_SYSTEM_PROMPT,
    )
    return {
        "Condenser": Condenser,
        "AnnotatedComputation": AnnotatedComputation,
        "LLMComputationAnnotation": LLMComputationAnnotation,
        "annotate": annotate,
        "build": build,
        "synthesize": synthesize,
        "build_index": _build_index,
        "is_computation": _is_computation,
        "resolve_refs": _resolve_refs,
        "system_prompt": DATASCI_SYSTEM_PROMPT,
        "synthesis_prompt": DATASCI_SYNTHESIS_PROMPT,
    }


# -- prepare -----------------------------------------------------------------

def prepare(crate: Crate, work_dir: str, *, references: Iterable[Crate] = (),
            output_path: str, model_label: str = DEFAULT_MODEL_LABEL) -> List[str]:
    """Condense the crate and write one annotation packet per computation.

    Returns the step directories, in the order the agent should hand them
    out (any order works; they are independent).
    """
    from fairscape_artifacts.interpret import adapters
    e = _engine()

    graph = adapters.CrateGraphSource(crate, references)
    sink = adapters.SidecarSink(os.path.join(work_dir, "unused.json"))
    graph_list, _, root_node = e["Condenser"](graph, sink).ensure_condensed(
        graph.primary_root_id)
    index = e["build_index"](graph_list)
    computations = [n for n in graph_list if e["is_computation"](n)]
    if not computations:
        raise AgenticError(f"no Computation nodes in {crate.path or 'crate'}")

    fetcher = adapters.LocalSoftwareFetcher(graph)
    software: Dict[str, str] = {}
    dataset_ids: set = set()
    for comp in computations:
        for sw_id in e["resolve_refs"](comp.get("usedSoftware")):
            if sw_id not in software:
                software[sw_id] = fetcher.fetch(index.get(sw_id, {}))
        dataset_ids.update(e["resolve_refs"](comp.get("usedDataset")))
        dataset_ids.update(e["resolve_refs"](comp.get("generated")))
    stats = graph.find_dataset_stats(dataset_ids) if dataset_ids else {}

    os.makedirs(work_dir, exist_ok=True)
    _dump(os.path.join(work_dir, CONDENSED), graph_list)

    schema = e["LLMComputationAnnotation"].model_json_schema()
    step_dirs = []
    for number, comp in enumerate(computations, 1):
        step_dir = os.path.join(work_dir, STEPS, f"{number:02d}")
        os.makedirs(step_dir, exist_ok=True)
        prompt = e["annotate"].build_computation_prompt(comp, software, index,
                                                        stats_cache=stats)
        _write(os.path.join(step_dir, PACKET), _packet(
            e["system_prompt"], prompt, schema,
            answer=os.path.join(step_dir, ANNOTATION)))
        _dump(os.path.join(step_dir, SCHEMA), schema)
        _dump(os.path.join(step_dir, COMPUTATION),
              {"@id": comp.get("@id"), "name": comp.get("name")})
        step_dirs.append(step_dir)

    _dump(os.path.join(work_dir, STATE), {
        "crate": crate.path,
        "rocrate_id": graph.primary_root_id,
        "root_node": root_node,
        "output": os.path.abspath(output_path),
        "model": model_label,
        "steps": [os.path.relpath(d, work_dir) for d in step_dirs],
        "missing_source": sorted(i for i, code in software.items()
                                 if code == adapters.SOURCE_PLACEHOLDER),
    })
    return step_dirs


# -- steps -> synthesis packet ------------------------------------------------

def collect_steps(work_dir: str) -> str:
    """Validate every step answer and write the synthesis packet.

    Raises `AgenticError` listing each step whose answer is missing or does
    not match the schema, so the agent knows exactly what to redo.
    """
    e = _engine()
    state = _load(os.path.join(work_dir, STATE))
    index = e["build_index"](_load(os.path.join(work_dir, CONDENSED)))
    annotations = _annotations(work_dir, state, index, e)

    prompt = e["synthesize"].build_synthesis_prompt(
        state["root_node"], annotations, graph_dict=index)
    out_dir = os.path.join(work_dir, SYNTHESIS)
    os.makedirs(out_dir, exist_ok=True)
    schema = e["synthesize"].GraphSynthesisResult.model_json_schema()
    _write(os.path.join(out_dir, PACKET), _packet(
        e["synthesis_prompt"], prompt, schema,
        answer=os.path.join(out_dir, SYNTHESIS_OUT)))
    _dump(os.path.join(out_dir, SCHEMA), schema)
    _dump(os.path.join(work_dir, ANNOTATED),
          [a.model_dump(by_alias=True, mode="json") for a in annotations])
    return os.path.join(out_dir, PACKET)


def _annotations(work_dir, state, index, e):
    problems: List[str] = []
    annotations = []
    for rel in state["steps"]:
        step_dir = os.path.join(work_dir, rel)
        comp = _load(os.path.join(step_dir, COMPUTATION))
        answer = os.path.join(step_dir, ANNOTATION)
        if not os.path.exists(answer):
            problems.append(f"{rel}: no {ANNOTATION}")
            continue
        try:
            llm = e["LLMComputationAnnotation"].model_validate(_load(answer))
        except Exception as err:  # json or pydantic
            problems.append(f"{rel}: {_one_line(err)}")
            continue
        problems.extend(f"{rel}: {p}" for p in _completeness(
            llm, index.get(comp["@id"], {}), e))
        annotations.append(e["annotate"].llm_to_annotated(
            llm, comp["@id"], state["model"], None))
    if problems:
        raise AgenticError("step answers need fixing:\n  " + "\n  ".join(problems))
    return annotations


def _completeness(llm, comp: dict, e) -> List[str]:
    """The same gaps the engine re-prompts for, plus ids that point at
    nothing the prompt showed."""
    refs = e["resolve_refs"]
    cap = e["annotate"].MAX_PROMPT_DATASETS
    software = refs(comp.get("usedSoftware"))
    inputs = refs(comp.get("usedDataset"))[:cap]
    outputs = refs(comp.get("generated"))[:cap]
    out = []
    got_sw = {c.software_id for c in llm.codeAnalysis or []}
    if set(software) - got_sw:
        out.append(f"codeAnalysis missing {sorted(set(software) - got_sw)}")
    got_in = {d.dataset_id for d in llm.inputSummaries or []}
    if set(inputs) - got_in:
        out.append(f"inputSummaries missing {sorted(set(inputs) - got_in)}")
    got_out = {d.dataset_id for d in llm.outputSummaries or []}
    if set(outputs) - got_out:
        out.append(f"outputSummaries missing {sorted(set(outputs) - got_out)}")
    if llm.computationStatus not in ("clear", "review_recommended", "error_detected"):
        out.append(f"computationStatus {llm.computationStatus!r} is not one of "
                   "clear / review_recommended / error_detected")
    return out


# -- assemble ----------------------------------------------------------------

def assemble(work_dir: str) -> Dict[str, Any]:
    """Build and write the AnnotatedEvidenceGraph; return it as a dict."""
    e = _engine()
    state = _load(os.path.join(work_dir, STATE))
    graph_list = _load(os.path.join(work_dir, CONDENSED))
    annotations = [e["AnnotatedComputation"].model_validate(a)
                   for a in _load(os.path.join(work_dir, ANNOTATED))]
    answer = os.path.join(work_dir, SYNTHESIS, SYNTHESIS_OUT)
    if not os.path.exists(answer):
        raise AgenticError(f"no {os.path.relpath(answer, work_dir)}")
    try:
        synthesis = e["synthesize"].GraphSynthesisResult.model_validate(_load(answer))
    except Exception as err:
        raise AgenticError(f"synthesis answer needs fixing: {_one_line(err)}")

    aeg = e["build"].build_aeg(state["rocrate_id"], graph_list, annotations,
                               synthesis, [], state["model"], None)
    payload = aeg.model_dump(by_alias=True, mode="json")
    _dump(state["output"], payload)
    return payload


def output_path(work_dir: str) -> str:
    return _load(os.path.join(work_dir, STATE))["output"]


# -- helpers -----------------------------------------------------------------

def _packet(system_prompt: str, prompt: str, schema: dict, *, answer: str) -> str:
    return (
        "# Instructions\n\n"
        f"{system_prompt.strip()}\n\n"
        "# Input\n\n"
        f"{prompt.strip()}\n\n"
        "# Answer\n\n"
        f"Write a single JSON object to `{answer}` that validates against "
        f"`{SCHEMA}` in this folder (reproduced below). Use the exact `@id`s "
        "shown above for `software_id`, `dataset_id` and evidence artifacts. "
        "No prose outside the JSON.\n\n"
        f"```json\n{json.dumps(schema, indent=2)}\n```\n"
    )


def _one_line(err: Exception) -> str:
    return re.sub(r"\s+", " ", str(err)).strip()[:600]


def _load(path: str) -> Any:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _dump(path: str, data: Any) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, default=str)


def _write(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
