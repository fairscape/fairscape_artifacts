"""Turn a crate into the context the datasheet template renders.

The page follows the FAIRSCAPE datasheet layout: an executive summary
(statistics and the AI-Ready review at a glance), the release overview with
its human-subjects block, the AI Ready Details drawn from the `rai:`
characterization fields, a composition section with one card per
constituent crate, and distribution information. Sub-crates are loaded and
summarised by `fairscape_artifacts.composition`.

The AI-Ready panel is a *review*, not a score: `fairscape_artifacts.grading`
hands back the rubric's per-criterion estimates, with the criteria the
rubric reserves for human judgment shown as awaiting review. No total is
synthesized; the donut is a count of criteria by outcome.

Python computes plain data — rows, tallies, hrefs — and every angle bracket
lives in a `.j2` file. Adding a row to the datasheet means adding a tuple to
one of the tables below, not writing HTML in Python.
"""

from __future__ import annotations

import datetime as _dt
import math
import os
from collections import Counter
from typing import Any, Dict, List, Optional

from fairscape_artifacts import composition as composition_mod
from fairscape_artifacts import fields as f
from fairscape_artifacts import grading
from fairscape_artifacts.composition import Composition, resolve_authors
from fairscape_artifacts.crate import Crate, METADATA_FILENAME

#: Summary tiles, in display order: (label, bucket, evi counter on the root).
TILES = (
    ("Datasets", "dataset", "evi:datasetCount"),
    ("Computations", "computation", "evi:computationCount"),
    ("Software", "software", "evi:softwareCount"),
    ("ML models", "mlmodel", "evi:mlModelCount"),
    ("Samples", "sample", "evi:sampleCount"),
    ("Experiments", "experiment", "evi:experimentCount"),
    ("Instruments", "instrument", "evi:instrumentCount"),
    ("Schemas", "schema", "evi:schemaCount"),
)

FORMATS_SHOWN = 10

#: AI Ready Details rows: (label, source fields in precedence order,
#: additionalProperty name fallback, joiner for list values).
USE_CASE_ROWS = (
    ("Intended Uses", ("rai:dataUseCases", "usageInfo"), None, ", "),
    ("Limitations", ("rai:dataLimitations",), None, ", "),
    ("Prohibited Uses", ("prohibitedUses",), "Prohibited Uses", ", "),
    ("Potential Sources of Bias", ("rai:dataBiases",), None, ", "),
    ("Maintenance Plan", ("rai:dataReleaseMaintenancePlan",), None, ", "),
    ("Data Collection", ("rai:dataCollection",), None, ", "),
    ("Data Collection Type", ("rai:dataCollectionType",), None, "; "),
    ("Missing Data", ("rai:dataCollectionMissingData",), None, ", "),
    ("Raw Data", ("rai:dataCollectionRawData",), None, ", "),
    ("Collection Timeframe", ("rai:dataCollectionTimeframe",), None, " – "),
    ("Sampling Strategy", ("d4d:samplingStrategies",), None, ", "),
    ("Imputation Protocol", ("rai:dataImputationProtocol",), None, ", "),
    ("Manipulation Protocol", ("rai:dataManipulationProtocol",), None, ", "),
    ("Preprocessing Protocol", ("rai:dataPreprocessingProtocol",), None, ", "),
    ("Annotation Protocol", ("rai:dataAnnotationProtocol",), None, ", "),
    ("Annotation Platform", ("rai:dataAnnotationPlatform",), None, ", "),
    ("Annotation Analysis", ("rai:dataAnnotationAnalysis",), None, ", "),
    ("Personal / Sensitive Information", ("rai:personalSensitiveInformation",), None, ", "),
    ("Social Impact", ("rai:dataSocialImpact",), None, ", "),
    ("Annotations Per Item", ("rai:annotationsPerItem",), None, ", "),
    ("Annotator Demographics", ("rai:annotatorDemographics",), None, ", "),
    ("Machine Annotation Tools", ("rai:machineAnnotationTools",), None, ", "),
)

#: Donut / bar segments that are drawn: (tally label, css class). An absent
#: criterion draws nothing — the empty track is the points not earned.
SEGMENTS = (("Substantive", "s2"), ("Partial", "s1"), (grading.AWAITING, "sna"))
CIRCUMFERENCE = round(2 * math.pi * 54, 2)

#: The four headline statistics, always shown so the panel stays a 2x2 grid.
HEADLINE = (("Total size", None), ("Datasets", "dataset"),
            ("Computations", "computation"), ("Software", "software"))

NOT_SPECIFIED = "Not specified"


# -- rows ----------------------------------------------------------------------

