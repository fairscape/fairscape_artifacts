"""AI interpretation of a crate, via the engine in `fairscape-graph-tools`.

Interpretation is not reimplemented here. `fairscape_graph_tools` condenses
the provenance graph, has an LLM annotate every computation and synthesize
the whole, and validates the result as an `AnnotatedEvidenceGraph`. This
module runs that engine over the adapters in `fairscape_artifacts.interpret`
and turns its JSON into a page.

It costs API calls and minutes, so unlike the grader it never runs as part
of `all`. `all` and `datasheet` only pick up an interpretation that already
sits beside the crate.
"""

from __future__ import annotations

import json
import os
import re
from collections import Counter, deque
from typing import Any, Callable, Dict, Iterable, List, Optional

from fairscape_artifacts import fields as f

INTERPRETATION_JSON = "ro-crate-interpretation.json"
INTERPRETATION_HTML = "ro-crate-interpretation.html"

DEFAULT_MODEL = "anthropic:claude-haiku-4-5-20251001"

#: pydantic-ai provider prefix -> the environment variable it reads its key
#: from. Same table the grader keeps, so `--api-key` behaves alike.
PROVIDER_ENV = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "google-gla": "GOOGLE_API_KEY",
    "google": "GOOGLE_API_KEY",
    "groq": "GROQ_API_KEY",
}

IMPACT_ORDER = ("CRITICAL", "MAJOR", "MINOR")
STATUS_LABELS = {
    "clear": "Clear",
    "review_recommended": "Worth a closer look",
    "error_detected": "Error found",
}

#: A headline or gist is cut here, at a word boundary.
HEADLINE_CHARS = 120


class InterpreterUnavailable(RuntimeError):
    """`fairscape_graph_tools` is not importable."""


def _import_engine():
    try:
        from fairscape_graph_tools.condenser import Condenser
        from fairscape_graph_tools.interpreter import InterpretConfig, Interpreter
        from fairscape_artifacts import interpret as adapters
    except ImportError as err:  # pragma: no cover - depends on the environment
        raise InterpreterUnavailable(
            "the interpretation engine is not installed; install "
            "'fairscape-artifacts[interpret]' (fairscape-graph-tools) to "
            "interpret crates"
        ) from err
    return Condenser, InterpretConfig, Interpreter, adapters


def configure_api_key(model: str, api_key: Optional[str]) -> None:
    """Put `api_key` where the model's provider will look for it."""
    if not api_key:
        return
    prefix = model.partition(":")[0]
    env_var = PROVIDER_ENV.get(prefix)
    if env_var is None:
        raise ValueError(f"unknown provider {prefix!r}; set its API key in the "
                         f"environment instead of passing --api-key")
    os.environ[env_var] = api_key


def run(crate, *, output_path: str, references: Iterable = (),
        model: str = DEFAULT_MODEL, temperature: float = 0.0,
        max_workers: int = 2, condensed_path: Optional[str] = None,
        trace_path: Optional[str] = None,
        progress: Optional[Callable[[str], None]] = None,
        rate_limit_requests: Optional[int] = None) -> Dict[str, Any]:
    """Interpret `crate`, write the AnnotatedEvidenceGraph to `output_path`
    and return it as a dict.

    `references` are extra `Crate`s to resolve ids against; the crate's own
    constituents and linked crates are always included. `rate_limit_requests`
    overrides the engine's per-minute request cap, which is tuned for
    low-tier Anthropic keys.
    """
    Condenser, InterpretConfig, Interpreter, adapters = _import_engine()

    graph = adapters.CrateGraphSource(crate, references)
    sink = adapters.SidecarSink(output_path, condensed_path)
    tracker = adapters.ProgressTracker(progress, trace_path)
    software = adapters.LocalSoftwareFetcher(graph)
    condenser = Condenser(graph, sink)

    settings = {"llm_model": model, "temperature": temperature,
                "max_workers": max_workers}
    if rate_limit_requests is not None:
        settings["rate_limiter_max_requests"] = rate_limit_requests
    interpreter = Interpreter(graph, sink, tracker, software, condenser,
                              InterpretConfig(**settings))
    interpreter.run_sync(graph.primary_root_id)
    assert sink.aeg is not None
    return sink.aeg


def load(path: str) -> Dict[str, Any]:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


# -- page context -----------------------------------------------------------

