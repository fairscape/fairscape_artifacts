"""Collapse fan-in sibling datasets into DatasetGroup summary nodes.

A computation that consumed 400 identically-shaped tiles renders as 400
indistinguishable boxes, which is worse than useless. Condensation replaces
runs of siblings that share a *provenance signature* — same format, same
schema, same software, same input structure — with one `evi:DatasetGroup`
node that names a representative and counts the rest.

Ported from `fairscape_graph_tools.pipeline.condense`, keeping only the
evidence-graph entry point (`condense_cache`) and dropping the separate
whole-crate condensation path.

Consumption is read through `crate.used_dataset_ids`, so a PROV-only crate —
whose computations state `prov:used` and nothing else — condenses like any
other. Groups are always written back as `usedDataset`, the spelling that
reading prefers and the viewer draws.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

from fairscape_artifacts.crate import (
    generated_by_ids,
    is_computation,
    is_dataset,
    is_rocrate,
    is_software,
    ref_ids,
    used_dataset_ids,
)

Node = Dict[str, Any]
Signature = Tuple[Any, ...]


def _id_ref(entity_id: str) -> Dict[str, str]:
    return {"@id": entity_id}


def signature(dataset_id: str, index: Dict[str, Node],
              cache: Dict[str, Signature]) -> Signature:
    """A hashable fingerprint of how a dataset came to exist.

    Two datasets share a signature when they went through the same pipeline
    shape — identical format, schema, software and recursive input structure —
    regardless of which concrete instances were involved. Memoized because the
    recursion revisits shared ancestors constantly.
    """
    if dataset_id in cache:
        return cache[dataset_id]

    # Seed before recursing so a provenance cycle terminates instead of
    # blowing the stack.
    cache[dataset_id] = ("unknown", (), None)

    dataset = index.get(dataset_id)
    if dataset is None:
        return cache[dataset_id]

    fmt = dataset.get("format", "unknown")
    schema_ids = tuple(sorted(ref_ids(dataset, "evi:Schema")))
    producers = generated_by_ids(dataset)

    if not producers:
        sig: Signature = (fmt, schema_ids, None)
    else:
        comp_sigs = []
        for comp_id in sorted(producers):
            comp = index.get(comp_id)
            if comp is None:
                comp_sigs.append(((), ()))
                continue
            software = tuple(sorted(ref_ids(comp, "usedSoftware")))
            inputs = tuple(sorted(
                signature(ds_id, index, cache)
                for ds_id in used_dataset_ids(comp)
            ))
            comp_sigs.append((software, inputs))
        sig = (fmt, schema_ids, tuple(sorted(comp_sigs)))

    cache[dataset_id] = sig
    return sig


def _backward_chain(dataset_id: str, index: Dict[str, Node]) -> set:
    """Every entity id reachable walking provenance backwards."""
    visited, stack = set(), [dataset_id]
    while stack:
        current = stack.pop()
        if current in visited:
            continue
        visited.add(current)
        node = index.get(current)
        if node is None:
            continue
        if is_dataset(node):
            stack.extend(generated_by_ids(node))
        if is_computation(node):
            stack.extend(used_dataset_ids(node))
            stack.extend(ref_ids(node, "usedSoftware", "usedMLModel"))
    return visited


def _collect_exclusive(dataset_id: str, representative_id: str,
                       index: Dict[str, Node], collapsed: set) -> None:
    """Mark a member's ancestors for removal, minus anything the
    representative also depends on — that part of the graph must survive."""
    keep = _backward_chain(representative_id, index)
    stack, visited = [dataset_id], set()
    while stack:
        current = stack.pop()
        if current in visited or current in keep:
            continue
        visited.add(current)
        node = index.get(current)
        if node is None or is_software(node):
            continue
        collapsed.add(current)
        if is_dataset(node):
            stack.extend(generated_by_ids(node))
        if is_computation(node):
            stack.extend(used_dataset_ids(node))


def _group_node(consuming_id: str, sig: Signature, member_ids: List[str],
                representative_id: str, index: Dict[str, Node],
                max_member_ids: int = 0) -> Node:
    """Build the `evi:DatasetGroup` node that stands in for the members."""
    representative = index[representative_id]
    count = len(member_ids)
    fmt = sig[0]
    schema_ids = list(sig[1]) if sig[1] else []

    software_ids: List[str] = []
    if sig[2]:
        for comp_software, _ in sig[2]:
            software_ids.extend(comp_software)
    software_ids = sorted(set(software_ids))

    consuming = index.get(consuming_id, {})
    label = str(consuming.get("name", "unknown")).lower().replace(" ", "-")
    slug = str(fmt).replace("/", "_").lstrip(".")
    # A group hanging off the crate root is that crate's outputs; anywhere
    # else it is the consuming computation's inputs.
    side = "outputs" if is_rocrate(consuming) else "inputs"
    group_id = f"ark:group/{label}-{slug}-{side}"

    software_names = [index.get(sid, {}).get("name", sid) for sid in software_ids]
    description = f"{count} {fmt} files with identical provenance structure."
    if software_names:
        description += f" All processed by {', '.join(software_names)}."

    member_list = sorted(member_ids)
    if 0 < max_member_ids < len(member_list):
        hidden = len(member_list) - max_member_ids
        member_list = member_list[:max_member_ids] + [
            f"... and {hidden} more (total: {len(member_ids)})"
        ]

    node: Node = {
        "@id": group_id,
        "@type": ["prov:Entity", "https://w3id.org/EVI#DatasetGroup"],
        "name": f"{representative.get('name', str(fmt) + ' files')} (and {count - 1} similar)",
        "description": description,
        "format": fmt,
        "evi:memberCount": count,
        "evi:representativeDataset": _id_ref(representative_id),
        "evi:commonFormat": fmt,
        "evi:commonSoftware": [_id_ref(sid) for sid in software_ids],
        "evi:provenanceSignature": str(sig),
        "evi:memberIds": member_list,
    }
    if schema_ids:
        node["evi:commonSchema"] = [_id_ref(sid) for sid in schema_ids]
    return node


def condense_cache(node_cache: Dict[str, Node], threshold: Optional[int] = 5,
                   max_member_ids: int = 0) -> Dict[str, Any]:
    """Condense `node_cache` in place; return stats describing what happened.

    Mutates the cache: adds group nodes, drops collapsed members and their
    exclusive ancestors, and rewrites each computation's `usedDataset` to
    point at the group instead of its members.

    A threshold of `None` turns grouping off entirely and leaves the cache
    untouched — what a domain-layer graph wants, since its whole point is
    seeing every node.
    """
    original_count = len(node_cache)
    if threshold is None:
        return {
            "condensed": False,
            "originalEntityCount": original_count,
            "condensedEntityCount": original_count,
            "datasetGroupCount": 0,
        }
    sig_cache: Dict[str, Signature] = {}
    groups: List[Node] = []
    collapsed: set = set()

    # Snapshot: the loop mutates node_cache as it goes.
    for comp_id in [nid for nid, n in node_cache.items() if is_computation(n)]:
        node = node_cache.get(comp_id)
        if node is None:
            continue

        inputs = [
            ds_id for ds_id in used_dataset_ids(node)
            if ds_id in node_cache and is_dataset(node_cache[ds_id])
        ]
        if len(inputs) <= threshold:
            continue

        by_signature: Dict[Signature, List[str]] = defaultdict(list)
        for ds_id in inputs:
            by_signature[signature(ds_id, node_cache, sig_cache)].append(ds_id)

        for sig, member_ids in by_signature.items():
            if len(member_ids) <= threshold:
                continue

            representative_id = sorted(member_ids)[0]
            for member_id in member_ids:
                if member_id != representative_id:
                    _collect_exclusive(member_id, representative_id, node_cache, collapsed)

            group = _group_node(comp_id, sig, member_ids, representative_id,
                                node_cache, max_member_ids)
            groups.append(group)

            members = set(member_ids)
            # Rewritten as `usedDataset` whatever the source spelling was:
            # `used_dataset_ids` prefers it, so a `prov:used` node is left
            # correctly shadowed rather than pointing at collapsed members.
            kept = [_id_ref(i) for i in used_dataset_ids(node)
                    if i not in members]
            kept.append(_id_ref(group["@id"]))
            node["usedDataset"] = kept

    for node_id in collapsed:
        node_cache.pop(node_id, None)
    for group in groups:
        node_cache[group["@id"]] = group

    if not groups:
        return {
            "condensed": False,
            "originalEntityCount": original_count,
            "condensedEntityCount": original_count,
            "datasetGroupCount": 0,
        }

    return {
        "condensed": True,
        "originalEntityCount": original_count,
        "condensedEntityCount": len(node_cache),
        "datasetGroupCount": len(groups),
        "entitiesRemoved": len(collapsed),
        "groups": [
            {
                "memberCount": g["evi:memberCount"],
                "format": g.get("format", "unknown"),
                "groupId": g["@id"],
            }
            for g in groups
        ],
    }
