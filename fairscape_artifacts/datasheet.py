"""Build the `DatasheetPage` for a crate.

The page follows the FAIRSCAPE datasheet layout: an executive summary
(statistics and the AI-Ready review at a glance), the release overview with
its human-subjects block, the AI Ready Details drawn from the `rai:`
characterization fields, a composition section with one card per
constituent crate, and distribution information. Sub-crates are loaded and
summarised by `fairscape_artifacts.composition`.

The AI-Ready panel is a *review*, not a score: `fairscape_artifacts.grading`
hands back the rubric's per-criterion estimates, with the criteria the
rubric reserves for human judgment shown as awaiting review. No total is
synthesized beyond the rubric's own 2/1/0 points; the donut and bars draw
those points, with criteria awaiting human review hatched.

Adding a row to the datasheet means adding a `Row.of(...)` to one of the
tables below, not writing HTML in Python.
"""

from __future__ import annotations

import datetime as _dt
import math
import os
from collections import Counter
from typing import Any, Dict, List, Optional, Union

from fairscape_artifacts import composition as composition_mod
from fairscape_artifacts import fields as f
from fairscape_artifacts import grading
from fairscape_artifacts.composition import Composition
from fairscape_artifacts.crate import Crate, ref_ids
from fairscape_artifacts.models import (NOT_SPECIFIED, Badge, CompositionSection,
                                        CrateMetadata, DatasheetPage, Link, Links, NavEntry,
                                        Regulatory, ReviewPanel, ReviewSection, Row, Segment,
                                        Stat, Summary, Swatch, links)

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
#: Ring units each outcome fills, out of two per criterion (the rubric's 2/1/0).
UNITS = {"Substantive": 2, "Partial": 1, grading.AWAITING: 2}
CIRCUMFERENCE = round(2 * math.pi * 54, 2)

#: The four headline statistics, always shown so the panel stays a 2x2 grid.
HEADLINE = (("Total size", None), ("Datasets", "dataset"),
            ("Computations", "computation"), ("Software", "software"))


# -- sections ----------------------------------------------------------------

def badges(meta: CrateMetadata) -> List[Badge]:
    out = []
    if meta.version:
        out.append(Badge(label=f"Version {meta.version}"))
    if meta.identifier:
        out.append(Badge(label=meta.identifier_label, href=meta.identifier_href,
                         title=meta.identifier))
    if meta.license:
        out.append(Badge(label="License", href=meta.license_href, title=meta.license))
    if meta.size:
        out.append(Badge(label=meta.size))
    if meta.date_published:
        out.append(Badge(label=f"Released {meta.date_published}"))
    return out


def summary(crate: Crate, comp: Composition) -> Summary:
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
        nodes = [n for member in comp.crates for n in member.entities()]
        if comp.is_release:
            # Entities described on the release root itself, not in any
            # constituent (a top-level README, say), count too.
            described = {n.get("@id") for n in nodes}
            nodes += [n for n in crate.entities() if n.get("@id") not in described]
        for node in nodes:
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

    stats = [Stat(label=label, n=(size or "—") if bucket is None else f"{counts[bucket]:,}")
             for label, bucket in HEADLINE]
    headline_buckets = {bucket for _, bucket in HEADLINE}
    extra = []
    if comp.is_release:
        extra.append(Stat(label="sub-crates", n=f"{len(comp.items):,}"))
    linked = crate.linked_crates()
    if linked:
        extra.append(Stat(label="linked crates", n=f"{len(linked):,}"))
    for label, bucket, _ in TILES:
        if bucket not in headline_buckets and counts[bucket]:
            extra.append(Stat(label=label.lower(), n=f"{counts[bucket]:,}"))

    return Summary(
        description=description,
        truncated=truncated,
        stats=stats,
        extra=extra,
        entities=entities,
        formats=formats[:FORMATS_SHOWN],
        formats_more=max(0, len(formats) - FORMATS_SHOWN),
        access_href=f.http_url(root.get("contentUrl")) or f.http_url(root.get("url")),
    )


def _points(tally: Dict[str, int]) -> Dict[str, int]:
    """The rubric's own scoring (2 / 1 / 0) summed over estimated criteria.

    Criteria awaiting human review are left out of `possible` rather than
    counted as zero; the caller shows how many were left out.
    """
    estimated = tally["Substantive"] + tally["Partial"] + tally["Absent"]
    return {"points": 2 * tally["Substantive"] + tally["Partial"],
            "possible": 2 * estimated}