def summarize(aeg: Dict[str, Any], *, link_base: str = "") -> Dict[str, Any]:
    """Everything the interpretation template needs, and nothing else.

    The AEG's `@graph` is a flat `{@id: node}` map holding the condensed
    crate plus one AnnotatedComputation per computation. Steps come out in
    pipeline order — a step that consumed another's output follows it — so
    the page reads the way the narrative does.
    """
    graph: Dict[str, Dict[str, Any]] = aeg.get("@graph") or {}
    if isinstance(graph, list):
        graph = {n.get("@id"): n for n in graph if isinstance(n, dict)}

    def name_of(node_id: Optional[str]) -> str:
        if not node_id:
            return ""
        node = graph.get(node_id) or {}
        return node.get("name") or node_id

    def href(node_id: Optional[str]) -> str:
        return f.ark_href(node_id, link_base) if node_id else ""

    steps = [_step(graph[i], graph, name_of, href)
             for i in _ref_ids(aeg.get("evi:stepAnnotations")) if i in graph]
    steps = _ordered(steps, graph)
    overview = aeg.get("evi:overview") or {}
    # A whole-run computation is not a step of the pipeline: it goes last,
    # unnumbered, and the graph does not draw it.
    umbrellas = _umbrellas(steps)
    steps = ([s for s in steps if s["computation_id"] not in umbrellas]
             + [s for s in steps if s["computation_id"] in umbrellas])
    for index, step in enumerate(steps, 1):
        step["umbrella"] = step["computation_id"] in umbrellas
        step["number"] = None if step["umbrella"] else index
        step["anchor"] = f"step-{index}"
        step["headline"] = _headline(step, overview.get("pipelineSteps") or [])
        step["gist"] = _sentences(step["summary"], 2)
    step_anchor = {s["id"]: s["anchor"] for s in steps}
    step_label = {s["id"]: (f"Step {s['number']}: {s['name']}" if s["number"]
                            else f"the whole run ({s['name']})") for s in steps}
    step_node = {s["id"]: s["computation_id"] for s in steps}

    assumptions = [_assumption(a, name_of, href, step_anchor, step_label, step_node)
                   for a in aeg.get("evi:assumptions") or []]
    assumptions.sort(key=lambda a: IMPACT_ORDER.index(a["impact"])
                     if a["impact"] in IMPACT_ORDER else len(IMPACT_ORDER))

    annotates = _first_ref(aeg.get("evi:annotates"))
    impact_tally = Counter(a["impact"] for a in assumptions)
    review_steps = sum(1 for s in steps if s["status"] != "clear")
    error_count = sum(len(s["errors"]) for s in steps)

    return {
        "kicker": "AI Interpretation",
        "title": name_of(annotates) or aeg.get("name") or "Interpretation",
        "ark": aeg.get("@id", ""),
        "description": "",
        "chips": [],
        "crate": {"id": annotates, "name": name_of(annotates), "href": href(annotates)},
        "model": aeg.get("evi:llmModel") or aeg.get("author") or "",
        "temperature": aeg.get("evi:llmTemperature"),
        "created": (aeg.get("dateCreated") or "")[:19].replace("T", " "),
        "engine_version": aeg.get("evi:interpreterVersion") or "",
        "overview": {
            "data": overview.get("dataDescription") or "",
            "pipeline": overview.get("pipelineDescription") or "",
            "steps": list(overview.get("pipelineSteps") or []),
            "formats": list(overview.get("dataFormats") or []),
            "keywords": list(overview.get("keywords") or []),
            "license": overview.get("license") or "",
            "access": overview.get("conditionsOfAccess") or "",
        },
        "stats": [
            {"n": len(steps), "label": "steps annotated"},
            {"n": review_steps, "label": "steps flagged for review"},
            {"n": impact_tally.get("CRITICAL", 0), "label": "critical assumptions"},
            {"n": error_count, "label": "errors detected"},
        ],
        # One plain sentence for the top of the page. The engine has no
        # field for it yet, so it is the first sentence of the summary.
        "gist": (overview.get("plainSummary")
                 or _sentences(aeg.get("evi:executiveSummary") or "", 1)
                 or _sentences(overview.get("pipelineDescription") or "", 1)),
        "executive": aeg.get("evi:executiveSummary") or "",
        "narrative": _paragraphs(aeg.get("evi:narrativeSummary") or ""),
        "findings": list(aeg.get("evi:keyFindings") or []),
        "assumptions": assumptions,
        "impact_tally": [{"impact": i, "n": impact_tally.get(i, 0)} for i in IMPACT_ORDER],
        "steps": steps,
        "audiences": [_audience(a, name_of, href, step_anchor, step_label, step_node)
                      for a in aeg.get("evi:audiences") or []],
        "condensation": (graph.get(annotates) or {}).get("evi:condensationStats") or {},
        # The page's graph viewer reads the whole document; ids it links
        # to go through the same base as the rest of the page. Its copy
        # carries the derived headline of each step under a page-only key.
        "graph": _graph_for_viewer(aeg, graph, steps),
        "link_base": link_base,
    }


