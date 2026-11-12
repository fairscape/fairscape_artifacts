"""What a crate is made of, one card per constituent crate.

A release crate is a thin root that lists its constituent crates; each of
those is loaded from disk and summarised: counts by kind, file formats and
access, what it took as input, and the shape of the work done in it
("Sample -> raw", "h5 -> h5ad"). A crate with no constituents is summarised
the same way, as a single card for itself.

A card's descriptive metadata is a `CrateMetadata` read from the
constituent's own root, with the release root's stub filling gaps. The
counts and patterns come from walking the constituent's entities against
the release-wide index, so an input that lives in a sibling crate is
attributed to it by name.
"""

from __future__ import annotations

import os
from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from fairscape_artifacts import fields as f
from fairscape_artifacts import outputs as outputs_mod
from fairscape_artifacts.crate import (Crate, METADATA_FILENAME, OUTPUT_FIELDS, SubCrate,
                                       generated_ids, generated_index, has_provenance,
                                       ref_ids, replaces, stored_generated_ids)
from fairscape_artifacts.evidence import GRAPH_HTML
from fairscape_artifacts.models import (Card, CellLine, ContentRow, CrateMetadata, Details,
                                        Pattern, PatternInput, Patterns, Statistics, Tally,
                                        tally)

PREVIEW_HTML = "ro-crate-preview.html"

#: How many distinct patterns a card lists before folding the rest.
PATTERN_LIMIT = 12


@dataclass
class Item:
    """One composition card, plus what the CLI needs to write its preview."""
    card: Card
    crate: Crate
    rel_dir: str                 # POSIX, relative to the release crate dir
    preview_path: str = ""       # absolute; "" when previews are off


@dataclass
class Composition:
    items: List[Item] = field(default_factory=list)
    index: Dict[str, dict] = field(default_factory=dict)
    owner: Dict[str, str] = field(default_factory=dict)
    #: crate root id -> display name, for showing `owner`
    names: Dict[str, str] = field(default_factory=dict)
    is_release: bool = False

    @property
    def crates(self) -> List[Crate]:
        return [item.crate for item in self.items]

    @property
    def cards(self) -> List[Card]:
        return [item.card for item in self.items]


# -- cross-crate index -------------------------------------------------------

def build_index(crate: Crate, subcrates: List[SubCrate]):
    """`(index, owner, names)`: every entity across the release by id, the
    root id of the crate it belongs to, and each crate's display name.

    Owners are root ids, not names: two constituents can share a name (or
    fall back to colliding ids), and attribution must still tell them apart.

    A sub-crate's own copy of an entity wins over the release root's stub
    of it, which is why sub-crates are indexed last; between two full
    copies, the one with provenance wins (`crate.replaces`).
    """
    index: Dict[str, dict] = {}
    owner: Dict[str, str] = {}
    names: Dict[str, str] = {crate.root_id: crate.name}
    for node in crate.graph:
        nid = node.get("@id")
        if nid:
            index[nid] = node
            owner[nid] = crate.root_id
    # linked crates first, then constituents: a constituent's copy is the
    # most authoritative, a linked crate's copy beats this crate's stub
    for sub in list(crate.linked_closure()) + list(subcrates):
        names[sub.crate.root_id] = sub.crate.name
        for node in sub.crate.graph:
            nid = node.get("@id")
            if not nid or (nid in index and not replaces(index[nid], node)):
                continue
            index[nid] = node
            owner[nid] = sub.crate.root_id
    return index, owner, names


# -- per-crate details -------------------------------------------------------

#: Display order and label for each kind in a single crate's contents table.
KIND_LABELS = (
    ("dataset", "Dataset"),
    ("mlmodel", "ML model"),
    ("software", "Software"),
    ("computation", "Computation"),
    ("experiment", "Experiment"),
    ("sample", "Sample"),
    ("instrument", "Instrument"),
    ("schema", "Schema"),
    ("rocrate", "RO-Crate"),
    ("other", "Other"),
)


def _content_url(node) -> str:
    url = node.get("contentUrl")
    if isinstance(url, list):
        url = url[0] if url else None
    return f.http_url(url)


