"""The four `fairscape_graph_tools` ports, implemented over local crates.

Ported from fairscape-cli's `fairscape_cli.interpret.*`, with the crate
reading replaced by `fairscape_artifacts.crate.Crate` and progress output
replaced by a callable so the CLI decides where it goes.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import re
from typing import Any, Callable, Dict, Iterable, List, Optional

from fairscape_graph_tools.pipeline.condense import ARK_REF_FIELDS
from fairscape_graph_tools.pipeline.github import (
    MAX_SOFTWARE_BYTES,
    prefetch_software_code,
)
from fairscape_graph_tools.pipeline.graph_utils import (
    flexible_ark_query,
    is_rocrate_root,
)

from fairscape_artifacts.crate import METADATA_FILENAME, Crate

logger = logging.getLogger(__name__)

Node = Dict[str, Any]
Progress = Callable[[str], None]

SOURCE_PLACEHOLDER = "[source not fetched]"


# -- GraphSource ------------------------------------------------------------

class CrateGraphSource:
    """`GraphSource` over a crate, the crates it contains or links to, and
    any extra reference crates.

    The merged index is layered the way the evidence graph builder layers
    it: a constituent's or linked crate's own copy of an entity wins over
    the stub the primary crate carries for it, because the stub has no
    provenance and the copy does. Reference crates only fill in ids the
    layered index does not already have.

    The engine finds the crate root by type (`ROCrate` in `@type`). A crate
    whose root is only typed `Dataset` — a PROV conversion, say — would give
    the synthesis step an empty root, so the primary root's *copy* in this
    index gains that type. The crate on disk is never modified.
    """

    def __init__(self, crate: Crate, references: Iterable[Crate] = ()):
        self.crate = crate
        self._index: Dict[str, Node] = {}
        self._owner: Dict[str, Crate] = {}
        self._stats: Dict[str, Dict[str, Any]] = {}

        self._add(crate, overlay=False)
        for sub in crate.sub_crates():
            self._add(sub.crate, overlay=True)
        for sub in crate.linked_closure():
            self._add(sub.crate, overlay=True)
        for ref in references:
            self._add(ref, overlay=False)

        self.primary_root_id = crate.root_id
        if not self.primary_root_id or self.primary_root_id not in self._index:
            raise ValueError(f"no root entity found in {crate.path or 'crate'}")
        root = dict(self._index[self.primary_root_id])
        if not is_rocrate_root(root):
            types = root.get("@type") or []
            types = [types] if isinstance(types, str) else list(types)
            root["@type"] = types + ["https://w3id.org/EVI#ROCrate"]
            self._index[self.primary_root_id] = root
        self.primary_root_name = self._index[self.primary_root_id].get("name", "") or ""

    def _add(self, crate: Crate, *, overlay: bool) -> None:
        for node in crate.graph:
            node_id = node.get("@id")
            if not node_id or node_id == METADATA_FILENAME:
                continue
            if node_id in self._index and not overlay:
                continue
            self._index[node_id] = node
            self._owner[node_id] = crate
            desc = node.get("descriptiveStatistics")
            split = node.get("splitStatistics")
            if desc or split:
                self._stats[node_id] = {"descriptiveStatistics": desc or {},
                                        "splitStatistics": split or {}}

    # port

    def find_entity(self, ark_id: str) -> Optional[Node]:
        if ark_id in self._index:
            return self._index[ark_id]
        query = flexible_ark_query(ark_id)
        if query is None:
            return None
        regex = re.compile(query["@id"]["$regex"])
        for candidate, node in self._index.items():
            if regex.match(candidate):
                return node
        return None

    def find_many(self, ark_ids: Iterable[str]) -> Dict[str, Node]:
        """Exact-match only, like the server's adapter: the engine resolves
        the start id once through `find_entity` and feeds canonical ids in
        here."""
        return {i: self._index[i] for i in ark_ids if i in self._index}

    def find_dataset_stats(self, ark_ids: Iterable[str]) -> Dict[str, Dict[str, Any]]:
        return {i: self._stats[i] for i in ark_ids if i in self._stats}

    def build_full_graph(self, rocrate_id: str) -> List[Node]:
        """Breadth-first from the root over every reference field the
        condenser knows, following any id the index resolves."""
        collected: Dict[str, Node] = {}
        queue = [rocrate_id]
        while queue:
            node_id = queue.pop(0)
            if node_id in collected:
                continue
            node = self._index.get(node_id)
            if node is None:
                continue
            collected[node_id] = node
            for field in ARK_REF_FIELDS:
                for ref in _refs(node.get(field)):
                    if ref not in collected:
                        queue.append(ref)
        return list(collected.values())

    # local affordances

    def owner_of(self, node_id: str) -> Optional[Crate]:
        """The crate a node was read from, for resolving its `contentUrl`."""
        return self._owner.get(node_id)


def _refs(value: Any) -> List[str]:
    if isinstance(value, dict):
        value = [value]
    elif isinstance(value, str):
        value = [value]
    elif not isinstance(value, list):
        return []
    out = []
    for item in value:
        ref = item.get("@id") if isinstance(item, dict) else item
        if isinstance(ref, str) and ref:
            out.append(ref)
    return out


# -- ResultSink -------------------------------------------------------------

class SidecarSink:
    """`ResultSink` that writes JSON beside the crate and keeps the last
    payload in memory so the caller can render it without re-reading."""

    def __init__(self, output_path: str, condensed_path: Optional[str] = None):
        self.output_path = output_path
        self.condensed_path = condensed_path
        self.aeg: Optional[Dict[str, Any]] = None
        self.condensed: Optional[Dict[str, Any]] = None
        self.condensation_stats: Dict[str, Any] = {}

    def persist_condensed(self, condensed_id: str, condensed_metadata: dict,
                          source_rocrate_id: str, stats: dict) -> str:
        self.condensed = condensed_metadata
        self.condensation_stats = stats or {}
        if self.condensed_path:
            _write_json(self.condensed_path, condensed_metadata)
        return condensed_id

    def persist_evidence_graph(self, evidence_graph, source_node_id: str) -> str:
        payload = evidence_graph.model_dump(by_alias=True, mode="json", exclude_none=True)
        _write_json(self.output_path, payload)
        return evidence_graph.guid

    def persist_aeg(self, aeg, rocrate_id: str, step_annotations: list) -> str:
        self.aeg = aeg.model_dump(by_alias=True, mode="json")
        _write_json(self.output_path, self.aeg)
        return aeg.guid


def _write_json(path: str, data: Any) -> None:
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, default=str)


# -- TaskTracker ------------------------------------------------------------

class ProgressTracker:
    """`TaskTracker` that reports through a callable and can append every
    raw LLM response to a JSONL trace."""

    def __init__(self, progress: Optional[Progress] = None,
                 trace_path: Optional[str] = None):
        self._progress = progress or (lambda message: None)
        self.trace_path = trace_path
        self.state: Dict[str, Any] = {
            "completed_computations": 0,
            "total_computations": 0,
            "computation_details": [],
        }

    def update(self, updates: dict) -> None:
        step = updates.get("current_step")
        changed = step is not None and step != self.state.get("current_step")
        self.state.update(updates)
        if changed:
            self._progress(f"[{step}]")
        if "total_computations" in updates:
            self._progress(f"  {updates['total_computations']} computation(s)")
        status = updates.get("status")
        if status == "SUCCESS":
            self._progress(f"  done: {self.state.get('annotated_evidence_graph_id', '')}".rstrip())
        elif status == "FAILURE":
            err = updates.get("error") or self.state.get("error") or {}
            message = err.get("message", "unknown") if isinstance(err, dict) else str(err)
            self._progress(f"  failed: {message}")

    def update_computation_status(self, comp_id: str, updates: dict) -> None:
        for entry in self.state.get("computation_details", []):
            if entry.get("computation_id") == comp_id:
                entry.update(updates)
                break
        status = updates.get("status")
        if status and status not in ("pending", "in_progress"):
            self._progress(f"  {status}: {comp_id.rsplit('/', 1)[-1]}")

    def increment_completed(self) -> None:
        self.state["completed_computations"] = self.state.get("completed_computations", 0) + 1
        self._progress(f"  {self.state['completed_computations']}/"
                       f"{self.state.get('total_computations', 0)} annotated")

    def push_llm_result(self, label: str, raw_output: dict) -> None:
        if not self.trace_path:
            return
        entry = {"label": label, "timestamp": _dt.datetime.utcnow().isoformat(),
                 "output": raw_output}
        try:
            parent = os.path.dirname(os.path.abspath(self.trace_path))
            os.makedirs(parent, exist_ok=True)
            with open(self.trace_path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, default=str) + "\n")
        except OSError as err:
            logger.warning("could not append LLM trace to %s: %s", self.trace_path, err)


# -- SoftwareFetcher --------------------------------------------------------

class LocalSoftwareFetcher:
    """`SoftwareFetcher`: the file the `contentUrl` names inside the crate
    that owns the node, then GitHub, then a placeholder."""

    def __init__(self, graph: CrateGraphSource):
        self.graph = graph

    def fetch(self, software_node: dict) -> str:
        sw_id = software_node.get("@id", "?")
        content_url = software_node.get("contentUrl") or ""

        owner = self.graph.owner_of(sw_id)
        path = owner.file_path(software_node) if owner else None
        if path and os.path.isfile(path):
            try:
                with open(path, encoding="utf-8", errors="replace") as handle:
                    return self._cap(handle.read(), sw_id, "local")
            except OSError as err:
                logger.warning("software %s: could not read %s: %s", sw_id, path, err)

        if isinstance(content_url, str) and content_url.startswith("http"):
            code = prefetch_software_code(content_url)
            # The GitHub helper answers with a bracketed note for anything it
            # could not fetch; that is a miss here, not source.
            if code and not code.startswith("["):
                return self._cap(code, sw_id, "github")

        logger.warning("software %s: no source for contentUrl %r", sw_id, content_url)
        return SOURCE_PLACEHOLDER

    @staticmethod
    def _cap(code: str, sw_id: str, source: str) -> str:
        if len(code.encode("utf-8")) > MAX_SOFTWARE_BYTES:
            code = code[:MAX_SOFTWARE_BYTES] + "\n[...truncated...]"
        logger.info("software %s: %d chars from %s", sw_id, len(code), source)
        return code