def _step(node, graph, name_of, href) -> Dict[str, Any]:
    comp_id = _first_ref(node.get("evi:annotates"))
    comp = graph.get(comp_id) or {}
    status = (node.get("evi:computationStatus") or "clear").strip().lower()
    return {
        "id": node.get("@id", ""),
        "computation_id": comp_id,
        "name": _human_name(comp.get("name") or name_of(comp_id) or node.get("name", "")),
        "raw_name": comp.get("name") or name_of(comp_id) or node.get("name", ""),
        "href": href(comp_id),
        "command": comp.get("command") or "",
        "description": comp.get("description") or "",
        "status": status,
        "status_label": STATUS_LABELS.get(status, status.replace("_", " ").capitalize()),
        "summary": node.get("evi:stepSummary") or "",
        "code": [{
            "id": _first_ref(c.get("software")),
            "name": c.get("name") or name_of(_first_ref(c.get("software"))),
            "href": href(_first_ref(c.get("software"))),
            "summary": c.get("summary") or "",
            "functions": list(c.get("keyFunctions") or []),
            "assumptions": [_assumption(a, name_of, href) for a in c.get("assumptions") or []],
        } for c in node.get("evi:codeAnalysis") or []],
        "inputs": [_dataset(d, name_of, href) for d in node.get("evi:inputSummaries") or []],
        "outputs": [_dataset(d, name_of, href) for d in node.get("evi:outputSummaries") or []],
        "assumptions": [_assumption(a, name_of, href) for a in node.get("evi:assumptions") or []],
        "errors": [{
            "severity": (e.get("severity") or "MAJOR").upper(),
            "description": e.get("description") or "",
            "affected": e.get("affectedOutputs") or "",
            "evidence": _evidence(e.get("evidence"), name_of, href),
        } for e in node.get("evi:errors") or []],
        "inputs_ids": _ref_ids(comp.get("usedDataset")),
        "outputs_ids": _ref_ids(comp.get("generated")),
    }


def _dataset(d, name_of, href) -> Dict[str, Any]:
    dataset_id = _first_ref(d.get("dataset"))
    return {
        "id": dataset_id,
        "name": d.get("name") or name_of(dataset_id),
        "href": href(dataset_id),
        "role": d.get("role") or "",
        "description": d.get("description") or "",
        "quality": d.get("dataQuality") or "",
    }


def _assumption(a, name_of, href, step_anchor=None, step_label=None,
                step_node=None) -> Dict[str, Any]:
    source = _first_ref(a.get("sourceAnnotation"))
    return {
        "impact": (a.get("impact") or "MINOR").upper(),
        "name": a.get("name") or "",
        "description": a.get("description") or "",
        "downstream": a.get("downstreamImpacts") or "",
        "evidence": _evidence(a.get("evidence"), name_of, href),
        "review": bool(a.get("reviewRecommended")),
        "validation": a.get("recommendedValidation") or "",
        "source_anchor": (step_anchor or {}).get(source, ""),
        "source_label": (step_label or {}).get(source, ""),
        "source_node": (step_node or {}).get(source, ""),
    }


def _evidence(pointer, name_of, href) -> Optional[Dict[str, str]]:
    if not isinstance(pointer, dict):
        return None
    artifact = _first_ref(pointer.get("artifact"))
    if not artifact and not pointer.get("location"):
        return None
    return {"id": artifact, "name": name_of(artifact), "href": href(artifact),
            "location": pointer.get("location") or ""}


def _audience(a, name_of, href, step_anchor, step_label, step_node) -> Dict[str, Any]:
    return {
        "key": re.sub(r"[^a-z0-9]+", "-", (a.get("targetAudience") or "").lower()).strip("-"),
        "label": a.get("audienceLabel") or a.get("targetAudience") or "",
        "executive": a.get("executiveSummary") or "",
        "narrative": _paragraphs(a.get("narrativeSummary") or ""),
        "findings": list(a.get("keyFindings") or []),
        "assumptions": [_assumption(x, name_of, href, step_anchor, step_label, step_node)
                        for x in a.get("assumptions") or []],
    }


