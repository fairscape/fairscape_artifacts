"""Build an evidence graph from a crate on disk.

Three steps, in order:

1. **BFS** backwards from the crate's outputs, collecting every entity the
   provenance chain reaches into a node cache.
2. **Condense** the cache, collapsing sibling fan-in into DatasetGroups.
3. **Project** the cache into the hierarchical `@graph` the viewer reads —
   a dict keyed by `@id`, where each node carries only the fields the
   visualization needs plus its outgoing edges.

Ported from `fairscape_graph_tools.evidence_graph_builder`, which drives the
same algorithm through storage ports. Here the only source is an in-memory
`{@id: node}` index, so the ports collapse to a dict and nothing touches a
database. The four near-identical `usedSoftware` / `usedSample` /
`usedInstrument` / `usedMLModel` branches of the original are one loop.

Beyond the original it understands PROV-converted crates: `prov:Activity`
and `prov:Entity` nodes with no EVI type still traverse, an activity with no
`usedDataset` falls back to `prov:used`, and an entity with no `generatedBy`
falls back to `derivedFrom` (see `crate.provenance_edge`).

`build_domain` is the second view CPM-style crates need: those carry two
provenance layers joined by `prov:specializationOf`, and it walks the
detailed domain layer instead of the standardized backbone.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set, Tuple

from fairscape_artifacts import outputs as io_outputs
from fairscape_artifacts.condense import condense_cache
from fairscape_artifacts.crate import (
    DERIVED_FROM_FIELDS,
    GENERATED_BY_FIELDS,
    PROV_USED_FIELDS,
    USED_FIELDS,
    derived_from_ids,
    evi_type,
    generated_by_ids,
    is_rocrate,
    provenance_edge,
    ref_ids,
    specialization_of_ids,
    used_dataset_ids,
)

Node = Dict[str, Any]

#: Entities — things provenance flows *into*.
_ENTITY_TYPES = {"Dataset", "Sample", "Instrument", "Software", "MLModel"}
#: Activities — things provenance flows *through*.
_ACTIVITY_TYPES = {"Computation", "Experiment", "Annotation"}

_ARK = re.compile(r"^ark:/?(\d+)/(.*)$")


def graph_id_for(node_id: str) -> str:
    """`ark:NAAN/x` -> `ark:NAAN/evidence-graph-x`; anything else gets a suffix."""
    match = _ARK.match(node_id)
    if match:
        return f"ark:{match.group(1)}/evidence-graph-{match.group(2)}"
    return f"{node_id}-evidence-graph"


#: Where a crate root records what it produced, in precedence order.
_OUTPUT_FIELDS = ("https://w3id.org/EVI#outputs", "EVI:outputs", "outputs")


def _rocrate_outputs(node: Node) -> List[Dict[str, str]]:
    for field in _OUTPUT_FIELDS:
        value = node.get(field)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            return [value]
    return []


def _provenance_refs(node: Node) -> Tuple[Optional[str], List[str]]:
    """The upstream edge to draw from an entity, with the arity the viewer wants.

    `generatedBy` is emitted as a single reference and only the first producer
    is followed — that is what the upstream builder does and what the viewer
    has always been fed. `derivedFrom` is emitted as a list. The asymmetry is
    not principled, it is the established wire format, so it is preserved
    rather than tidied.
    """
    edge, ids = provenance_edge(node)
    if edge == "generatedBy" and ids:
        return edge, ids[:1]
    return edge, ids


def _referenced_ids(node: Node) -> Set[str]:
    """The ids the BFS should visit next from `node`."""
    kind = evi_type(node)
    if kind in _ENTITY_TYPES:
        return set(_provenance_refs(node)[1])
    if kind in _ACTIVITY_TYPES:
        return set(ref_ids(node, *USED_FIELDS)) | set(used_dataset_ids(node))
    return set()


def _expand_used_dataset(dataset_ids: List[str],
                         cache: Dict[str, Node]) -> List[Dict[str, str]]:
    """Resolve a computation's consumed datasets, unwrapping nested crates.

    Pointing at a whole RO-Crate means "everything that crate produced", so it
    is replaced by that crate's outputs. A crate with no declared outputs, and
    any id that didn't resolve, is kept as-is.
    """
    refs: List[Dict[str, str]] = []
    for dataset_id in dataset_ids:
        target = cache.get(dataset_id)
        if target and "error" not in target and is_rocrate(target):
            nested = [r for r in _rocrate_outputs(target) if r.get("@id")]
            refs.extend({"@id": r["@id"]} for r in nested) if nested else \
                refs.append({"@id": dataset_id})
        else:
            refs.append({"@id": dataset_id})
    return refs


class EvidenceGraph:
    """Builds and holds one evidence graph.

    `cache` only ever needs `.get(id)`, so a `SpecializationView` stands in
    for the plain `{@id: node}` dict to build the domain-layer view.

    `crate_root_id` names the crate's own root entity. A graph rooted there
    starts from what the crate *produced* rather than from the node itself —
    which is what `@type` alone used to decide, and cannot in a PROV-only
    crate, whose root is an ordinary `Dataset`.
    """

    def __init__(self, cache, condense_threshold: Optional[int] = 5,
                 crate_root_id: Optional[str] = None,
                 owner: Optional[Dict[str, str]] = None,
                 crate_names: Optional[Dict[str, str]] = None):
        self.cache = cache
        self.condense_threshold = condense_threshold
        self.crate_root_id = crate_root_id
        #: id -> root id of the crate that supplied the node (linked crates)
        self.owner = owner or {}
        #: crate root id -> display name, for the `crate` annotation
        self.crate_names = crate_names or {}

    @classmethod
    def from_crate(cls, crate, condense_threshold: Optional[int] = 5) -> "EvidenceGraph":
        """Index a crate's `@graph` — and every crate it links to — as the
        node cache, so the walk crosses into an upstream crate wherever this
        one carries a stub of something the upstream produced."""
        index, owner, names = _linked_index(crate)
        return cls(index, condense_threshold=condense_threshold,
                   crate_root_id=crate.root_id, owner=owner, crate_names=names)

    def build(self, node_id: str, *, owner: str = "local",
              name: Optional[str] = None,
              description: Optional[str] = None) -> Dict[str, Any]:
        """Build the graph rooted at `node_id` and return it as JSON."""
        start = self.cache.get(node_id)

        graph_dict, output_refs, stats = self._populate(node_id, start)

        return {
            "@id": graph_id_for(node_id),
            "@type": "evi:EvidenceGraph",
            "name": name or f"Evidence Graph for {node_id}",
            "description": description
                or f"Automatically generated Evidence Graph for node {node_id}",
            "owner": owner,
            "outputs": output_refs,
            "@graph": graph_dict,
            "condensation_stats": stats,
        }

    # -- internals -----------------------------------------------------

    def _populate(self, start_id: str, start: Optional[Node]):
        if start is None:
            return (
                {start_id: {"@id": start_id, "error": "not found"}},
                [{"@id": start_id}],
                {"condensed": False, "originalEntityCount": 0,
                 "condensedEntityCount": 0, "datasetGroupCount": 0},
            )

        # Copy every node in: `condense_cache` rewrites a computation's
        # `usedDataset` to point at its group, and the cache is otherwise
        # sharing dicts with the caller's crate. Without this, building a
        # graph silently rewrites the crate for whatever is built next.
        cache: Dict[str, Node] = {start_id: dict(start)}
        output_refs: List[Dict[str, str]] = []
        start_crate_id: Optional[str] = None
        start_crate_outputs: Optional[List[Dict[str, str]]] = None

        if is_rocrate(start) or start_id == self.crate_root_id:
            # A crate's evidence graph is rooted at what it produced; the crate
            # node itself rides along so it can hold `hasOutputs`.
            start_crate_id = start_id
            start_crate_outputs = list(_rocrate_outputs(start))
            for ref in start_crate_outputs + [{"@id": start_id}]:
                if ref.get("@id"):
                    output_refs.append({"@id": ref["@id"]})
        else:
            output_refs.append({"@id": start_id})

        # Breadth-first, one level at a time.
        frontier = {ref["@id"] for ref in output_refs}
        seen: Set[str] = set()
        while frontier:
            todo = frontier - seen
            if not todo:
                break
            for node_id in todo - cache.keys():
                found = self.cache.get(node_id)
                cache[node_id] = dict(found) if found is not None else \
                    {"@id": node_id, "error": "not found"}
            nxt: Set[str] = set()
            for node_id in todo:
                seen.add(node_id)
                node = cache.get(node_id)
                if node and "error" not in node:
                    nxt |= _referenced_ids(node)
            frontier = nxt

        stats = condense_cache(cache, self.condense_threshold)

        graph_dict: Dict[str, Node] = {}
        for ref in output_refs:
            if ref.get("@id"):
                self._project(ref["@id"], cache, graph_dict,
                              start_crate_id, start_crate_outputs)

        return graph_dict, output_refs, stats

    def _project(self, node_id: str, cache: Dict[str, Node],
                 graph: Dict[str, Node], start_crate_id: Optional[str],
                 start_crate_outputs: Optional[List[Dict[str, str]]]) -> None:
        """Copy one node into `graph`, following its edges depth-first."""
        if node_id in graph:
            return

        node = cache.get(node_id)
        if not node:
            graph[node_id] = {"@id": node_id, "error": "not found"}
            return
        if "error" in node:
            graph[node_id] = node
            return

        # Claim the slot before recursing so a provenance cycle terminates.
        result: Node = {
            "@id": node.get("@id"),
            "@type": node.get("@type"),
            "name": node.get("name"),
            "description": node.get("description"),
        }
        graph[node_id] = result

        if node.get("createdBy"):
            result["createdBy"] = node["createdBy"]

        source = self.owner.get(node_id)
        if source and self.crate_root_id and source != self.crate_root_id:
            # the node came from a linked crate: say so, so a reader of the
            # JSON (and any viewer that shows it) knows where the chain went
            result["crate"] = {"@id": source,
                               "name": self.crate_names.get(source, source)}

        if node_id == start_crate_id and start_crate_outputs:
            result["hasOutputs"] = start_crate_outputs

        def recurse(target_id: str) -> None:
            self._project(target_id, cache, graph, start_crate_id, start_crate_outputs)

        kind = evi_type(node)

        if kind in _ENTITY_TYPES:
            # `generatedBy` if it exists, else `derivedFrom` — never both, so
            # the viewer always has exactly one upstream edge to draw.
            edge, ids = _provenance_refs(node)
            if edge and ids:
                result[edge] = ({"@id": ids[0]} if edge == "generatedBy"
                                else [{"@id": i} for i in ids])
                for target_id in ids:
                    recurse(target_id)

        elif kind in _ACTIVITY_TYPES:
            for field in USED_FIELDS:
                # `usedDataset` also carries what a PROV-converted activity
                # states as `prov:used` — the viewer draws only the EVI fields.
                ids = (used_dataset_ids(node) if field == "usedDataset"
                       else ref_ids(node, field))
                if not ids:
                    continue
                refs = (_expand_used_dataset(ids, cache) if field == "usedDataset"
                        else [{"@id": i} for i in ids])
                if refs:
                    result[field] = refs
                    for ref in refs:
                        recurse(ref["@id"])

        if kind == "DatasetGroup":
            # Keep the summary fields and pull in the representative; the other
            # members were dropped by condensation on purpose.
            for field in ("evi:memberCount", "evi:representativeDataset",
                          "evi:commonFormat", "evi:commonSoftware", "format",
                          "evi:memberIds"):
                if field in node:
                    result[field] = node[field]
            for rep_id in ref_ids(node, "evi:representativeDataset"):
                recurse(rep_id)


def build(crate, node_id: Optional[str] = None, *, owner: str = "local",
          condense_threshold: Optional[int] = 5, name: Optional[str] = None,
          description: Optional[str] = None) -> Dict[str, Any]:
    """Build the evidence graph for `crate`, rooted at the crate itself.

    A crate that never had `add-io` run against it has no declared outputs and
    would render as one lonely node, so they are derived in memory first. The
    root is recognized as the crate's root entity, not by its `@type`: a
    PROV-only crate's root is a plain `Dataset`.
    """
    index, owners, names = _rooted_index(crate, node_id or crate.root_id)
    return EvidenceGraph(index, condense_threshold, crate_root_id=crate.root_id,
                         owner=owners, crate_names=names).build(
        node_id or crate.root_id, owner=owner, name=name, description=description)


def _linked_index(crate):
    """`(index, owner, names)`: the crate's nodes plus every linked crate's,
    the linked copy winning over this crate's stub (see
    `Crate.provenance_index`); `names` maps crate root ids to display names.
    A crate built in memory (no path) has nothing to link to."""
    if getattr(crate, "provenance_index", None) is None:
        return dict(crate.index), {}, {}
    index, owner = crate.provenance_index()
    names = {crate.root_id: crate.name}
    for sub in crate.linked_closure():
        names[sub.crate.root_id] = sub.crate.name
    return index, owner, names


def _rooted_index(crate, target: str):
    """The crate's index (spanning its linked crates), with the root's
    outputs derived in memory if it is the target and has none recorded.
    The file on disk is never touched, and outputs are derived from this
    crate's own graph only — what a linked crate produced is its business."""
    index, owner, names = _linked_index(crate)
    if target == crate.root_id and not _rocrate_outputs(crate.root):
        _, derived = io_outputs.ensure(crate)
        if derived:
            root = dict(crate.root)
            root[io_outputs.EVI_OUTPUTS] = derived
            index[target] = root
    return index, owner, names