def row(label: str, value: Any = "", *, id: str = "", href: str = "", kind: str = "text",
        items: Optional[List[Dict[str, str]]] = None,
        missing: str = "") -> Optional[Dict[str, Any]]:
    """One label/value row, or None when there is nothing to show.

    `kind` is text, mono, link (uses `href`) or list (uses `items`); a row
    with `missing` set renders that placeholder instead of disappearing,
    for fields a reader should notice are absent.
    """
    value = f.text(value) if not isinstance(value, str) else value.strip()
    if kind == "list":
        items = [i for i in (items or []) if i.get("text")]
        if not items:
            return {"id": id, "label": label, "kind": "missing", "value": missing,
                    "href": "", "entries": []} if missing else None
        return {"id": id, "label": label, "kind": "list", "value": "", "href": "",
                "entries": items}
    if not value:
        if missing:
            return {"id": id, "label": label, "kind": "missing", "value": missing,
                    "href": "", "entries": []}
        return None
    return {"id": id, "label": label, "kind": kind, "value": value, "href": href,
            "entries": []}


def _list_items(values: List[str]) -> List[Dict[str, str]]:
    return [{"text": v, "href": f.link_href(v) if v.startswith(("http", "doi:", "10.")) else ""}
            for v in values]


def _prop(root: Dict[str, Any], field: str, *ap_names: str) -> str:
    """A top-level field, falling back to an `additionalProperty` entry."""
    return f.first(root, field) or (f.additional_property(root, *ap_names) if ap_names else "")


# -- sections ----------------------------------------------------------------

def badges(crate: Crate, link_base: str) -> List[Dict[str, str]]:
    root = crate.root
    doi = f.first(root, "identifier")
    out = []
    version = f.first(root, "version")
    if version:
        out.append({"label": f"Version {version}", "href": "", "title": ""})
    if doi:
        out.append({"label": "DOI", "href": f.doi_href(doi), "title": doi})
    license_ = f.first(root, "license")
    if license_:
        out.append({"label": "License", "href": f.http_url(license_), "title": license_})
    size = f.human_size(root.get("contentSize")) or f.human_size(root.get("evi:totalContentSizeBytes"))
    if size:
        out.append({"label": size, "href": "", "title": ""})
    released = f.first(root, "datePublished")
    if released:
        out.append({"label": f"Released {released}", "href": "", "title": ""})
    return out


def summary(crate: Crate, comp: Composition) -> Dict[str, Any]:
    """The executive summary's statistics.

    A release crate that carries `evi:*` counters on its root is trusted;
    otherwise every entity across the crate and its constituents is counted.
    """
    root = crate.root
    description, truncated = f.truncate(f.first(root, "description"))

    counts: Counter = Counter()
    formats: List[str] = []
    size_total = 0.0
    if root.get("evi:totalEntities"):
        for label, bucket, counter in TILES:
            counts[bucket] = int(root.get(counter) or 0)
        formats = f.dedupe(f.normalize_formats(root.get("evi:formats")))
        entities = int(root.get("evi:totalEntities") or 0)
    else:
        format_counter: Counter = Counter()
        entities = 0
        for member in comp.crates:
            for node in composition_mod._entities(member):
                kind = f.bucket(node)
                if kind == "rocrate":
                    continue
                entities += 1
                counts[kind] += 1
                if kind == "dataset":
                    format_counter.update(f.formats_of(node))
                if kind in ("dataset", "mlmodel"):
                    size_total += f.size_bytes(node.get("contentSize")) or 0
        formats = [label for label, _ in format_counter.most_common()]

    size = (f.human_size(root.get("contentSize"))
            or f.human_size(root.get("evi:totalContentSizeBytes"))
            or (f.human_size(size_total) if size_total else ""))

    stats = [{"label": label, "n": (size or "—") if bucket is None else f"{counts[bucket]:,}"}
             for label, bucket in HEADLINE]
    headline_buckets = {bucket for _, bucket in HEADLINE}
    extra = []
    if comp.is_release:
        extra.append({"label": "sub-crates", "n": f"{len(comp.items):,}"})
    for label, bucket, _ in TILES:
        if bucket not in headline_buckets and counts[bucket]:
            extra.append({"label": label.lower(), "n": f"{counts[bucket]:,}"})

    return {
        "description": description,
        "truncated": truncated,
        "stats": stats,
        "extra": extra,
        "entities": entities,
        "formats": formats[:FORMATS_SHOWN],
        "formats_more": max(0, len(formats) - FORMATS_SHOWN),
        "access_href": f.http_url(root.get("contentUrl")) or f.http_url(root.get("url")),
    }


def _points(tally: Dict[str, int]) -> Dict[str, int]:
    """The rubric's own scoring (2 / 1 / 0) summed over estimated criteria.

    Criteria awaiting human review are left out of `possible` rather than
    counted as zero; the caller shows how many were left out.
    """
    estimated = tally["Substantive"] + tally["Partial"] + tally["Absent"]
    return {"points": 2 * tally["Substantive"] + tally["Partial"],
            "possible": 2 * estimated}


