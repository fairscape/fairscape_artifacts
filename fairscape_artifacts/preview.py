"""The per-crate preview page: every entity, tabulated by kind.

`ro-crate-preview.html` is the RO-Crate convention for a human-readable
listing of a crate, and here it is the "View full dataset details" target
behind each composition card. Datasets, software, computations (with their
inputs, outputs, software and command), samples, experiments, instruments,
schemas (with their properties) and everything else each get a tab.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fairscape_artifacts import fields as f
from fairscape_artifacts.composition import _entities, resolve_authors, summary_statistics
from fairscape_artifacts.crate import Crate, ref_ids

DESCRIPTION_LIMIT = 100

TABS = (
    ("dataset", "datasets", "Datasets"),
    ("mlmodel", "models", "ML models"),
    ("software", "software", "Software"),
    ("computation", "computations", "Computations"),
    ("sample", "samples", "Samples"),
    ("experiment", "experiments", "Experiments"),
    ("instrument", "instruments", "Instruments"),
    ("schema", "schemas", "Schemas"),
    ("other", "other", "Other"),
)


def _ref_row(ref_id: str, index, owner, here: str, *, with_format: bool = True):
    node = index.get(ref_id)
    row = {"id": ref_id, "name": (f.first(node, "name") if node else "") or ref_id}
    if with_format:
        row["format"] = f.format_label(node) if node else ""
    source = owner.get(ref_id)
    row["crate"] = source if source and source != here else ""
    return row


def computation_details(node, index, owner, here: str) -> Dict[str, Any]:
    used = ref_ids(node, "usedDataset")
    software = ref_ids(node, "usedSoftware")
    models = ref_ids(node, "usedMLModel")
    if not used and not software and not models:
        for pid in ref_ids(node, "prov:used"):
            target = index.get(pid)
            (software if target and f.bucket(target) == "software" else used).append(pid)
    command = node.get("command")
    if isinstance(command, list):
        command = " ".join(str(part) for part in command)
    return {
        "inputs": [_ref_row(i, index, owner, here) for i in used],
        "outputs": [_ref_row(o, index, owner, here)
                    for o in ref_ids(node, "generated", "prov:generated")],
        "software": [_ref_row(s, index, owner, here, with_format=False) for s in software],
        "models": [_ref_row(m, index, owner, here) for m in models],
        "command": f.text(command),
        "parameters": f.as_list(node.get("parameter")),
    }


def schema_properties(node) -> List[Dict[str, Any]]:
    props = node.get("properties")
    if not isinstance(props, dict):
        return []
    rows = []
    for name, spec in props.items():
        if not isinstance(spec, dict):
            continue
        rows.append({
            "name": name,
            "type": f.text(spec.get("type")) or "—",
            "description": f.text(spec.get("description")),
            "index": spec.get("index", ""),
        })
    rows.sort(key=lambda r: (r["index"] == "", r["index"] if isinstance(r["index"], int) else 0))
    return rows


def _item(node, kind: str, index, owner, here: str, link_base: str) -> Dict[str, Any]:
    description = f.first(node, "description")
    short, cut = f.truncate(description, DESCRIPTION_LIMIT)
    url = node.get("contentUrl")
    if isinstance(url, list):
        url = url[0] if url else None
    status = f.access(node)
    item = {
        "id": node.get("@id", ""),
        "id_href": f.ark_href(node.get("@id", ""), link_base),
        "name": f.first(node, "name") or node.get("@id", ""),
        "description": description,
        "description_short": short + ("…" if cut else ""),
        "date": f.date_only(f.first(node, "datePublished", "dateCreated", "datePerformed", "dateModified")),
        "identifier": f.first(node, "identifier"),
        "format": f.format_label(node),
        "size": f.human_size(node.get("contentSize")),
        "access": status,
        "access_href": f.http_url(url) if status == "Available" else "",
        "experiment_type": f.first(node, "experimentType"),
        "manufacturer": f.first(node, "manufacturer"),
        "version": f.first(node, "version"),
        "types": ", ".join(t for t in (f.text(t) for t in
                                       (node.get("@type") if isinstance(node.get("@type"), list)
                                        else [node.get("@type")])) if t),
    }
    if kind == "computation":
        item["computation"] = computation_details(node, index, owner, here)
    if kind == "schema":
        item["properties"] = schema_properties(node)
    return item


def build_context(crate: Crate, index, owner, *, link_base: str = "",
                  datasheet_href: str = "", evidence_href: str = "",
                  source: str = "", generated_at: str = "") -> Dict[str, Any]:
    here = crate.name
    root = crate.root
    buckets: Dict[str, List[Dict[str, Any]]] = {key: [] for key, _, _ in TABS}
    for node in _entities(crate):
        kind = f.bucket(node)
        if kind == "rocrate":
            kind = "other"
        buckets[kind].append(_item(node, kind, index, owner, here, link_base))

    tabs = [{"key": key, "label": label, "entries": buckets[kind], "n": len(buckets[kind])}
            for kind, key, label in TABS if buckets[kind]]

    doi = f.first(root, "identifier")
    rows = [
        {"label": "RO-Crate ID", "value": crate.root_id, "href": f.ark_href(crate.root_id, link_base), "mono": True},
        {"label": "DOI", "value": doi, "href": f.doi_href(doi), "mono": False},
        {"label": "Version", "value": f.first(root, "version"), "href": "", "mono": True},
        {"label": "Release date", "value": f.first(root, "datePublished"), "href": "", "mono": False},
        {"label": "Date created", "value": f.first(root, "dateCreated"), "href": "", "mono": False},
        {"label": "Date modified", "value": f.first(root, "dateModified"), "href": "", "mono": False},
        {"label": "Size", "value": f.human_size(root.get("contentSize")), "href": "", "mono": True},
        {"label": "Description", "value": f.first(root, "description"), "href": "", "mono": False},
        {"label": "Authors", "value": ", ".join(resolve_authors(root, index)), "href": "", "mono": False},
        {"label": "Publisher", "value": f.first(root, "publisher"), "href": f.http_url(root.get("publisher")), "mono": False},
        {"label": "Principal investigator", "value": f.first(root, "principalInvestigator"), "href": "", "mono": False},
        {"label": "Contact", "value": f.first(root, "contactEmail"), "href": f.link_href(root.get("contactEmail")), "mono": False},
        {"label": "License", "value": f.first(root, "license"), "href": f.http_url(root.get("license")), "mono": False},
        {"label": "Confidentiality level", "value": f.first(root, "confidentialityLevel"), "href": "", "mono": False},
        {"label": "Keywords", "value": ", ".join(f.keywords(root)), "href": "", "mono": False},
    ]
    stats = summary_statistics(crate, "", crate.dir or "", crate.dir or "")
    if stats:
        rows.append({"label": "Quality control report", "value": stats["name"], "href": stats["href"], "mono": False})
    rows = [r for r in rows if r["value"]]

    return {
        "kicker": "RO-Crate preview",
        "title": crate.name,
        "ark": crate.root_id,
        "ark_href": f.ark_href(crate.root_id, link_base),
        "description": "",
        "chips": f.keywords(root)[:12],
        "badges": [],
        "body_class": "",
        "rows": rows,
        "citation": f.publications(root.get("citation")),
        "publications": f.publications(root.get("associatedPublication")),
        "tabs": tabs,
        "entity_total": sum(t["n"] for t in tabs),
        "datasheet_href": datasheet_href,
        "evidence_href": evidence_href,
        "source": source,
        "generated_at": generated_at,
    }