def _ordered(steps: List[Dict[str, Any]], graph) -> List[Dict[str, Any]]:
    """Topological order by data flow, ties kept in listing order.

    A step depends on another when one of its inputs was generated by the
    other, read either from the input's `generatedBy` or from the other's
    `generated` list. A cycle or an unresolvable input falls back to the
    listing order rather than dropping the step.
    """
    producer: Dict[str, str] = {}
    for step in steps:
        for out in step["outputs_ids"]:
            producer.setdefault(out, step["computation_id"])
    for node_id, node in graph.items():
        for gen in _ref_ids(node.get("generatedBy")):
            producer.setdefault(node_id, gen)

    by_comp = {s["computation_id"]: s for s in steps}
    deps = {s["computation_id"]: set() for s in steps}
    for step in steps:
        for inp in step["inputs_ids"]:
            src = producer.get(inp)
            if src and src in by_comp and src != step["computation_id"]:
                deps[step["computation_id"]].add(src)

    indegree = {c: len(d) for c, d in deps.items()}
    ready = deque(s["computation_id"] for s in steps if indegree[s["computation_id"]] == 0)
    out: List[Dict[str, Any]] = []
    while ready:
        current = ready.popleft()
        out.append(by_comp[current])
        for other in steps:
            if current in deps[other["computation_id"]]:
                indegree[other["computation_id"]] -= 1
                if indegree[other["computation_id"]] == 0:
                    ready.append(other["computation_id"])
    placed = {s["computation_id"] for s in out}
    out.extend(s for s in steps if s["computation_id"] not in placed)
    return out


def _human_name(name: str) -> str:
    """`IMAGE_EMBEDDING (U2OS)` reads as `Image Embedding (U2OS)`.

    Underscores become spaces and all-caps words of five letters or more
    are capitalised; shorter ones are usually acronyms and stay. The same
    rule as the graph's cards, so the list and the drawing agree.
    """
    words = []
    for word in (name or "").replace("_", " ").split(" "):
        if re.fullmatch(r"[A-Z][A-Z0-9]{4,}", word):
            word = word[0] + word[1:].lower()
        words.append(word)
    return " ".join(words)


def _sentences(text: str, n: int) -> str:
    """The first `n` sentences of `text`, as one string."""
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])", (text or "").strip())
    return " ".join(p for p in parts[:n] if p).strip()


def _clip(text: str, limit: int = HEADLINE_CHARS) -> str:
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(",;:")
    return cut + "\u2026"


def _headline(step: Dict[str, Any], pipeline_steps: List[str]) -> str:
    """One short line saying what a step does, for its card in the graph.

    The overview's `pipelineSteps` usually names each step and says what it
    did in one sentence; the entry naming this step, minus that prefix, is
    the best short text available. Otherwise the step summary's first
    sentence, cut short.
    """
    token = re.split(r"[\s(]", step["name"].strip(), 1)[0]
    if len(token) >= 4:
        word = r"\b" + re.escape(token) + r"\b"
        lead = r"^\s*(\d+[a-z]?[.)]\s*)?" + word
        hits = ([p for p in pipeline_steps if re.match(lead, p, re.I)]
                or [p for p in pipeline_steps if re.search(word, p, re.I)])
        if len(hits) == 1:
            text = re.sub(r"^\s*\d+[a-z]?[.)]\s*", "", hits[0])
            text = re.sub(r"^" + re.escape(token) + r"\s*(\([^)]*\))?\s*", "",
                          text, flags=re.I)
            if text:
                return _clip(text[0].upper() + text[1:])
    return _clip(_sentences(step["summary"], 1))


def _umbrellas(steps: List[Dict[str, Any]]) -> set:
    """Computations that claim outputs of two or more other steps.

    A workflow-run computation (Nextflow's, say) lists every stage's output
    as its own. The graph viewer leaves such a run out of the drawing, so
    the page must not offer to show it there.
    """
    producers: Dict[str, set] = {}
    for step in steps:
        for out in step["outputs_ids"]:
            producers.setdefault(out, set()).add(step["computation_id"])
    partners: Dict[str, set] = {}
    for comps in producers.values():
        if len(comps) < 2:
            continue
        for comp in comps:
            partners.setdefault(comp, set()).update(comps - {comp})
    return {comp for comp, others in partners.items() if len(others) >= 2}


def _graph_for_viewer(aeg: Dict[str, Any], graph: Dict[str, Dict[str, Any]],
                      steps: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_annotation = {s["id"]: s for s in steps}
    nodes = dict(graph)
    for node_id, step in by_annotation.items():
        nodes[node_id] = {**graph[node_id], "_headline": step["headline"]}
        if step["number"]:
            nodes[node_id]["_number"] = step["number"]
    return {**aeg, "@graph": nodes}


def _paragraphs(text: str) -> List[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]


def _first_ref(value: Any) -> Optional[str]:
    ids = _ref_ids(value)
    return ids[0] if ids else None


def _ref_ids(value: Any) -> List[str]:
    if isinstance(value, (dict, str)):
        value = [value]
    if not isinstance(value, list):
        return []
    out = []
    for item in value:
        ref = item.get("@id") if isinstance(item, dict) else item
        if isinstance(ref, str) and ref:
            out.append(ref)
    return out
