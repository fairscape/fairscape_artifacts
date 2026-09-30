"""Complete the inverse EVI links in an RO-Crate (the old CLI's `augment link-inverses`).

EVI declares its provenance properties in pairs — `generatedBy` / `generated`,
`usedDataset` / `datasetUsedBy`, and so on — and a crate writer normally
records only one side: the dataset says what generated it, the computation
does not list what it generated. Every builder in this package reads the
declared side, so nothing here depends on the inverses being present. They
matter for consumers that walk the graph the other way (a server resolving
"what did this computation produce", a SPARQL view of the crate), which is
why the workflow reporters ran this step on every crate they wrote.

The rule is inherited from `fairscape_cli.entailments.inverse`: for every
`owl:inverseOf` pair `(p, q)` in the EVI ontology and every entity that
carries `p: {"@id": T}` where `T` is also in the graph, make sure `T`
carries `q: {"@id": <the entity>}`. Existing values are kept — the link is
appended, a scalar becomes a two-element list — and nothing is ever removed.

The pairs are a table rather than a runtime ontology parse. The CLI loaded
`evi.xml` with rdflib on every call; the set of inverse pairs is a property
of the EVI vocabulary, not of any crate, so it is pinned here (18 pairs,
extracted from the ontology shipped with fairscape-cli 1.2.10) and the test
suite re-derives it from the ontology whenever rdflib and that checkout are
around. Bare keys are the EVI vocabulary as the FAIRSCAPE `@context` exposes
it. A crate that spells a property `evi:generatedBy` gets its inverse in the
same spelling; the two spellings are never mixed on one entity.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Iterable, List, Sequence, Tuple

EVI_NAMESPACE = "https://w3id.org/EVI#"

#: Every `owl:inverseOf` pair in the EVI ontology, as the bare JSON keys the
#: FAIRSCAPE context maps them to. Order within a pair is alphabetical; the
#: rule is symmetric so it does not matter which side is declared.
INVERSE_PAIRS: Tuple[Tuple[str, str], ...] = (
    ("associateFor", "associatedWith"),
    ("challengedBy", "challenges"),
    ("containedBy", "contains"),
    ("created", "createdBy"),
    ("datasetUsedBy", "usedDataset"),
    ("derivedFrom", "derivedTo"),
    ("describedBy", "describes"),
    ("directlyChallengedBy", "directlyChallenges"),
    ("directlySupportedBy", "directlySupports"),
    ("distributedFrom", "hasDistribution"),
    ("generated", "generatedBy"),
    ("indirectlyChallengedBy", "indirectlyChallenges"),
    ("packagedBy", "packages"),
    ("representedBy", "represents"),
    ("serviceUsedBy", "usedService"),
    ("softwareUsedBy", "usedSoftware"),
    ("supportedBy", "supports"),
    ("used", "usedBy"),
)

#: `{property: its inverse}` in both directions.
INVERSE_OF: Dict[str, str] = {}
for _a, _b in INVERSE_PAIRS:
    INVERSE_OF[_a] = _b
    INVERSE_OF[_b] = _a

#: How a crate may spell an EVI property key: bare (the FAIRSCAPE context),
#: prefixed, or as the full IRI. The inverse is written in the same style.
_SPELLINGS = ("", "evi:", EVI_NAMESPACE)

Addition = Tuple[str, str, str]  # (entity id, property key, id to link)


def _ids(value: Any) -> List[str]:
    """The `@id`s a property value points at; strings are not references."""
    items = value if isinstance(value, list) else [value]
    return [v["@id"] for v in items if isinstance(v, dict) and v.get("@id")]


def _has_link(value: Any, target: str) -> bool:
    return target in _ids(value)


def calculate(graph: Sequence[Dict[str, Any]]) -> List[Addition]:
    """The links a graph is missing, in the order a writer should add them.

    Deterministic: entities in graph order, their keys in document order,
    targets in value order. Each `(entity, key, id)` appears once even when
    several declared links imply it. A link to an id the graph does not
    describe is left alone — there is no entity to write the inverse on.
    """
    index = {n["@id"]: n for n in graph if isinstance(n, dict) and n.get("@id")}
    planned: Dict[Tuple[str, str], List[str]] = {}
    additions: List[Addition] = []

    for node in graph:
        if not isinstance(node, dict) or not node.get("@id"):
            continue
        source_id = node["@id"]
        for prefix in _SPELLINGS:
            for key, value in list(node.items()):
                if not key.startswith(prefix):
                    continue
                bare = key[len(prefix):]
                if prefix == "" and (":" in bare or "/" in bare):
                    continue  # a prefixed key of some other vocabulary
                inverse = INVERSE_OF.get(bare)
                if inverse is None:
                    continue
                inverse_key = prefix + inverse
                for target_id in _ids(value):
                    target = index.get(target_id)
                    if target is None:
                        continue
                    if _has_link(target.get(inverse_key), source_id):
                        continue
                    queued = planned.setdefault((target_id, inverse_key), [])
                    if source_id in queued:
                        continue
                    queued.append(source_id)
                    additions.append((target_id, inverse_key, source_id))
    return additions


def _append_link(entity: Dict[str, Any], key: str, link_id: str) -> None:
    """Add `{"@id": link_id}` under `key`, keeping whatever is there."""
    link = {"@id": link_id}
    current = entity.get(key)
    if current is None:
        entity[key] = [link]
    elif isinstance(current, list):
        current.append(link)
    else:
        entity[key] = [current, link]


def apply(graph: Sequence[Dict[str, Any]]) -> List[Addition]:
    """Add every missing inverse link to `graph` in place; return what was added."""
    index = {n["@id"]: n for n in graph if isinstance(n, dict) and n.get("@id")}
    additions = calculate(graph)
    for entity_id, key, link_id in additions:
        _append_link(index[entity_id], key, link_id)
    return additions


def ensure(crate) -> List[Addition]:
    """`apply` over a loaded `Crate`, in memory."""
    return apply(crate.graph)


def write(path: str) -> Tuple[bool, str]:
    """Persist the missing inverse links into the crate's metadata file.

    Only the entities that gained a link change; everything else in the file
    is preserved as parsed. The old CLI also pruned every null on the way
    out — this does not, matching `outputs.write`.
    """
    from fairscape_artifacts.crate import METADATA_FILENAME, Crate

    if os.path.isdir(path):
        path = os.path.join(path, METADATA_FILENAME)
    if not os.path.exists(path):
        return False, f"RO-Crate metadata file not found at {path}"

    crate = Crate.load(path)
    if not crate.graph:
        return False, "RO-Crate metadata has no @graph"

    additions = apply(crate.graph)
    if not additions:
        return True, f"No inverse links missing in {path}"

    with open(path, "w", encoding="utf-8") as handle:
        json.dump(crate.data, handle, indent=2, ensure_ascii=False)

    touched = len({(e, k) for e, k, _ in additions})
    return True, (f"Added {len(additions)} inverse link(s) on {touched} "
                  f"propert{'y' if touched == 1 else 'ies'} in {path}")


def summary(additions: Iterable[Addition]) -> Dict[str, int]:
    """`{property key: links added}` for a report line."""
    out: Dict[str, int] = {}
    for _, key, _ in additions:
        out[key] = out.get(key, 0) + 1
    return dict(sorted(out.items()))
