"""Load an RO-Crate from disk and index it.

Everything else in this package takes a `Crate`. It is a thin wrapper over
the parsed `ro-crate-metadata.json`: the `@graph` list, an `{@id: node}`
index, and the root entity. No validation, no pydantic — the artifact
builders read fields defensively because real crates are uneven.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

METADATA_FILENAME = "ro-crate-metadata.json"

#: On a release crate, each constituent crate is a node carrying the path of
#: its own metadata file, relative to the release crate's directory. The same
#: field on a node the root does *not* list in `hasPart` marks a *linked*
#: crate: an upstream crate this one only points at because it consumed
#: something the upstream produced (see `Crate.linked_crates`).
SUBCRATE_PATH_FIELD = "ro-crate-metadata"

#: How many pointer hops the linked-crate walk follows (a linked crate may
#: itself link onward). Cycles are cut by metadata path regardless.
LINKED_DEPTH = 4

#: Reference fields whose values are `{"@id": ...}` (or lists/strings thereof).
REF_FIELDS = (
    "hasPart", "EVI:outputs", "outputs",
    "generatedBy", "prov:wasGeneratedBy",
    "usedDataset", "usedSoftware", "usedMLModel",
    "derivedFrom", "prov:wasDerivedFrom",
    "usedSample", "usedInstrument", "usedTreatment", "usedStain",
    "generated", "prov:used", "used",
    "prov:specializationOf", "specializationOf",
    "wasGeneratedBy", "wasDerivedFrom",
)


def short_types(node: Dict[str, Any]) -> List[str]:
    """`@type` as a list of bare type names, prefixes and URLs stripped.

    `"EVI:Dataset"`, `"https://w3id.org/EVI#Dataset"` and `["Dataset"]` all
    come back as `["Dataset"]`.
    """
    raw = node.get("@type") or []
    if isinstance(raw, str):
        raw = [raw]
    out = []
    for t in raw:
        t = str(t)
        for sep in ("#", "/", ":"):
            if sep in t:
                t = t.rsplit(sep, 1)[-1]
        if t:
            out.append(t)
    return out


def has_type(node: Dict[str, Any], *names: str) -> bool:
    """True if any of `names` appears in the node's normalized `@type`."""
    types = short_types(node)
    return any(n in types for n in names)


def ref_ids(node: Dict[str, Any], *fields: str) -> List[str]:
    """Collect `@id` strings out of one or more reference fields.

    Tolerates every shape crates use in practice: a bare string, a single
    `{"@id": ...}`, a list of either, or the field being absent.
    """
    ids: List[str] = []
    for field in fields:
        value = node.get(field)
        if value is None:
            continue
        items = value if isinstance(value, list) else [value]
        for item in items:
            if isinstance(item, str):
                ids.append(item)
            elif isinstance(item, dict) and item.get("@id"):
                ids.append(item["@id"])
    return ids


@dataclass
class SubCrate:
    """A constituent crate of a release crate, loaded from disk.

    `rel_dir` is the sub-crate's directory relative to the parent crate's,
    as a POSIX path ("" would mean the parent itself, which never happens
    here), so links from the parent's pages can be formed without knowing
    where either crate lives on disk.
    """
    stub: Dict[str, Any]      # the node in the parent's graph
    crate: "Crate"            # the sub-crate's own metadata, parsed
    metadata_path: str        # POSIX, relative to the parent crate directory

    @property
    def rel_dir(self) -> str:
        return os.path.dirname(self.metadata_path)

    @property
    def id(self) -> str:
        return self.crate.root_id or self.stub.get("@id", "")