def review_panel(review: Optional[Dict[str, Any]]) -> Optional[ReviewPanel]:
    """The grader summary plus donut and bar geometry for the panel.

    The geometry draws points, so it agrees with the points/possible label:
    every criterion is worth two units of the ring, a substantive criterion
    fills both, a partial one fills one, and an absent one fills none. A
    criterion awaiting human review is drawn hatched over its two units so it
    is not read as a miss; with those set aside, the filled share of the rest
    is exactly points / possible.
    """
    if not review:
        return None
    units = 2 * (review["criteria_total"] or 1)
    donut = []
    cursor = 0.0
    for label, cls in SEGMENTS:
        n = review["tally"][label]
        length = round(CIRCUMFERENCE * UNITS[label] * n / units, 2)
        if n:
            donut.append(Segment(label=label, cls=cls, n=n, length=length,
                                 offset=round(-cursor, 2)))
        cursor += length
    sections = []
    for section in review["sections"]:
        section_units = 2 * (section["total"] or 1)
        segments = [
            Segment(label=label, cls=cls, n=section["tally"][label],
                    pct=round(100 * UNITS[label] * section["tally"][label] / section_units, 1))
            for label, cls in SEGMENTS if section["tally"][label]
        ]
        sections.append(ReviewSection(**section, segments=segments, **_points(section["tally"])))
    legend = [Swatch(label=label, cls=cls) for label, cls in SEGMENTS]
    legend.append(Swatch(label="No points", cls="none"))
    return ReviewPanel(**{k: v for k, v in review.items() if k != "sections"},
                       sections=sections, donut=donut, circumference=CIRCUMFERENCE,
                       legend=legend, **_points(review["tally"]))


def linked_crate_items(crate: Crate) -> List[Link]:
    """One entry per upstream crate this crate points at: its name, how many
    of this crate's entities it owns, and a link to its datasheet when one
    sits beside its metadata file."""
    items = []
    for sub in crate.linked_crates():
        # the stubs: this crate's nodes that say they are part of that crate
        # (not merely nodes both crates happen to describe, like the engine)
        shared = [n for n in crate.graph
                  if sub.crate.root_id in ref_ids(n, "isPartOf")
                  and n.get("@id") != sub.crate.root_id]
        count = len(shared)
        noun = "entity" if count == 1 else "entities"
        text = f"{sub.crate.name} ({sub.crate.root_id}) — {count} {noun} from this crate"
        href = ""
        if sub.crate.dir and os.path.exists(os.path.join(sub.crate.dir, "ro-crate-datasheet.html")):
            href = os.path.join(sub.rel_dir, "ro-crate-datasheet.html").replace(os.sep, "/") \
                if not os.path.isabs(sub.rel_dir) else \
                os.path.join(sub.crate.dir, "ro-crate-datasheet.html")
        items.append(Link(text=text, href=href))
    return items


def overview(crate: Crate, meta: CrateMetadata) -> List[Row]:
    citation = meta.citation
    rows = [
        Row.of("RO-Crate ID", meta.id, id="accession", kind="mono", href=meta.id_href),
        Row.of(meta.identifier_label or "DOI", meta.identifier, id="doi", kind="link",
               href=meta.identifier_href, missing=NOT_SPECIFIED),
        Row.of("Release Date", meta.date_published, id="release-date"),
        Row.of("Date Created", meta.date_created, id="date-created"),
        Row.of("Date Modified", meta.date_modified, id="date-modified"),
        Row.of("Version", meta.version, id="version", kind="mono"),
        Row.of("Size", meta.size, id="content-size", kind="mono"),
        Row.of("Description", meta.description, id="description"),
        Row.of("Authors", ", ".join(meta.authors), id="authors"),
        Row.of("Publisher", meta.publisher, id="publisher", kind="link", href=meta.publisher_href),
        Row.of("Principal Investigator", meta.principal_investigator,
               id="principal-investigator"),
        Row.of("Contact Email", meta.contact, id="contact-email", kind="link",
               href=meta.contact_href),
        Row.of("Data Governance Committee", meta.data_governance, id="data-governance"),
        Row.of("Ethical Review", meta.ethical_review, id="ethical-review"),
        Row.of("Copyright", meta.copyright, id="copyright"),
        Row.of("License", meta.license, id="license", kind="link", href=meta.license_href,
               missing=NOT_SPECIFIED),
        Row.of("Terms of Use", meta.terms_of_use, id="terms-of-use", kind="link",
               href=meta.terms_href),
        Row.of("HL7 Confidentiality Level", meta.confidentiality, id="confidentiality-level"),
        Row.of("Keywords", ", ".join(meta.keywords), id="keywords"),
        Row.of("Cite As", citation[0] if len(citation) == 1 else "", id="citation")
        if len(citation) <= 1 else
        Row.of("Cite As", id="citation", kind="list", items=links(citation)),
        Row.of("Funding", meta.funder, id="funding"),
        Row.of("Completeness", meta.completeness, id="completeness"),
        Row.of("Related Publications", id="related-publications", kind="list",
               items=links(meta.publications)),
        Row.of("Based On", id="based-on", kind="list", items=links(meta.based_on)),
        Row.of("Linked Crates", id="linked-crates", kind="list",
               items=linked_crate_items(crate)),
        Row.of("Conforms To", id="conforms-to", kind="list", items=links(meta.conforms_to)),
    ]
    return [r for r in rows if r]


