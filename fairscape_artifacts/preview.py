"""The per-crate preview page: every entity, tabulated by kind.

`ro-crate-preview.html` is the RO-Crate convention for a human-readable
listing of a crate, and here it is the "View full dataset details" target
behind each composition card. Datasets, software, computations (with their
inputs, outputs, software and command), samples, experiments, instruments,
schemas (with their properties) and everything else each get a tab.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from fairscape_artifacts import fields as f
from fairscape_artifacts.composition import summary_statistics
from fairscape_artifacts.crate import Crate, generated_ids, generated_index, ref_ids
from fairscape_artifacts.models import (ComputationDetails, CrateMetadata, Entry, Link,
                                        PreviewPage, RefRow, Row, SchemaProperty, Tab)

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


def _ref_row(ref_id: str, index, owner, here: str, *, with_format: bool = True,
             names: Optional[Dict[str, str]] = None) -> RefRow:
    node = index.get(ref_id)
    source = owner.get(ref_id)
    return RefRow(
        id=ref_id,
        name=(f.first(node, "name") if node else "") or ref_id,
        format=f.format_label(node) if node and with_format else "",
        crate=(names or {}).get(source, source) if source and source != here else "",
    )


def computation_details(node, index, owner, here: str,
                        generated: Optional[Dict[str, List[str]]] = None,
                        names: Optional[Dict[str, str]] = None) -> ComputationDetails:
    if generated is None:
        generated = generated_index(index)
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
    return ComputationDetails(
        inputs=[_ref_row(i, index, owner, here, names=names) for i in used],
        outputs=[_ref_row(o, index, owner, here, names=names)
                 for o in generated_ids(node, generated)],
        software=[_ref_row(s, index, owner, here, with_format=False, names=names)
                  for s in software],
        models=[_ref_row(m, index, owner, here, names=names) for m in models],
        command=f.text(command),
        parameters=f.as_list(node.get("parameter")),
    )


def schema_properties(node) -> List[SchemaProperty]:
    props = node.get("properties")
    if not isinstance(props, dict):
        return []
    rows = [(spec.get("index", ""), name, spec) for name, spec in props.items()
            if isinstance(spec, dict)]
    rows.sort(key=lambda r: (r[0] == "", r[0] if isinstance(r[0], int) else 0))
    return [SchemaProperty(name=name, type=f.text(spec.get("type")) or "—",
                           description=f.text(spec.get("description")))
            for _, name, spec in rows]


def _entry(node, kind: str, index, owner, here: str, link_base: str,
           generated: Dict[str, List[str]], names: Dict[str, str]) -> Entry:
    description = f.first(node, "description")
    short, cut = f.truncate(description, DESCRIPTION_LIMIT)
    url = node.get("contentUrl")
    if isinstance(url, list):
        url = url[0] if url else None
    status = f.access(node)
    types = node.get("@type") if isinstance(node.get("@type"), list) else [node.get("@type")]
    return Entry(
        id=node.get("@id", ""),
        id_href=f.ark_href(node.get("@id", ""), link_base),
        name=f.first(node, "name") or node.get("@id", ""),
        description=description,
        description_short=short + ("…" if cut else ""),
        date=f.date_only(f.first(node, "datePublished", "dateCreated", "datePerformed",
                                 "dateModified")),
        format=f.format_label(node),
        size=f.human_size(node.get("contentSize")),
        access=status,
        access_href=f.http_url(url) if status == "Available" else "",
        experiment_type=f.first(node, "experimentType"),
        manufacturer=f.first(node, "manufacturer"),
        types=", ".join(t for t in (f.text(t) for t in types) if t),
        computation=(computation_details(node, index, owner, here, generated, names)
                     if kind == "computation" else None),
        properties=schema_properties(node) if kind == "schema" else [],
    )


def build(crate: Crate, index, owner, *, link_base: str = "",
          datasheet_href: str = "", evidence_href: str = "",
          source: str = "", generated_at: str = "",
          names: Optional[Dict[str, str]] = None) -> PreviewPage:
    """The preview page for `crate`.

    `owner` maps ids to crate root ids; `names` maps those to the names
    shown (a crate missing from it is shown by its id).
    """
    here = crate.root_id
    names = names or {}
    buckets: Dict[str, List[Entry]] = {key: [] for key, _, _ in TABS}
    generated = generated_index(index)
    for node in crate.entities():
        kind = f.bucket(node)
        if kind == "rocrate":
            kind = "other"
        buckets[kind].append(_entry(node, kind, index, owner, here, link_base, generated, names))
    tabs = [Tab(key=key, label=label, entries=buckets[kind])
            for kind, key, label in TABS if buckets[kind]]

    meta = CrateMetadata.read(crate.root, index, link_base)
    stats = summary_statistics(crate, crate.dir or "", crate.dir or "")
    rows = [
        Row.of("RO-Crate ID", meta.id, href=meta.id_href, kind="mono"),
        Row.of(meta.identifier_label, meta.identifier, href=meta.identifier_href,
               kind="mono" if meta.identifier_label == "ARK" else "text"),
        Row.of("Version", meta.version, kind="mono"),
        Row.of("Release date", meta.date_published),
        Row.of("Date created", meta.date_created),
        Row.of("Date modified", meta.date_modified),
        Row.of("Size", meta.size, kind="mono"),
        Row.of("Description", meta.description),
        Row.of("Authors", ", ".join(meta.authors)),
        Row.of("Publisher", meta.publisher, href=meta.publisher_href),
        Row.of("Principal investigator", meta.principal_investigator),
        Row.of("Contact", meta.contact, href=meta.contact_href),
        Row.of("License", meta.license, href=meta.license_href),
        Row.of("Confidentiality level", meta.confidentiality),
        Row.of("Keywords", ", ".join(meta.keywords)),
        Row.of("Quality control report", stats.name, href=stats.href) if stats else None,
        Row.of("Cite as", kind="list", items=[Link(text=c) for c in meta.citation]),
        Row.of("Related publications", kind="list",
               items=[Link(text=p) for p in meta.publications]),
    ]

    return PreviewPage(
        kicker="RO-Crate preview",
        title=meta.name,
        ark=meta.id,
        ark_href=meta.id_href,
        chips=meta.keywords[:12],
        rows=[r for r in rows if r],
        tabs=tabs,
        datasheet_href=datasheet_href,
        evidence_href=evidence_href,
        source=source,
        generated_at=generated_at,
    )