def contents(crate: Crate, link_base: str = "") -> List[ContentRow]:
    """One row per entity in the crate, grouped by kind then by name: what a
    single crate's card lists so its datasheet shows everything it holds,
    not just the counts."""
    order = {kind: i for i, (kind, _) in enumerate(KIND_LABELS)}
    labels = dict(KIND_LABELS)
    rows = []
    for node in crate.entities():
        kind = f.bucket(node)
        nid = node.get("@id", "")
        name = f.first(node, "name") or nid
        fmt = f.format_label(node)
        status = f.access(node) if kind in ("dataset", "software", "mlmodel") else ""
        description, cut = f.truncate(f.first(node, "description"), 160)
        rows.append(((order[kind], name.lower()), ContentRow(
            kind=labels[kind], name=name, id=nid, id_href=f.ark_href(nid, link_base),
            description=description + ("…" if cut else ""), format=fmt,
            size=f.human_size(node.get("contentSize")), access=status,
            access_href=_content_url(node) if status == "Available" else "",
            search=" ".join([name, labels[kind], fmt, nid]).lower())))
    rows.sort(key=lambda pair: pair[0])
    return [row for _, row in rows]


# -- work patterns -----------------------------------------------------------

def _pattern_key(pattern: Pattern) -> str:
    ins = ";".join(f"{i.crate or ''}:{'+'.join(i.formats)}" for i in pattern.inputs)
    return ins + "->" + "+".join(pattern.outputs)


def _output_formats(node, index, generated) -> List[str]:
    out = []
    for oid in generated_ids(node, generated):
        target = index.get(oid)
        if target:
            out.extend(f.formats_of(target))
    return sorted(set(out))


def _computation_pattern(node, index, owner, here: str, generated,
                         names: Dict[str, str]) -> Optional[Pattern]:
    """`inputs -> outputs` in formats, naming the crate an input came from
    when it is not this one."""
    used = ref_ids(node, "usedDataset")
    if not used:
        used = [i for i in ref_ids(node, "prov:used")
                if i in index and f.bucket(index[i]) == "dataset"]
    by_crate: Dict[Optional[str], set] = {}
    for did in used:
        target = index.get(did)
        if not target:
            continue
        formats = f.formats_of(target)
        if not formats:
            continue
        source = owner.get(did)
        key = names.get(source, source) if source and source != here else None
        by_crate.setdefault(key, set()).update(formats)
    outputs = _output_formats(node, index, generated)
    if not by_crate or not outputs:
        return None
    inputs = [PatternInput(crate=crate_name, formats=sorted(formats))
              for crate_name, formats in by_crate.items()]
    inputs.sort(key=lambda i: (i.crate or "", i.formats))
    return Pattern(inputs=inputs, outputs=outputs)


def _experiment_pattern(node, index, generated) -> Optional[Pattern]:
    outputs = _output_formats(node, index, generated)
    if not outputs:
        return None
    return Pattern(inputs=[PatternInput(formats=["Sample"])], outputs=outputs)


def _fold_patterns(patterns: List[Pattern]) -> Patterns:
    """Distinct patterns, commonest first, capped at `PATTERN_LIMIT`."""
    counts: Counter = Counter()
    first_seen: Dict[str, Pattern] = {}
    for pattern in patterns:
        key = _pattern_key(pattern)
        counts[key] += 1
        first_seen.setdefault(key, pattern)
    rows = [first_seen[key].model_copy(update={"n": n}) for key, n in counts.most_common()]
    return Patterns(rows=rows[:PATTERN_LIMIT], more=max(0, len(rows) - PATTERN_LIMIT))


def _cell_line(sample, index) -> Optional[CellLine]:
    refs = ref_ids(sample, "cellLineReference", "derivedFrom")
    if not refs:
        return None
    ref = refs[0]
    target = index.get(ref, {})
    organism = target.get("organism")
    organism_name = f.text(organism) if organism else ""
    if organism_name and organism_name in index:
        organism_name = f.first(index[organism_name], "name") or organism_name
    return CellLine(id=ref, name=f.first(target, "name") or ref, organism=organism_name)


