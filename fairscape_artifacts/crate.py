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
from urllib.parse import unquote

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

#: Where a crate root records what it produced, in precedence order.
OUTPUT_FIELDS = ("https://w3id.org/EVI#outputs", "EVI:outputs", "outputs")

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


def ids_of(value: Any) -> List[str]:
    """The `@id`s a reference-valued field points at.

    Tolerates every shape crates use in practice: a bare string, a single
    `{"@id": ...}`, a list of either, or the field being absent.
    """
    items = value if isinstance(value, list) else [value]
    out: List[str] = []
    for item in items:
        ref = item.get("@id") if isinstance(item, dict) else item
        if isinstance(ref, str) and ref:
            out.append(ref)
    return out


def ref_ids(node: Dict[str, Any], *fields: str) -> List[str]:
    """`ids_of` over one or more fields of a node, in order."""
    return [ref for field in fields for ref in ids_of(node.get(field))]


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
    #: False when the sub-crate's own metadata is not published beside the
    #: release (controlled-access data, say): `crate` is then built from the
    #: stub alone (`Crate.from_stub`) and `metadata_path` is "".
    published: bool = True

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

    @classmethod
    def from_stub(cls, stub: Dict[str, Any]) -> "Crate":
        """A crate known only from the node a release uses to list it.

        The stub is the root and the graph holds nothing else, so the crate
        has a name, description, license and so on but no entities, and, with
        no path, no directory, sub-crates or local files.
        """
        root = {k: v for k, v in stub.items() if k != SUBCRATE_PATH_FIELD}
        descriptor = {"@id": METADATA_FILENAME, "@type": "CreativeWork",
                      "about": {"@id": stub.get("@id", "")}}
        return cls({"@graph": [descriptor, root]})

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
        fallback when the relative one no longer resolves.

        A pointer that resolves to this crate's own metadata file is not the
        sub-crate: a release whose constituents are not published beside it
        (controlled-access data) still writes a bare `ro-crate-metadata.json`
        on each stub, which resolves to the release itself. When nothing
        loads, a sub-crate is kept, built from its stub (`published=False`),
        since the release does list it; a linked crate is skipped.
        """
        rel = str(stub[SUBCRATE_PATH_FIELD]).replace(os.sep, "/")
        while rel.startswith("./"):
            rel = rel[2:]
        own = os.path.normpath(os.path.abspath(self.path)) if self.path else ""
        full = os.path.normpath(os.path.join(self.dir, rel))
        candidates = [full]
        local = stub.get("localPath")
        if isinstance(local, str) and local and os.path.normpath(local) != full:
            candidates.append(os.path.normpath(local))
        err: Optional[Exception] = None
        for path in candidates:
            target = os.path.join(path, METADATA_FILENAME) if os.path.isdir(path) else path
            if own and os.path.normpath(os.path.abspath(target)) == own:
                err = err or ValueError("points at this crate's own metadata file")
                continue
            try:
                sub = Crate.load(path)
            except (OSError, ValueError) as e:
                err = err or e
                continue
            # Record the file that actually loaded, not the pointer: a
            # pointer may name a directory, or be the one that failed before
            # `localPath` resolved, and every link is formed from this path.
            loaded = os.path.relpath(sub.path, self.dir).replace(os.sep, "/")
            return SubCrate(stub=stub, crate=sub, metadata_path=loaded)
        if kind == "sub-crate":
            print(f"note: sub-crate {stub.get('@id', rel)!r} is not published here "
                  f"({err}); described from the release's metadata only", file=sys.stderr)
            return SubCrate(stub=stub, crate=Crate.from_stub(stub), metadata_path="",
                            published=False)
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
        return self._closure(depth, lambda crate: crate.linked_crates())

    def crate_closure(self, depth: int = LINKED_DEPTH) -> List[SubCrate]:
        """Every crate reachable from this one by a pointer, each once.

        Constituents *and* linked crates, and theirs, breadth-first. Both
        kinds hold entities this crate's graph refers to and does not
        describe, which is the only thing resolving a reference cares about.
        """
        return self._closure(depth, lambda crate: crate.sub_crates() + crate.linked_crates())

    def _closure(self, depth: int, step) -> List[SubCrate]:
        out: List[SubCrate] = []
        seen = {os.path.abspath(self.path)} if self.path else set()
        frontier: List[Crate] = [self]
        for _ in range(max(depth, 0)):
            nxt: List[Crate] = []
            for crate in frontier:
                for sub in step(crate):
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

    def provenance_index(self, depth: int = LINKED_DEPTH,
                         pool: "Iterable[Crate]" = ()) -> "Tuple[Dict[str, Dict[str, Any]], Dict[str, str]]":
        """`(index, owner)` spanning every crate that could resolve a
        reference made in this one.

        A reference in a crate is a bare `@id`: nothing in it says which
        crate holds the entity, so an entity described next door reads as a
        dangling id. This gathers the crates that might hold it —

        * this crate;
        * everything `crate_closure` reaches: constituents, the crates this
          one links to, and theirs. Their own copy of an entity wins over a
          stub of it here, because a stub deliberately carries no provenance
          and the copy does;
        * `pool`, crates the caller knows are related but this one does not
          point at — sibling constituents of the same release, say, which
          refer to each other's entities without either owning the other.
          Consulted last and only for ids still unresolved, so a wide pool
          can fill gaps but never overrule a crate that was actually
          pointed at.

        `owner` maps every id to the root id of the crate that supplied it,
        which is how the evidence graph labels a node that came from
        somewhere else. The `ro-crate-metadata.json` descriptor is never
        merged: every crate has one, they differ, and none is ever the
        target of a reference.
        """
        index: Dict[str, Dict[str, Any]] = dict(self.index)
        owner: Dict[str, str] = {nid: self.root_id for nid in self.index}
        for sub in self.crate_closure(depth):
            for nid, node in sub.crate.index.items():
                if nid == METADATA_FILENAME or nid == self.root_id:
                    continue
                if nid in index and not replaces(index[nid], node):
                    continue
                index[nid] = node
                owner[nid] = sub.crate.root_id
        for crate in pool:
            for nid, node in crate.index.items():
                if nid in index or nid == METADATA_FILENAME:
                    continue
                index[nid] = node
                owner[nid] = crate.root_id
        return index, owner

    # -- access --------------------------------------------------------

    def get(self, node_id: str) -> Optional[Dict[str, Any]]:
        return self.index.get(node_id)

    def entities(self) -> Iterable[Dict[str, Any]]:
        """The crate's data entities: no descriptor, no root, no crate stubs."""
        for node in self.graph:
            nid = node.get("@id")
            if nid in (METADATA_FILENAME, self.root_id) or node.get(SUBCRATE_PATH_FIELD):
                continue
            yield node

    def file_path(self, node: Dict[str, Any]) -> Optional[str]:
        """Absolute path for a node's `contentUrl`, if it resolves locally.

        Crates in this corpus write `contentUrl` as `file:///<path>` where the
        path is relative to the crate directory, so the scheme is stripped and
        the remainder joined against `self.dir` rather than treated as
        absolute. Percent-encoding is decoded; a path that would land outside
        the crate directory doesn't resolve.
        """
        url = node.get("contentUrl")
        if not isinstance(url, str) or not self.dir:
            return None
        relative = url[len("file:///"):] if url.startswith("file:///") else url
        if relative.startswith("file://"):
            relative = relative[len("file://"):]
        if not relative or "://" in relative:
            return None
        relative = unquote(relative)
        path = os.path.normpath(os.path.join(self.dir, relative.lstrip("/")))
        # A `../` path naming something outside the crate is not one of its
        # files, and linking to it would point a page at the host filesystem.
        if os.path.commonpath([path, os.path.normpath(self.dir)]) != os.path.normpath(self.dir):
            return None
        return path

    def __len__(self) -> int:
        return len(self.graph)

    def __repr__(self) -> str:
        return f"<Crate {self.root_id!r} nodes={len(self.graph)}>"