def review_panel(review: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The grader summary plus donut and bar geometry for the panel.

    Every criterion is an equal slice. Substantive and partial slices are
    drawn; a slice awaiting human review is drawn hatched so it is not read
    as a miss; an absent criterion draws nothing, so the empty track is
    simply the points the crate does not have.
    """
    if not review:
        return None
    total = review["criteria_total"] or 1
    donut = []
    cursor = 0.0
    for label, cls in SEGMENTS:
        n = review["tally"][label]
        length = round(CIRCUMFERENCE * n / total, 2)
        if n:
            donut.append({"label": label, "cls": cls, "n": n,
                          "length": length, "offset": round(-cursor, 2)})
        cursor += length
    for section in review["sections"]:
        section["segments"] = [
            {"label": label, "cls": cls, "n": section["tally"][label],
             "pct": round(100 * section["tally"][label] / (section["total"] or 1), 1)}
            for label, cls in SEGMENTS if section["tally"][label]
        ]
        section.update(_points(section["tally"]))
    legend = [{"label": label, "cls": cls} for label, cls in SEGMENTS]
    legend.append({"label": "No points", "cls": "none"})
    return {**review, "donut": donut, "circumference": CIRCUMFERENCE,
            "legend": legend, **_points(review["tally"])}


def overview(crate: Crate, index, link_base: str) -> List[Dict[str, Any]]:
    root = crate.root
    doi = f.first(root, "identifier")
    citation = f.publications(root.get("citation"))
    publications = f.publications(root.get("associatedPublication"))
    rows = [
        row("RO-Crate ID", crate.root_id, id="accession", kind="mono",
            href=f.ark_href(crate.root_id, link_base)),
        row("DOI", doi, id="doi", kind="link", href=f.doi_href(doi), missing=NOT_SPECIFIED),
        row("Release Date", f.first(root, "datePublished"), id="release-date"),
        row("Date Created", f.first(root, "dateCreated"), id="date-created"),
        row("Date Modified", f.first(root, "dateModified"), id="date-modified"),
        row("Version", f.first(root, "version"), id="version", kind="mono"),
        row("Size", f.human_size(root.get("contentSize"))
            or f.human_size(root.get("evi:totalContentSizeBytes")), id="content-size", kind="mono"),
        row("Description", f.first(root, "description"), id="description"),
        row("Authors", ", ".join(resolve_authors(root, index)), id="authors"),
        row("Publisher", f.first(root, "publisher"), id="publisher",
            kind="link", href=f.http_url(root.get("publisher"))),
        row("Principal Investigator", f.first(root, "principalInvestigator"),
            id="principal-investigator"),
        row("Contact Email", f.first(root, "contactEmail"), id="contact-email",
            kind="link", href=f.link_href(root.get("contactEmail"))),
        row("Data Governance Committee",
            _prop(root, "dataGovernanceCommittee", "Data Governance Committee"),
            id="data-governance"),
        row("Ethical Review", f.first(root, "ethicalReview"), id="ethical-review"),
        row("Copyright", f.first(root, "copyrightNotice"), id="copyright"),
        row("License", f.first(root, "license"), id="license", kind="link",
            href=f.http_url(root.get("license")), missing=NOT_SPECIFIED),
        row("Terms of Use", f.first(root, "conditionsOfAccess"), id="terms-of-use",
            kind="link", href=f.http_url(root.get("conditionsOfAccess"))),
        row("HL7 Confidentiality Level", f.first(root, "confidentialityLevel"),
            id="confidentiality-level"),
        row("Keywords", ", ".join(f.keywords(root)), id="keywords"),
        row("Cite As", citation[0] if len(citation) == 1 else "", id="citation",
            kind="text") if len(citation) <= 1 else
        row("Cite As", id="citation", kind="list", items=_list_items(citation)),
        row("Funding", f.joined(root.get("funder")), id="funding"),
        row("Completeness", _prop(root, "completeness", "Completeness"), id="completeness"),
        row("Related Publications", id="related-publications", kind="list",
            items=_list_items(publications)),
        row("Based On", id="based-on", kind="list",
            items=_list_items(f.as_list(root.get("isBasedOn")))),
        row("Conforms To", id="conforms-to", kind="list",
            items=_list_items(f.as_list(root.get("conformsTo")))),
    ]
    return [r for r in rows if r]


def regulatory(crate: Crate) -> Dict[str, Any]:
    """The human-subjects block. Absent fields say so rather than assuming a
    reassuring default."""
    root = crate.root
    irb = root.get("irb")
    if not isinstance(irb, dict):
        irb = f.first(root, "irb") or f.additional_property(root, "IRB")
    fields = {
        "human_subject_research": _prop(root, "humanSubjectResearch", "Human Subject Research")
                                  or f.first(root, "humanSubjects"),
        "deidentified": f.yes_no(root.get("deidentified"))
                        or f.additional_property(root, "De-identified Samples"),
        "fda_regulated": f.yes_no(root.get("fdaRegulated"))
                         or f.additional_property(root, "FDA Regulated"),
        "irb_protocol_id": _prop(root, "irbProtocolId", "IRB Protocol ID"),
        "irb": irb,
        "exemptions": _prop(root, "humanSubjectExemption", "Human Subjects Exemptions"),
        "informed_consent": f.first(root, "d4d:informedConsent"),
        "at_risk_populations": f.first(root, "d4d:atRiskPopulations"),
    }
    fields["any"] = any(bool(v) for v in fields.values())
    fields["not_specified"] = NOT_SPECIFIED
    return fields


def use_cases(crate: Crate) -> List[Dict[str, Any]]:
    root = crate.root
    rows = []
    for label, sources, ap_name, joiner in USE_CASE_ROWS:
        value = ""
        for source in sources:
            value = f.joined(root.get(source), joiner)
            if value:
                break
        if not value and ap_name:
            value = f.additional_property(root, ap_name)
        item = row(label, value)
        if item:
            rows.append(item)
    return rows


def distribution(crate: Crate) -> List[Dict[str, Any]]:
    root = crate.root
    doi = f.first(root, "identifier")
    rows = [
        row("Publisher", f.first(root, "publisher"), kind="link",
            href=f.http_url(root.get("publisher"))),
        row("License", f.first(root, "license"), kind="link",
            href=f.http_url(root.get("license")), missing=NOT_SPECIFIED),
        row("DOI", doi, kind="link", href=f.doi_href(doi)),
        row("Release Date", f.first(root, "datePublished")),
        row("Version", f.first(root, "version"), kind="mono"),
        row("Access", f.http_url(root.get("contentUrl")) or f.http_url(root.get("url")),
            kind="link", href=f.http_url(root.get("contentUrl")) or f.http_url(root.get("url"))),
    ]
    return [r for r in rows if r]


def navigation(comp: Composition, has_use_cases: bool, has_review: bool) -> List[Dict[str, Any]]:
    nav = [
        {"href": "#datasheet-summary", "label": "Datasheet Summary", "children": []},
        {"href": "#dataset-details", "label": "Release Overview", "children": []},
    ]
    if has_use_cases:
        nav.append({"href": "#ai-readiness", "label": "AI Ready Details", "children": []})
    if has_review:
        nav.append({"href": "#ai-ready-review", "label": "AI-Ready Review", "children": []})
    children = [{"href": "#composition", "label": "All datasets"}] if comp.is_release else []
    children += [{"href": "#" + item.context["anchor"], "label": item.context["name"]}
                 for item in comp.items]
    nav.append({"href": "#composition", "label": "Composition", "children": children})
    nav.append({"href": "#distribution", "label": "Distribution", "children": []})
    return nav


# -- entry point ---------------------------------------------------------------

def build_context(crate: Crate, *, composition: Optional[Composition] = None,
                  review: Optional[Dict[str, Any]] = None,
                  generated_at: Optional[str] = None,
                  links: Optional[Dict[str, str]] = None,
                  link_base: str = "") -> Dict[str, Any]:
    """Everything the datasheet template needs, and nothing else."""
    if composition is None:
        composition = composition_mod.build(
            crate, out_dir=crate.dir or os.getcwd(), link_base=link_base, previews=False)
    root = crate.root
    review_summary = review_panel(grading.summarize(review))
    use_case_rows = use_cases(crate)
    links = {"evidence_graph": "", "review_html": "", "review_json": "", "graph_json": "",
             **(links or {})}

    return {
        "kicker": "Datasheet",
        "title": crate.name or "RO-Crate",
        "ark": crate.root_id,
        "ark_href": f.ark_href(crate.root_id, link_base),
        "description": "",
        "chips": f.keywords(root)[:12],
        "badges": badges(crate, link_base),
        "body_class": "has-nav",
        "nav": navigation(composition, bool(use_case_rows), bool(review_summary)),
        "summary": summary(crate, composition),
        "review": review_summary,
        "overview": overview(crate, composition.index, link_base),
        "regulatory": regulatory(crate),
        "use_cases": use_case_rows,
        "composition": {
            "cards": [item.context for item in composition.items],
            "count": len(composition.items),
            "is_release": composition.is_release,
            "show_index": len(composition.items) > 1,
        },
        "distribution": distribution(crate),
        "links": links,
        "entity_total": len([n for n in crate.graph if n.get("@id") != METADATA_FILENAME]),
        "source": os.path.basename(crate.path) if crate.path else "",
        "generated_at": generated_at or _dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