class Crate:
    """A parsed RO-Crate plus the two lookups every builder needs."""

    def __init__(self, data: Dict[str, Any], path: Optional[str] = None):
        self.data = data
        self.path = path
        self.dir = os.path.dirname(os.path.abspath(path)) if path else None

        graph = data.get("@graph") or []
        self.graph: List[Dict[str, Any]] = [n for n in graph if isinstance(n, dict)]
        self.index: Dict[str, Dict[str, Any]] = {
            n["@id"]: n for n in self.graph if n.get("@id")
        }
        self.root = self._find_root()
        self._sub_crates: Optional[List[SubCrate]] = None
        self._linked: Optional[List[SubCrate]] = None

    # -- construction --------------------------------------------------

    @classmethod
    def load(cls, path: str) -> "Crate":
        """Load from a metadata file, or from a directory containing one."""
        if os.path.isdir(path):
            path = os.path.join(path, METADATA_FILENAME)
        with open(path, encoding="utf-8") as handle:
            return cls(json.load(handle), path=path)

    # -- root ----------------------------------------------------------

    def _find_root(self) -> Dict[str, Any]:
        """The crate's root data entity.

        Preferred route is the RO-Crate spec's: the descriptor entity
        (`ro-crate-metadata.json`) points at the root through `about`. Real
        crates in this corpus don't always carry a descriptor, so fall back
        to a node typed ROCrate, then to `./`, then to the first node.
        """
        descriptor = self.index.get(METADATA_FILENAME)
        if descriptor:
            about = ref_ids(descriptor, "about")
            if about and about[0] in self.index:
                return self.index[about[0]]

        for node in self.graph:
            if node.get("@id") != METADATA_FILENAME and has_type(node, "ROCrate", "Dataset"):
                if has_type(node, "ROCrate"):
                    return node

        if "./" in self.index:
            return self.index["./"]
        return self.graph[0] if self.graph else {}

    @property
    def root_id(self) -> str:
        return self.root.get("@id", "")

    @property
    def name(self) -> str:
        name = self.root.get("name")
        return name.strip() if isinstance(name, str) and name.strip() else self.root_id

    # -- sub-crates ----------------------------------------------------

    def _stubs(self) -> List[Dict[str, Any]]:
        return [n for n in self.graph
                if n.get(SUBCRATE_PATH_FIELD) and n.get("@id") != self.root_id]

    def sub_crate_stubs(self) -> List[Dict[str, Any]]:
        """Nodes in this graph that stand for constituent crates on disk:
        crate stubs the root lists in `hasPart`. (A root with no `hasPart` at
        all keeps the old reading, where every stub is a constituent.)"""
        stubs = self._stubs()
        parts = set(ref_ids(self.root, "hasPart"))
        if not parts:
            return stubs
        return [n for n in stubs if n.get("@id") in parts]

    def linked_crate_stubs(self) -> List[Dict[str, Any]]:
        """Crate stubs the root does not contain: upstream crates this one
        points at because it consumed something they produced."""
        parts = set(ref_ids(self.root, "hasPart"))
        if not parts:
            return []
        return [n for n in self._stubs() if n.get("@id") not in parts]

    def _load_stub(self, stub: Dict[str, Any], kind: str) -> Optional["SubCrate"]:
        """Load the crate a stub points at. The pointer is relative to this
        crate's directory; an absolute `localPath` on the stub is the
        fallback when the relative one no longer resolves."""
        rel = str(stub[SUBCRATE_PATH_FIELD]).replace(os.sep, "/")
        while rel.startswith("./"):
            rel = rel[2:]
        full = os.path.normpath(os.path.join(self.dir, rel))
        candidates = [full]
        local = stub.get("localPath")
        if isinstance(local, str) and local and os.path.normpath(local) != full:
            candidates.append(os.path.normpath(local))
        err: Optional[Exception] = None
        for path in candidates:
            try:
                sub = Crate.load(path)
            except (OSError, ValueError) as e:
                err = err or e
                continue
            return SubCrate(stub=stub, crate=sub, metadata_path=rel)
        print(f"warning: {kind} {stub.get('@id', rel)!r}: {err}", file=sys.stderr)
        return None

    def sub_crates(self) -> List[SubCrate]:
        """Constituent crates, loaded once from disk and cached.

        A stub whose metadata file is missing or unparsable is reported on
        stderr and skipped, so one broken sub-crate does not take the whole
        release down with it. A crate loaded from memory (no path) has no
        directory to resolve against and so has no sub-crates.
        """
        if self._sub_crates is not None:
            return self._sub_crates
        self._sub_crates = []
        if not self.dir:
            return self._sub_crates
        for stub in self.sub_crate_stubs():
            loaded = self._load_stub(stub, "sub-crate")
            if loaded:
                self._sub_crates.append(loaded)
        return self._sub_crates

    def linked_crates(self) -> List[SubCrate]:
        """Upstream crates this crate points at, loaded once and cached.

        Same loading rules as `sub_crates`; the difference is what the
        pointer means. A constituent is *part of* this crate. A linked crate
        is where an input of this crate came from: the consumer carries a
        stub of the shared entity under the upstream's own `@id`, and the
        upstream carries the entity's provenance.
        """
        if self._linked is not None:
            return self._linked
        self._linked = []
        if not self.dir:
            return self._linked
        for stub in self.linked_crate_stubs():
            loaded = self._load_stub(stub, "linked crate")
            if loaded:
                self._linked.append(loaded)
        return self._linked

    def linked_closure(self, depth: int = LINKED_DEPTH) -> List[SubCrate]:
        """Linked crates, and theirs, breadth-first, each once."""
        out: List[SubCrate] = []
        seen = {os.path.abspath(self.path)} if self.path else set()
        frontier: List[Crate] = [self]
        for _ in range(max(depth, 0)):
            nxt: List[Crate] = []
            for crate in frontier:
                for sub in crate.linked_crates():
                    key = os.path.abspath(sub.crate.path) if sub.crate.path else id(sub)
                    if key in seen:
                        continue
                    seen.add(key)
                    out.append(sub)
                    nxt.append(sub.crate)
            if not nxt:
                break
            frontier = nxt
        return out

    def provenance_index(self, depth: int = LINKED_DEPTH) -> "Tuple[Dict[str, Dict[str, Any]], Dict[str, str]]":
        """`(index, owner)` spanning this crate and every crate it links to.

        A linked crate's own copy of an entity wins over this crate's stub
        of it — the stub deliberately carries no provenance, the copy does.
        `owner` maps every id to the root id of the crate that supplied it.
        """
        index: Dict[str, Dict[str, Any]] = dict(self.index)
        owner: Dict[str, str] = {nid: self.root_id for nid in self.index}
        for sub in self.linked_closure(depth):
            for nid, node in sub.crate.index.items():
                index[nid] = node
                owner[nid] = sub.crate.root_id
        return index, owner

    # -- access --------------------------------------------------------

    def get(self, node_id: str) -> Optional[Dict[str, Any]]:
        return self.index.get(node_id)

    def of_type(self, *names: str) -> Iterable[Dict[str, Any]]:
        """Every non-descriptor node carrying one of `names` as a type."""
        for node in self.graph:
            if node.get("@id") == METADATA_FILENAME:
                continue
            if has_type(node, *names):
                yield node

    def file_path(self, node: Dict[str, Any]) -> Optional[str]:
        """Absolute path for a node's `contentUrl`, if it resolves locally.

        Crates in this corpus write `contentUrl` as `file:///<path>` where the
        path is relative to the crate directory, so the scheme is stripped and
        the remainder joined against `self.dir` rather than treated as
        absolute.
        """
        url = node.get("contentUrl")
        if not isinstance(url, str) or not self.dir:
            return None
        relative = url[len("file:///"):] if url.startswith("file:///") else url
        if relative.startswith("file://"):
            relative = relative[len("file://"):]
        if not relative or "://" in relative:
            return None
        return os.path.normpath(os.path.join(self.dir, relative.lstrip("/")))

    def __len__(self) -> int:
        return len(self.graph)

    def __repr__(self) -> str:
        return f"<Crate {self.root_id!r} nodes={len(self.graph)}>"


