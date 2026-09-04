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
from fairscape_artifacts.crate import Crate, METADATA_FILENAME, SubCrate, ref_ids

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
    is_release: bool = False

    @property
    def crates(self) -> List[Crate]:
        return [item.crate for item in self.items]


# -- cross-crate index -------------------------------------------------------

def build_index(crate: Crate, subcrates: List[SubCrate]):
    """`(index, owner)`: every entity across the release by id, and which
    crate (by name) it belongs to.

    A sub-crate's own copy of an entity wins over the release root's stub
    of it, which is why sub-crates are indexed last.
    """
    index: Dict[str, Dict[str, Any]] = {}
    owner: Dict[str, str] = {}
    for node in crate.graph:
        nid = node.get("@id")
        if nid:
            index[nid] = node
            owner[nid] = crate.name
    for sub in subcrates:
        for node in sub.crate.graph:
            nid = node.get("@id")
            if nid:
                index[nid] = node
                owner[nid] = sub.crate.name
    return index, owner


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


def _output_formats(node, index) -> List[str]:
    out = []
    for oid in ref_ids(node, "generated", "prov:generated"):
        target = index.get(oid)
        if target:
            out.extend(f.formats_of(target))
    return sorted(set(out))


def _computation_pattern(node, index, owner, here: str) -> Optional[Dict[str, Any]]:
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
        key = source if source and source != here else None
        by_crate.setdefault(key, set()).update(formats)
    outputs = _output_formats(node, index)
    if not by_crate or not outputs:
        return None
    inputs = [{"crate": crate_name, "formats": sorted(formats)}
              for crate_name, formats in by_crate.items()]
    inputs.sort(key=lambda i: (i["crate"] or "", i["formats"]))
    return {"inputs": inputs, "outputs": outputs}


def _experiment_pattern(node, index) -> Optional[Dict[str, Any]]:
    outputs = _output_formats(node, index)
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


def _input_datasets(crate: Crate, index, owner) -> List[Dict[str, Any]]:
    """What the crate declares (or, failing that, derives) as its inputs,
    tallied by format and, for inputs from another crate, by that crate."""
    here = crate.name
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
            label = f"{source} ({label})"
        labels.append(label)
    return f.tally(labels)


def details(crate: Crate, index, owner) -> Dict[str, Any]:
    """Counts, formats, access, inputs and work patterns for one crate."""
    here = crate.name
    counts: Counter = Counter()
    provenance = 0
    file_formats: List[str] = []
    software_formats: List[str] = []
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
        elif kind == "sample":
            line = _cell_line(node, index)
            if line:
                cell_lines.setdefault(line["id"], line)
                if line["organism"]:
                    organisms.append(line["organism"])
        elif kind == "experiment":
            experiment_types.append(f.first(node, "experimentType") or "Unspecified")
            pattern = _experiment_pattern(node, index)
            if pattern:
                experiment_patterns.append(pattern)
        elif kind == "computation":
            pattern = _computation_pattern(node, index, owner, here)
            if pattern:
                computation_patterns.append(pattern)

    input_datasets = _input_datasets(crate, index, owner)
    if not input_datasets and counts["sample"]:
        input_datasets = [{"label": "Sample", "n": counts["sample"]}]

    return {
        "files": counts["dataset"],
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


def _href(target_abs: str, out_dir: str) -> str:
    return os.path.relpath(target_abs, out_dir).replace(os.sep, "/")


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


def summary_statistics(crate: Crate, rel_dir: str, crate_dir: str, out_dir: str):
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
        return {"name": name, "href": _href(local, out_dir)}
    return None


def card(crate: Crate, *, stub: Optional[Dict[str, Any]], main_root: Dict[str, Any],
         rel_dir: str, index, owner, crate_dir: str, out_dir: str,
         link_base: str, preview: bool, position: int) -> Dict[str, Any]:
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

    doi = f.first(root, "identifier") or (f.first(main_root, "identifier") if stub else "")
    pubs = f.publications(root.get("associatedPublication"))
    if not pubs and stub:
        pubs = f.publications(main_root.get("associatedPublication"))

    evidence_href = ""
    for candidate in (os.path.join(sub_dir_abs, GRAPH_HTML),
                      os.path.join(out_dir, rel_dir, GRAPH_HTML) if rel_dir else ""):
        if candidate and os.path.exists(candidate):
            evidence_href = _href(candidate, out_dir)
            break
    if not evidence_href:
        local_graph = ref_ids(root, "localEvidenceGraph")
        if local_graph:
            target = local_graph[0]
            if target.endswith(".html"):
                evidence_href = _href(os.path.join(crate_dir, target), out_dir)
            else:
                evidence_href = f.ark_href(target, link_base)

    preview_href = ""
    if preview:
        preview_href = "/".join(p for p in (rel_dir, PREVIEW_HTML) if p)

    keywords = f.keywords(root)
    det = details(crate, index, owner)
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
        "publisher": f.first(root, "publisher"),
        "doi": doi,
        "doi_href": f.doi_href(doi),
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
        "statistics": summary_statistics(crate, rel_dir, crate_dir, out_dir),
        "metadata_path": "/".join(p for p in (rel_dir, METADATA_FILENAME) if p),
        "details": det,
        "search": search,
    }


# -- entry point ---------------------------------------------------------------

def build(crate: Crate, *, out_dir: str, link_base: str = "",
          previews: bool = True) -> Composition:
    """Cards for a crate's constituents, or for the crate itself."""
    crate_dir = crate.dir or out_dir
    subcrates = crate.sub_crates()
    index, owner = build_index(crate, subcrates)
    comp = Composition(index=index, owner=owner, is_release=bool(subcrates))

    if subcrates:
        for position, sub in enumerate(subcrates, start=1):
            context = card(sub.crate, stub=sub.stub, main_root=crate.root,
                           rel_dir=sub.rel_dir, index=index, owner=owner,
                           crate_dir=crate_dir, out_dir=out_dir, link_base=link_base,
                           preview=previews, position=position)
            preview_path = os.path.join(out_dir, sub.rel_dir, PREVIEW_HTML) if previews else ""
            comp.items.append(Item(context=context, crate=sub.crate,
                                   rel_dir=sub.rel_dir, preview_path=preview_path))
    else:
        context = card(crate, stub=None, main_root=crate.root, rel_dir="",
                       index=index, owner=owner, crate_dir=crate_dir, out_dir=out_dir,
                       link_base=link_base, preview=previews, position=1)
        preview_path = os.path.join(out_dir, PREVIEW_HTML) if previews else ""
        comp.items.append(Item(context=context, crate=crate, rel_dir="",
                               preview_path=preview_path))
    return comp
