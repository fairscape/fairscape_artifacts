"""What a crate is made of, one card per constituent crate.

A release crate is a thin root that lists its constituent crates; each of
those is loaded from disk and summarised: counts by kind, file formats and
access, what it took as input, and the shape of the work done in it
("Sample -> raw", "h5 -> h5ad"). A crate with no constituents is summarised
the same way, as a single card for itself.

Python computes plain data here — counts, tallies, rows, hrefs — and the
templates render it. The patterns in particular used to be HTML fragments
dropped in with `| safe`; they are now structured (`inputs`/`outputs`
lists) so crate-authored text never bypasses autoescaping.
"""

from __future__ import annotations

import os
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from fairscape_artifacts import fields as f
from fairscape_artifacts import outputs as outputs_mod
from fairscape_artifacts.crate import (Crate, METADATA_FILENAME, SubCrate,
                                       generated_by_ids, ref_ids, replaces)

PREVIEW_HTML = "ro-crate-preview.html"
GRAPH_HTML = "ro-crate-evidence-graph.html"

#: How many distinct patterns a card lists before folding the rest.
PATTERN_LIMIT = 12


@dataclass
class Item:
    """One composition card, plus what the CLI needs to write its preview."""
    context: Dict[str, Any]
    crate: Crate
    rel_dir: str                 # POSIX, relative to the release crate dir
    preview_path: str = ""       # absolute; "" when previews are off


@dataclass
class Composition:
    items: List[Item] = field(default_factory=list)
    index: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    owner: Dict[str, str] = field(default_factory=dict)
    #: crate root id -> display name, for showing `owner`
    names: Dict[str, str] = field(default_factory=dict)
    is_release: bool = False

    @property
    def crates(self) -> List[Crate]:
        return [item.crate for item in self.items]


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
    index: Dict[str, Dict[str, Any]] = {}
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

def _entities(crate: Crate):
    """Data entities of one crate: no descriptor, no root, no stubs."""
    for node in crate.graph:
        nid = node.get("@id")
        if nid == METADATA_FILENAME or nid == crate.root_id:
            continue
        if f.is_subcrate_stub(node):
            continue
        yield node


def _pattern_key(pattern: Dict[str, Any]) -> str:
    ins = ";".join(f"{i['crate'] or ''}:{'+'.join(i['formats'])}" for i in pattern["inputs"])
    return ins + "->" + "+".join(pattern["outputs"])


# -- what an activity produced -------------------------------------------------

def derived_generated(index) -> Dict[str, List[str]]:
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


def stored_generated(node) -> List[str]:
    return ref_ids(node, "generated", "prov:generated")


def generated_ids(node, generated: Dict[str, List[str]]) -> List[str]:
    """Stored `generated` first, then any `generatedBy` inverse it lacks."""
    out = stored_generated(node)
    seen = set(out)
    for oid in generated.get(node.get("@id", ""), []):
        if oid not in seen:
            seen.add(oid)
            out.append(oid)
    return out


def _output_formats(node, index, generated) -> List[str]:
    out = []
    for oid in generated_ids(node, generated):
        target = index.get(oid)
        if target:
            out.extend(f.formats_of(target))
    return sorted(set(out))


def _computation_pattern(node, index, owner, here: str, generated,
                         names: Dict[str, str]) -> Optional[Dict[str, Any]]:
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
    inputs = [{"crate": crate_name, "formats": sorted(formats)}
              for crate_name, formats in by_crate.items()]
    inputs.sort(key=lambda i: (i["crate"] or "", i["formats"]))
    return {"inputs": inputs, "outputs": outputs}


def _experiment_pattern(node, index, generated) -> Optional[Dict[str, Any]]:
    outputs = _output_formats(node, index, generated)
    if not outputs:
        return None
    return {"inputs": [{"crate": None, "formats": ["Sample"]}], "outputs": outputs}