def _prop(root: Dict[str, Any], field: str, *ap_names: str) -> str:
    """A top-level field, falling back to an `additionalProperty` entry."""
    return f.first(root, field) or f.additional_property(root, *ap_names)


def regulatory(crate: Crate) -> Regulatory:
    """The human-subjects block. Absent fields say so rather than assuming a
    reassuring default."""
    root = crate.root
    irb = f.resolved(root.get("irb"), crate.index)
    if not isinstance(irb, dict):
        irb = f.first(root, "irb") or f.additional_property(root, "IRB")
    return Regulatory(
        human_subject_research=(_prop(root, "humanSubjectResearch", "Human Subject Research")
                                or f.first(root, "humanSubjects")),
        deidentified=(f.yes_no(root.get("deidentified"))
                      or f.additional_property(root, "De-identified Samples")),
        fda_regulated=(f.yes_no(root.get("fdaRegulated"))
                       or f.additional_property(root, "FDA Regulated")),
        irb_protocol_id=_prop(root, "irbProtocolId", "IRB Protocol ID"),
        irb=irb,
        exemptions=_prop(root, "humanSubjectExemption", "Human Subjects Exemptions"),
        informed_consent=f.first(root, "d4d:informedConsent"),
        at_risk_populations=f.first(root, "d4d:atRiskPopulations"),
    )


def use_cases(crate: Crate) -> List[Row]:
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
        item = Row.of(label, value)
        if item:
            rows.append(item)
    return rows


def distribution(meta: CrateMetadata) -> List[Row]:
    rows = [
        Row.of("Publisher", meta.publisher, kind="link", href=meta.publisher_href),
        Row.of("License", meta.license, kind="link", href=meta.license_href,
               missing=NOT_SPECIFIED),
        Row.of(meta.identifier_label, meta.identifier, kind="link", href=meta.identifier_href),
        Row.of("Release Date", meta.date_published),
        Row.of("Version", meta.version, kind="mono"),
        Row.of("Access", meta.access_href, kind="link", href=meta.access_href),
    ]
    return [r for r in rows if r]


def navigation(comp: Composition, has_use_cases: bool, has_review: bool) -> List[NavEntry]:
    nav = [
        NavEntry(href="#datasheet-summary", label="Datasheet Summary"),
        NavEntry(href="#dataset-details", label="Release Overview"),
    ]
    if has_use_cases:
        nav.append(NavEntry(href="#ai-readiness", label="AI Ready Details"))
    if has_review:
        nav.append(NavEntry(href="#ai-ready-review", label="AI-Ready Review"))
    children = [NavEntry(href="#composition", label="All datasets")] if comp.is_release else []
    children += [NavEntry(href="#" + card.anchor, label=card.meta.name) for card in comp.cards]
    nav.append(NavEntry(href="#composition", label="Composition", children=children))
    nav.append(NavEntry(href="#distribution", label="Distribution"))
    return nav


# -- entry point ---------------------------------------------------------------

def build(crate: Crate, *, composition: Optional[Composition] = None,
          review: Optional[Dict[str, Any]] = None,
          generated_at: Optional[str] = None,
          links: Union[Links, Dict[str, str], None] = None,
          link_base: str = "") -> DatasheetPage:
    """The datasheet page for `crate`.

    `review` is the grader's presentation document, if one was produced;
    `links` names the companion pages beside the datasheet.
    """
    if composition is None:
        composition = composition_mod.build(
            crate, out_dir=crate.dir or os.getcwd(), link_base=link_base, previews=False)
    meta = CrateMetadata.read(crate.root, composition.index, link_base)
    review_summary = review_panel(grading.summarize(review))
    use_case_rows = use_cases(crate)

    return DatasheetPage(
        kicker="Datasheet",
        title=meta.name or "RO-Crate",
        ark=meta.id,
        ark_href=meta.id_href,
        chips=meta.keywords[:12],
        badges=badges(meta),
        body_class="has-nav",
        nav=navigation(composition, bool(use_case_rows), bool(review_summary)),
        summary=summary(crate, composition),
        review=review_summary,
        overview=overview(crate, meta),
        regulatory=regulatory(crate),
        use_cases=use_case_rows,
        composition=CompositionSection(cards=composition.cards,
                                       is_release=composition.is_release),
        distribution=distribution(meta),
        links=links if isinstance(links, Links) else Links(**(links or {})),
        source=os.path.basename(crate.path) if crate.path else "",
        generated_at=generated_at or _dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
    )