def has_provenance(node: Dict[str, Any]) -> bool:
    """Whether a node says where it came from or what it used — the part a
    bare copy or a stub of it leaves out."""
    if node.get(SUBCRATE_PATH_FIELD):
        return False
    return bool(ref_ids(node, *_PROVENANCE_FIELDS))


def replaces(existing: Dict[str, Any], candidate: Dict[str, Any]) -> bool:
    """When several crates describe one id, should `candidate` replace the
    copy already indexed? Yes, unless only the existing copy carries
    provenance."""
    return has_provenance(candidate) or not has_provenance(existing)


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
#: Every spelling that says an entity came from somewhere or an activity
#: consumed something; `has_provenance` reads all of them.
_PROVENANCE_FIELDS = (GENERATED_BY_FIELDS + ("EVI:generatedBy", "evi:generatedBy",
                                             "https://w3id.org/EVI#generatedBy")
                      + DERIVED_FROM_FIELDS + USED_FIELDS + PROV_USED_FIELDS)
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


def generated_index(index: Dict[str, Dict[str, Any]]) -> Dict[str, List[str]]:
    """activity id -> the entities naming it in `generatedBy`.

    Most crates record only that declared side; `generated` on the activity
    is the inverse `link-inverses` adds. Reading both means a crate shows its
    outputs whether or not that was run, and nothing is written to disk.
    """
    out: Dict[str, List[str]] = {}
    for nid, node in index.items():
        for producer in generated_by_ids(node):
            out.setdefault(producer, []).append(nid)
    return out


def stored_generated_ids(node: Dict[str, Any]) -> List[str]:
    return ref_ids(node, "generated", "prov:generated")


def generated_ids(node: Dict[str, Any], generated: Dict[str, List[str]]) -> List[str]:
    """What an activity produced: stored `generated` first, then any
    `generatedBy` inverse (from `generated_index`) it lacks."""
    out = stored_generated_ids(node)
    seen = set(out)
    for oid in generated.get(node.get("@id", ""), []):
        if oid not in seen:
            seen.add(oid)
            out.append(oid)
    return out