def _fold_patterns(patterns: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Distinct patterns, commonest first, capped at `PATTERN_LIMIT`."""
    counts: Counter = Counter()
    first_seen: Dict[str, Dict[str, Any]] = {}
    for pattern in patterns:
        key = _pattern_key(pattern)
        counts[key] += 1
        first_seen.setdefault(key, pattern)
    rows = [{**first_seen[key], "n": n} for key, n in counts.most_common()]
    return {"rows": rows[:PATTERN_LIMIT], "more": max(0, len(rows) - PATTERN_LIMIT)}


def _cell_line(sample, index) -> Optional[Dict[str, str]]:
    refs = ref_ids(sample, "cellLineReference", "derivedFrom")
    if not refs:
        return None
    ref = refs[0]
    target = index.get(ref, {})
    organism = target.get("organism")
    organism_name = f.text(organism) if organism else ""
    if organism_name and organism_name in index:
        organism_name = f.first(index[organism_name], "name") or organism_name
    return {"id": ref, "name": f.first(target, "name") or ref,
            "organism": organism_name}


def _input_datasets(crate: Crate, index, owner,
                    names: Dict[str, str]) -> List[Dict[str, Any]]:
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
            for oid in ref_ids(node, "https://w3id.org/EVI#outputs", "EVI:outputs", "outputs"):
                target = index.get(oid)
                fmt = f.format_label(target) if target else ""
                labels.append(f"{name} ({fmt})" if fmt else name)
            continue
        if kind == "sample":
            label = "Sample"
        else:
            label = f.format_label(node) or kind.capitalize()
        source = owner.get(iid)
        if source and source != here:
            label = f"{names.get(source, source)} ({label})"
        labels.append(label)
    return f.tally(labels)


def details(crate: Crate, index, owner,
            generated: Optional[Dict[str, List[str]]] = None,
            names: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Counts, formats, access, inputs and work patterns for one crate."""
    here = crate.root_id
    names = names or {}
    if generated is None:
        generated = derived_generated(index)
    counts: Counter = Counter()
    provenance = 0
    file_formats: List[str] = []
    software_formats: List[str] = []
    model_formats: List[str] = []
    file_access: List[str] = []
    software_access: List[str] = []
    computation_patterns: List[Dict[str, Any]] = []
    experiment_patterns: List[Dict[str, Any]] = []
    experiment_types: List[str] = []
    cell_lines: Dict[str, Dict[str, str]] = {}
    organisms: List[str] = []

    for node in _entities(crate):
        kind = f.bucket(node)
        if kind == "rocrate":
            continue
        counts[kind] += 1
        if kind == "dataset":
            if f.has_provenance(node):
                provenance += 1
            file_formats.extend(f.formats_of(node))
            file_access.append(f.access(node))
        elif kind == "software":
            software_formats.extend(f.formats_of(node))
            software_access.append(f.access(node))
        elif kind == "mlmodel":
            model_formats.extend(f.formats_of(node))
        elif kind == "sample":
            line = _cell_line(node, index)
            if line:
                cell_lines.setdefault(line["id"], line)
                if line["organism"]:
                    organisms.append(line["organism"])
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
        input_datasets = [{"label": "Sample", "n": counts["sample"]}]

    return {
        "files": counts["dataset"],
        "models": counts["mlmodel"],
        "software": counts["software"],
        "instruments": counts["instrument"],
        "samples": counts["sample"],
        "experiments": counts["experiment"],
        "computations": counts["computation"],
        "schemas": counts["schema"],
        "other": counts["other"],
        "activities": counts["computation"] + counts["experiment"],
        "with_provenance": provenance,
        "file_formats": f.tally(file_formats),
        "software_formats": f.tally(software_formats),
        "model_formats": f.tally(model_formats),
        "file_access": f.tally(file_access),
        "software_access": f.tally(software_access),
        "computation_patterns": _fold_patterns(computation_patterns),
        "experiment_patterns": _fold_patterns(experiment_patterns),
        "experiment_types": f.tally(experiment_types),
        "cell_lines": list(cell_lines.values()),
        "organisms": f.tally(organisms),
        "input_datasets": input_datasets,
        "inputs": sum(row["n"] for row in input_datasets),
    }


def notes(crate: Crate, generated: Dict[str, List[str]]) -> List[str]:
    """What the card filled in that the crate does not store, and the
    command that would store it."""
    out = []
    for node in _entities(crate):
        if f.bucket(node) not in ("computation", "experiment"):
            continue
        stored = set(stored_generated(node))
        if any(oid not in stored for oid in generated.get(node.get("@id", ""), [])):
            out.append("Activity outputs were derived from each output's generatedBy; "
                       "run `fairscape-artifacts link-inverses` to store them.")
            break
    stored_inputs, stored_outputs = outputs_mod.stored(crate.root)
    if stored_outputs and not stored_inputs and outputs_mod.ensure(crate)[0]:
        out.append("Inputs were derived from the graph (only outputs are stored); "
                   "run `fairscape-artifacts add-io` to store them.")
    return out


# -- card metadata -----------------------------------------------------------

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


def resolve_authors(root: Dict[str, Any], index) -> List[str]:
    """Author names, following `{@id}` stubs into the graph."""
    raw = root.get("author")
    entries = raw if isinstance(raw, list) else ([raw] if raw else [])
    names = []
    for entry in entries:
        if isinstance(entry, dict):
            name = f.text(entry.get("name"))
            if not name and entry.get("@id") in index:
                name = f.first(index[entry["@id"]], "name")
            names.append(name or f.text(entry))
        elif isinstance(entry, str):
            if entry in index and f.first(index[entry], "name"):
                names.append(f.first(index[entry], "name"))
            elif entry.strip():
                names.append(entry.strip())
    return [n for n in names if n]


def summary_statistics(crate: Crate, rel_dir: str, crate_dir: str, out_dir: str,
                       page_dir: Optional[str] = None):
    """The QC / summary-statistics report a crate points at, as a link."""
    refs = ref_ids(crate.root, "hasSummaryStatistics")
    if not refs:
        return None
    target = crate.get(refs[0])
    if not target:
        return None
    url = target.get("contentUrl")
    if isinstance(url, list):
        url = url[0] if url else None
    if not isinstance(url, str) or not url:
        return None
    name = f.first(target, "name") or "Summary statistics"
    if url.startswith(("http://", "https://")):
        return {"name": name, "href": url}
    local = crate.file_path(target)
    if local:
        return {"name": name, "href": _href(local, crate_dir, out_dir, page_dir)}
    return None


def card(crate: Crate, *, stub: Optional[Dict[str, Any]], main_root: Dict[str, Any],
         rel_dir: str, index, owner, crate_dir: str, out_dir: str,
         link_base: str, preview: bool, position: int,
         generated: Optional[Dict[str, List[str]]] = None,
         names: Optional[Dict[str, str]] = None,
         page_dir: Optional[str] = None) -> Dict[str, Any]:
    """Template context for one composition card.

    The sub-crate's own root wins; the release root's stub fills gaps; the
    release root itself supplies a DOI and publications when neither has
    them, since constituent crates usually inherit those.
    """
    root: Dict[str, Any] = {}
    for source in (stub or {}, crate.root):
        for key, value in source.items():
            if value not in (None, "", [], {}):
                root[key] = value

    sub_dir_abs = os.path.normpath(os.path.join(crate_dir, rel_dir)) if rel_dir else crate_dir

    size = f.human_size(root.get("contentSize"))
    if not size and os.path.isdir(sub_dir_abs):
        size = f.human_size(_dir_size(sub_dir_abs))

    id_label, doi, doi_href = f.identifier(root.get("identifier"), link_base)
    if not doi and stub:
        id_label, doi, doi_href = f.identifier(main_root.get("identifier"), link_base)
    pubs = f.publications(root.get("associatedPublication"))
    if not pubs and stub:
        pubs = f.publications(main_root.get("associatedPublication"))

    evidence_href = ""
    # a graph this run wrote first, then one already beside the crate
    for candidate in (os.path.join(out_dir, rel_dir, GRAPH_HTML) if rel_dir else "",
                      os.path.join(sub_dir_abs, GRAPH_HTML)):
        if candidate and os.path.exists(candidate):
            evidence_href = _href(candidate, crate_dir, out_dir, page_dir)
            break
    if not evidence_href:
        local_graph = ref_ids(root, "localEvidenceGraph")
        if local_graph:
            target = local_graph[0]
            if target.endswith(".html"):
                # Written relative to the release (CM4AI writes it that way on
                # the stub *and* on the constituent's own root) or to the
                # constituent: take whichever holds the file, else the
                # constituent's directory when its own root carries it.
                candidates = [os.path.join(sub_dir_abs, target), os.path.join(crate_dir, target)]
                found = [c for c in candidates if os.path.exists(c)]
                fallback = candidates[0] if ref_ids(crate.root, "localEvidenceGraph") else candidates[1]
                evidence_href = _href(found[0] if found else fallback, crate_dir, out_dir, page_dir)
            else:
                evidence_href = f.ark_href(target, link_base)

    preview_href = ""
    if preview:
        preview_href = _href(os.path.join(out_dir, rel_dir, PREVIEW_HTML),
                             out_dir, out_dir, page_dir)

    keywords = f.keywords(root)
    if generated is None:
        generated = derived_generated(index)
    det = details(crate, index, owner, generated, names)
    search = " ".join([root.get("name", ""), " ".join(keywords),
                       " ".join(row["label"] for row in det["file_formats"])]).lower()

    return {
        "position": position,
        "anchor": f"subcrate-{position}",
        "name": crate.name,
        "id": crate.root_id,
        "id_href": f.ark_href(crate.root_id, link_base),
        "description": f.first(root, "description"),
        "authors": ", ".join(resolve_authors(root, index)),
        "keywords": keywords,
        "date": f.date_only(f.first(root, "datePublished", "dateCreated", "dateModified")),
        "version": f.first(root, "version"),
        "size": size,
        "publisher": f.text(f.resolved(root.get("publisher"), index)),
        "doi": doi,
        "doi_href": doi_href,
        "identifier_label": id_label or "DOI",
        "contact": f.first(root, "contactEmail"),
        "contact_href": f.link_href(root.get("contactEmail")),
        "copyright": f.first(root, "copyrightNotice"),
        "license": f.first(root, "license"),
        "license_href": f.http_url(root.get("license")),
        "terms_of_use": f.first(root, "conditionsOfAccess"),
        "terms_href": f.http_url(root.get("conditionsOfAccess")),
        "confidentiality": f.first(root, "confidentialityLevel"),
        "funder": f.joined(root.get("funder")),
        "md5": f.first(root, "MD5", "md5"),
        "publications": pubs,
        "access_href": f.http_url(root.get("contentUrl")) or f.http_url(root.get("url"))
                       or f.http_url(root.get("sameAs")),
        "evidence_href": evidence_href,
        "evidence_label": ("Open" if evidence_href.startswith(("http://", "https://"))
                           else os.path.basename(evidence_href)) if evidence_href else "",
        "preview_href": preview_href,
        "statistics": summary_statistics(crate, rel_dir, crate_dir, out_dir, page_dir),
        "metadata_path": "/".join(p for p in (rel_dir, METADATA_FILENAME) if p),
        "details": det,
        "notes": notes(crate, generated),
        "search": search,
    }


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
    generated = derived_generated(index)
    comp = Composition(index=index, owner=owner, names=names, is_release=bool(subcrates))

    if subcrates:
        for position, sub in enumerate(subcrates, start=1):
            context = card(sub.crate, stub=sub.stub, main_root=crate.root,
                           rel_dir=sub.rel_dir, index=index, owner=owner,
                           crate_dir=crate_dir, out_dir=out_dir, link_base=link_base,
                           preview=previews, position=position, generated=generated,
                           names=names, page_dir=page_dir)
            preview_path = os.path.join(out_dir, sub.rel_dir, PREVIEW_HTML) if previews else ""
            comp.items.append(Item(context=context, crate=sub.crate,
                                   rel_dir=sub.rel_dir, preview_path=preview_path))
    else:
        context = card(crate, stub=None, main_root=crate.root, rel_dir="",
                       index=index, owner=owner, crate_dir=crate_dir, out_dir=out_dir,
                       link_base=link_base, preview=previews, position=1,
                       generated=generated, names=names, page_dir=page_dir)
        preview_path = os.path.join(out_dir, PREVIEW_HTML) if previews else ""
        comp.items.append(Item(context=context, crate=crate, rel_dir="",
                               preview_path=preview_path))
    return comp