# ---------------------------------------------------------------------------
# Domain-layer graphs (CPM-style crates)
# ---------------------------------------------------------------------------
#
# A CPM crate describes the same computation twice: the *backbone* — the
# connectors and receipt activities CPM standardizes, identical in shape in
# every bundle — and the *domain layer*, what actually ran in each
# organization's own terms. The two are joined only by
# `prov:specializationOf`, pointing from the detailed domain entity at its
# backbone stand-in, and the ordinary walk does not follow it. So the default
# graph is the backbone, and this is the other view.

#: Every reference field the walk follows, and so every field the domain view
#: has to rewrite for the substitution to be consistent.
_REF_FIELDS = (GENERATED_BY_FIELDS + DERIVED_FROM_FIELDS + USED_FIELDS
               + PROV_USED_FIELDS + _OUTPUT_FIELDS)


class SpecializationView:
    """A read-only node index in which backbone entities give way to their
    `prov:specializationOf` domain entities.

    The crate itself is never touched: every node is rewritten on the way out
    so that a reference to a specialized backbone entity resolves to the
    domain entity/entities that specialize it instead. The walk still crosses
    organization boundaries through the backbone's receipt activities — only
    the connectors give way, to the real detailed provenance. A domain entity
    carrying no provenance of its own inherits its generalization's
    `generatedBy` / `derivedFrom` (substituted the same way), so chains stay
    connected across bundles.

    Reads like the `{@id: node}` dict `EvidenceGraph` expects, so it drops
    straight in as that cache.
    """

    def __init__(self, nodes: Dict[str, Node]):
        self.base = nodes
        self.spec_of: Dict[str, List[str]] = {}     # backbone id -> [domain ids]
        for guid, node in nodes.items():
            for general in specialization_of_ids(node):
                targets = self.spec_of.setdefault(general, [])
                if guid not in targets:
                    targets.append(guid)

    def _substitute(self, ids: List[str], self_id: str) -> List[Dict[str, str]]:
        """Reference ids -> dict refs, with specialized ids swapped in.

        `self_id` never substitutes into its own references: a connector whose
        only specialization is the asking node contributes nothing, because
        the domain layer already holds that link in the other direction.
        """
        seen, out = set(), []
        for rid in ids:
            for target in self.spec_of.get(rid) or [rid]:
                if target == self_id or target in seen:
                    continue
                seen.add(target)
                out.append({"@id": target})
        return out

    def _inherit(self, out: Node, guid: str) -> None:
        """Copy the generalization's provenance onto a bare domain entity."""
        generated, derived = [], []
        for general_id in specialization_of_ids(out):
            general = self.base.get(general_id)
            if not general:
                continue
            generated += self._substitute(generated_by_ids(general), guid)
            derived += self._substitute(derived_from_ids(general), guid)
        if generated:
            out["generatedBy"] = generated
        if derived:
            out["derivedFrom"] = derived

    # -- mapping surface -----------------------------------------------

    def get(self, guid: str) -> Optional[Node]:
        node = self.base.get(guid)
        if node is None:
            return None
        out = dict(node)
        for field in _REF_FIELDS:
            if out.get(field):
                out[field] = self._substitute(ref_ids(out, field), guid)
        if (evi_type(out) not in _ACTIVITY_TYPES
                and not generated_by_ids(out) and not derived_from_ids(out)):
            self._inherit(out, guid)
        return out

    def __getitem__(self, guid: str) -> Node:
        node = self.get(guid)
        if node is None:
            raise KeyError(guid)
        return node

    def __contains__(self, guid) -> bool:
        return guid in self.base

    def keys(self):
        return self.base.keys()


def build_domain(crate, node_id: Optional[str] = None, *, owner: str = "local",
                 condense_threshold: Optional[int] = None,
                 name: Optional[str] = None,
                 description: Optional[str] = None) -> Dict[str, Any]:
    """The domain-layer evidence graph for `crate`, rooted at `node_id`.

    The same walk as `build`, run over a `SpecializationView`: backbone
    connectors and external inputs are replaced by their
    `prov:specializationOf` domain entities wherever one exists. Condensation
    defaults to off — the point of the domain layer is seeing the full detail.
    """
    target = node_id or crate.root_id
    index, owners, names = _rooted_index(crate, target)
    view = SpecializationView(index)
    graph = EvidenceGraph(view, condense_threshold, crate_root_id=crate.root_id,
                          owner=owners, crate_names=names).build(
        target, owner=owner,
        name=name or f"Domain Evidence Graph for {target}",
        description=description
            or f"Automatically generated domain-layer Evidence Graph for node "
               f"{target}: backbone connectors expanded to their "
               f"prov:specializationOf domain entities")
    gid = graph["@id"]
    graph["@id"] = (gid.replace("evidence-graph-", "evidence-graph-domain-", 1)
                    if "evidence-graph-" in gid else gid + "-domain")
    return graph