def _input_datasets(crate: Crate, index, owner, names: Dict[str, str]) -> List[Tally]:
    """What the crate declares (or, failing that, derives) as its inputs,
    tallied by format and, for inputs from another crate, by that crate."""
    here = crate.root_id
    declared = bool(outputs_mod.stored(crate.root)[0])
    inputs, _ = outputs_mod.ensure(crate)
    labels: List[str] = []
    for ref in inputs:
        iid = ref.get("@id") if isinstance(ref, dict) else ref
        if not iid:
            continue
        node = index.get(iid)
        if node is None:
            # A declared input that cannot be found is worth flagging; a
            # derived one is just a reference to something outside the
            # crate (a workflow's scratch file, say) and is not an input.
            if declared:
                labels.append("unresolved reference")
            continue
        kind = f.bucket(node)
        if kind == "rocrate":
            # An input crate contributes each of its outputs.
            name = f.first(node, "name") or iid
            for oid in ref_ids(node, *OUTPUT_FIELDS):
                target = index.get(oid)
                fmt = f.format_label(target) if target else ""
                labels.append(f"{name} ({fmt})" if fmt else name)
            continue
        label = "Sample" if kind == "sample" else (f.format_label(node) or kind.capitalize())
        source = owner.get(iid)
        if source and source != here:
            label = f"{names.get(source, source)} ({label})"
        labels.append(label)
    return tally(labels)


def details(crate: Crate, index, owner, generated: Optional[Dict[str, List[str]]] = None,
            names: Optional[Dict[str, str]] = None) -> Details:
    """Counts, formats, access, inputs and work patterns for one crate."""
    here = crate.root_id
    names = names or {}
    if generated is None:
        generated = generated_index(index)
    counts: Counter = Counter()
    provenance = 0
    formats: Dict[str, List[str]] = {"dataset": [], "software": [], "mlmodel": []}
    access: Dict[str, List[str]] = {"dataset": [], "software": []}
    computation_patterns: List[Pattern] = []
    experiment_patterns: List[Pattern] = []
    experiment_types: List[str] = []
    cell_lines: Dict[str, CellLine] = {}
    organisms: List[str] = []

    for node in crate.entities():
        kind = f.bucket(node)
        if kind == "rocrate":
            continue
        counts[kind] += 1
        if kind in formats:
            formats[kind].extend(f.formats_of(node))
        if kind in access:
            access[kind].append(f.access(node))
        if kind == "dataset" and has_provenance(node):
            provenance += 1
        elif kind == "sample":
            line = _cell_line(node, index)
            if line:
                cell_lines.setdefault(line.id, line)
                if line.organism:
                    organisms.append(line.organism)
        elif kind == "experiment":
            experiment_types.append(f.first(node, "experimentType") or "Unspecified")
            pattern = _experiment_pattern(node, index, generated)
            if pattern:
                experiment_patterns.append(pattern)
        elif kind == "computation":
            pattern = _computation_pattern(node, index, owner, here, generated, names)
            if pattern:
                computation_patterns.append(pattern)

    input_datasets = _input_datasets(crate, index, owner, names)
    if not input_datasets and counts["sample"]:
        input_datasets = [Tally(label="Sample", n=counts["sample"])]

    return Details(
        files=counts["dataset"], models=counts["mlmodel"], software=counts["software"],
        instruments=counts["instrument"], samples=counts["sample"],
        experiments=counts["experiment"], computations=counts["computation"],
        schemas=counts["schema"], other=counts["other"], with_provenance=provenance,
        file_formats=tally(formats["dataset"]), software_formats=tally(formats["software"]),
        model_formats=tally(formats["mlmodel"]), file_access=tally(access["dataset"]),
        software_access=tally(access["software"]),
        computation_patterns=_fold_patterns(computation_patterns),
        experiment_patterns=_fold_patterns(experiment_patterns),
        experiment_types=tally(experiment_types), cell_lines=list(cell_lines.values()),
        organisms=tally(organisms), input_datasets=input_datasets,
    )


def notes(crate: Crate, generated: Dict[str, List[str]]) -> List[str]:
    """What the card filled in that the crate does not store, and the
    command that would store it."""
    out = []
    for node in crate.entities():
        if f.bucket(node) not in ("computation", "experiment"):
            continue
        stored = set(stored_generated_ids(node))
        if any(oid not in stored for oid in generated.get(node.get("@id", ""), [])):
            out.append("Activity outputs were derived from each output's generatedBy; "
                       "run `fairscape-artifacts link-inverses` to store them.")
            break
    stored_inputs, stored_outputs = outputs_mod.stored(crate.root)
    if stored_outputs and not stored_inputs and outputs_mod.ensure(crate)[0]:
        out.append("Inputs were derived from the graph (only outputs are stored); "
                   "run `fairscape-artifacts add-io` to store them.")
    return out