# ---------------------------------------------------------------------------
# Strict EVI vocabulary (graph algorithms)
# ---------------------------------------------------------------------------
#
# `short_types` above is deliberately loose — good for counting and display.
# The graph traversal needs the stricter reading the condensation pipeline
# uses, where an unrecognized type is *no* type rather than a guess, so a
# `schema.org/Dataset` never gets walked as if it were an EVI Dataset.
#
# On top of that strict reading sits PROV support: crates converted from PROV
# bundles carry `prov:Activity` / `prov:Entity` with no EVI type at all. Those
# are read as Computation and Dataset respectively, but only as a fallback —
# an explicit EVI type always wins.

EVI_TYPES = {
    "Dataset", "Software", "MLModel", "Computation", "Annotation",
    "Experiment", "ROCrate", "CreativeWork", "Schema", "Sample",
    "Instrument", "DatasetGroup",
}

#: A crate may spell PROV terms prefixed, bare (a `prov` @context term) or as
#: full IRIs. All three are the same term, so every PROV tuple below lists all
#: three spellings and every EVI reading is tried first.
_PROV_NS = "http://www.w3.org/ns/prov#"

#: PROV type -> the EVI type it is read as, for nodes carrying no EVI type.
PROV_TYPE_FALLBACK = {
    "prov:Activity": "Computation", "prov:Entity": "Dataset",
    f"{_PROV_NS}Activity": "Computation", f"{_PROV_NS}Entity": "Dataset",
}