# -- files beside the crate --------------------------------------------------

def _dir_size(path: str) -> int:
    total = 0
    for dirpath, _, filenames in os.walk(path):
        for name in filenames:
            full = os.path.join(dirpath, name)
            if not os.path.islink(full):
                try:
                    total += os.path.getsize(full)
                except OSError:
                    pass
    return total


def _within(path: str, directory: str) -> bool:
    return os.path.commonpath([path, directory]) == directory


def _href(target_abs: str, crate_dir: str, out_dir: str,
          page_dir: Optional[str] = None) -> str:
    """A link from a page in `page_dir` (default `out_dir`) to `target_abs`.

    Pages written somewhere else (`-d`) link to the crate's own files as if
    `out_dir` sat at the crate root, so the output stays self-contained and
    works once published beside the crate, rather than pointing back at
    wherever the crate happened to live on the build machine.
    """
    target = os.path.normpath(os.path.abspath(target_abs))
    out_dir = os.path.normpath(os.path.abspath(out_dir))
    crate_dir = os.path.normpath(os.path.abspath(crate_dir))
    if not _within(target, out_dir) and _within(target, crate_dir):
        target = os.path.join(out_dir, os.path.relpath(target, crate_dir))
    return os.path.relpath(target, page_dir or out_dir).replace(os.sep, "/")


def summary_statistics(crate: Crate, crate_dir: str, out_dir: str,
                       page_dir: Optional[str] = None) -> Optional[Statistics]:
    """The QC / summary-statistics report a crate points at, as a link."""
    refs = ref_ids(crate.root, "hasSummaryStatistics")
    target = crate.get(refs[0]) if refs else None
    if not target:
        return None
    url = target.get("contentUrl")
    if isinstance(url, list):
        url = url[0] if url else None
    if not isinstance(url, str) or not url:
        return None
    name = f.first(target, "name") or "Summary statistics"
    if url.startswith(("http://", "https://")):
        return Statistics(name=name, href=url)
    local = crate.file_path(target)
    if local:
        return Statistics(name=name, href=_href(local, crate_dir, out_dir, page_dir))
    return None


def _evidence_href(crate: Crate, root: dict, *, rel_dir: str, sub_dir_abs: str,
                   crate_dir: str, out_dir: str, page_dir: Optional[str],
                   link_base: str, published: bool) -> str:
    """Where a card's provenance graph is: one this run wrote, one already
    beside the crate, or the `localEvidenceGraph` the metadata names."""
    if published:
        # a graph this run wrote first, then one already beside the crate
        for candidate in ((os.path.join(out_dir, rel_dir, GRAPH_HTML) if rel_dir else ""),
                          os.path.join(sub_dir_abs, GRAPH_HTML)):
            if candidate and os.path.exists(candidate):
                return _href(candidate, crate_dir, out_dir, page_dir)
    local_graph = ref_ids(root, "localEvidenceGraph")
    if not local_graph:
        return ""
    target = local_graph[0]
    if not target.endswith(".html"):
        return f.ark_href(target, link_base)
    if not published:
        return ""
    # Written relative to the release (CM4AI writes it that way on the stub
    # *and* on the constituent's own root) or to the constituent: take
    # whichever holds the file, else the constituent's directory when its
    # own root carries it.
    candidates = [os.path.join(sub_dir_abs, target), os.path.join(crate_dir, target)]
    found = [c for c in candidates if os.path.exists(c)]
    fallback = candidates[0] if ref_ids(crate.root, "localEvidenceGraph") else candidates[1]
    return _href(found[0] if found else fallback, crate_dir, out_dir, page_dir)


# -- cards -------------------------------------------------------------------