#: Generation edges, in precedence order.
GENERATED_BY_FIELDS = ("generatedBy", "prov:wasGeneratedBy", "wasGeneratedBy",
                       f"{_PROV_NS}wasGeneratedBy")
#: Derivation edges, followed only when no generation edge exists.
DERIVED_FROM_FIELDS = ("derivedFrom", "prov:wasDerivedFrom", "wasDerivedFrom",
                       f"{_PROV_NS}wasDerivedFrom")
#: What a computation consumed.
USED_FIELDS = ("usedDataset", "usedSoftware", "usedSample",
               "usedInstrument", "usedMLModel")
#: PROV's untyped consumption edge. An activity in a PROV-only crate — or a
#: CPM receipt activity — carries only this, so it is read as `usedDataset`:
#: the same fallback `prov:wasGeneratedBy` gets on the generation side, and
#: the field the viewer actually draws.
PROV_USED_FIELDS = ("prov:used", "used", f"{_PROV_NS}used")
#: PROV specialization: the SUBJECT is the more specialized entity (PROV-DM
#: §5.5.1), the object its general stand-in. In CPM crates this is the only
#: link between the backbone and the domain layer.
SPECIALIZATION_FIELDS = ("prov:specializationOf", "specializationOf",
                         f"{_PROV_NS}specializationOf")


def _raw_types(node: Dict[str, Any]) -> List[str]:
    raw = node.get("@type") or []
    return [raw] if isinstance(raw, str) else [str(t) for t in raw]


def evi_short_types(node: Dict[str, Any]) -> List[str]:
    """`@type` filtered to the known EVI vocabulary."""
    out = []
    for t in _raw_types(node):
        short = t.split("#")[-1] if "#" in t else (t.split(":")[-1] if ":" in t else t)
        if short in EVI_TYPES:
            out.append(short)
    return out


def evi_type(node: Dict[str, Any]) -> Optional[str]:
    """The node's primary EVI type, or None.

    Falls back to the PROV reading for `prov:Activity` / `prov:Entity` nodes
    that carry no EVI type, so a PROV-only crate — one with no EVI vocabulary
    anywhere — still traverses.
    """
    shorts = evi_short_types(node)
    if not shorts:
        for raw in _raw_types(node):
            if raw in PROV_TYPE_FALLBACK:
                return PROV_TYPE_FALLBACK[raw]
        return None
    for preferred in ("ROCrate", "Computation", "Software", "MLModel",
                      "Experiment", "Annotation", "Schema"):
        if preferred in shorts:
            return preferred
    return shorts[0]


def is_dataset(node: Dict[str, Any]) -> bool:
    """A data entity — but never the crate root, which is typed Dataset too."""
    types = evi_short_types(node)
    if types:
        return "Dataset" in types and "ROCrate" not in types
    return evi_type(node) == "Dataset"


def is_computation(node: Dict[str, Any]) -> bool:
    return evi_type(node) == "Computation"


def is_software(node: Dict[str, Any]) -> bool:
    return evi_type(node) == "Software"


def is_rocrate(node: Dict[str, Any]) -> bool:
    return "ROCrate" in evi_short_types(node)


def generated_by_ids(node: Dict[str, Any]) -> List[str]:
    return ref_ids(node, *GENERATED_BY_FIELDS)


def derived_from_ids(node: Dict[str, Any]) -> List[str]:
    return ref_ids(node, *DERIVED_FROM_FIELDS)


def used_dataset_ids(node: Dict[str, Any]) -> List[str]:
    """What this activity consumed as data: `usedDataset`, else `prov:used`."""
    return ref_ids(node, "usedDataset") or ref_ids(node, *PROV_USED_FIELDS)


def specialization_of_ids(node: Dict[str, Any]) -> List[str]:
    """The general entities this node is a specialization of."""
    return ref_ids(node, *SPECIALIZATION_FIELDS)


def provenance_edge(node: Dict[str, Any]) -> Tuple[Optional[str], List[str]]:
    """How this entity came to be: `("generatedBy" | "derivedFrom", ids)`.

    Generation wins when both are present, which is why the projected graph
    never carries both edges on one node. Derivation is the fallback that lets
    a PROV-converted crate — where activities were dropped and only
    entity-to-entity `wasDerivedFrom` survives — still form a chain.
    """
    generated = generated_by_ids(node)
    if generated:
        return "generatedBy", generated
    derived = derived_from_ids(node)
    if derived:
        return "derivedFrom", derived
    return None, []