def card(crate: Crate, *, stub: Optional[dict], main: CrateMetadata, rel_dir: str,
         index, owner, crate_dir: str, out_dir: str, link_base: str, preview: bool,
         position: int, generated: Optional[Dict[str, List[str]]] = None,
         names: Optional[Dict[str, str]] = None, page_dir: Optional[str] = None,
         list_contents: bool = False, published: bool = True) -> Card:
    """One composition card.

    `list_contents` adds a row per entity (`contents`); `build` sets it for
    a crate with no constituents, whose one card is the whole composition.

    `published=False` is a sub-crate whose own metadata is not beside the
    release (`SubCrate.published`): the card is the stub's description
    only, and nothing on disk (size, evidence graph, preview) is credited
    to it, since the only directory to look in is the release's own.

    The sub-crate's own root wins; the release root's stub fills gaps; the
    release root itself (`main`) supplies a DOI and publications when
    neither has them, since constituent crates usually inherit those.
    """
    root: dict = {}
    for source in (stub or {}, crate.root):
        for key, value in source.items():
            if value not in (None, "", [], {}):
                root[key] = value
    meta = CrateMetadata.read(root, index, link_base)
    if stub:
        inherited = {}
        if not meta.identifier:
            inherited.update(identifier_label=main.identifier_label, identifier=main.identifier,
                             identifier_href=main.identifier_href)
        if not meta.publications:
            inherited.update(publications=main.publications)
        meta = meta.model_copy(update=inherited)

    sub_dir_abs = os.path.normpath(os.path.join(crate_dir, rel_dir)) if rel_dir else crate_dir
    size = meta.size
    if not size and published and os.path.isdir(sub_dir_abs):
        size = f.human_size(_dir_size(sub_dir_abs))

    evidence_href = _evidence_href(crate, root, rel_dir=rel_dir, sub_dir_abs=sub_dir_abs,
                                   crate_dir=crate_dir, out_dir=out_dir, page_dir=page_dir,
                                   link_base=link_base, published=published)
    preview_href = ""
    if preview and published:
        preview_href = _href(os.path.join(out_dir, rel_dir, PREVIEW_HTML),
                             out_dir, out_dir, page_dir)

    if generated is None:
        generated = generated_index(index)
    det = details(crate, index, owner, generated, names)
    return Card(
        position=position,
        anchor=f"subcrate-{position}",
        meta=meta,
        size=size,
        evidence_href=evidence_href,
        evidence_label=("Open" if evidence_href.startswith(("http://", "https://"))
                        else os.path.basename(evidence_href)) if evidence_href else "",
        preview_href=preview_href,
        statistics=summary_statistics(crate, crate_dir, out_dir, page_dir),
        metadata_path="/".join(p for p in (rel_dir, METADATA_FILENAME) if p),
        details=det,
        contents=contents(crate, link_base) if list_contents else [],
        notes=notes(crate, generated) if published else [
            "This sub-crate's own metadata is not published with the release "
            "(access to its data is controlled), so only the release's "
            "description of it is shown; its contents are not counted."],
        published=published,
        search=" ".join([meta.name, " ".join(meta.keywords),
                         " ".join(row.label for row in det.file_formats)]).lower(),
    )


# -- entry point ---------------------------------------------------------------

def build(crate: Crate, *, out_dir: str, link_base: str = "",
          previews: bool = True, page_dir: Optional[str] = None) -> Composition:
    """Cards for a crate's constituents, or for the crate itself.

    Previews go under `out_dir`; card links are relative to `page_dir`, the
    directory the datasheet itself is written to (default `out_dir`).
    """
    crate_dir = crate.dir or out_dir
    subcrates = crate.sub_crates()
    index, owner, names = build_index(crate, subcrates)
    generated = generated_index(index)
    main = CrateMetadata.read(crate.root, index, link_base)
    comp = Composition(index=index, owner=owner, names=names, is_release=bool(subcrates))
    shared = dict(main=main, index=index, owner=owner, crate_dir=crate_dir, out_dir=out_dir,
                  link_base=link_base, preview=previews, generated=generated, names=names,
                  page_dir=page_dir)

    if subcrates:
        for position, sub in enumerate(subcrates, start=1):
            item = card(sub.crate, stub=sub.stub, rel_dir=sub.rel_dir, position=position,
                        published=sub.published, **shared)
            preview_path = (os.path.join(out_dir, sub.rel_dir, PREVIEW_HTML)
                            if previews and sub.published else "")
            comp.items.append(Item(card=item, crate=sub.crate, rel_dir=sub.rel_dir,
                                   preview_path=preview_path))
    else:
        item = card(crate, stub=None, rel_dir="", position=1, list_contents=True, **shared)
        preview_path = os.path.join(out_dir, PREVIEW_HTML) if previews else ""
        comp.items.append(Item(card=item, crate=crate, rel_dir="", preview_path=preview_path))
    return comp
