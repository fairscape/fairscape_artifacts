"""What the datasheet and preview templates render.

Every page is a pydantic model: `composition.build` makes the cards,
`datasheet.build` and `preview.build` make the pages, and `render` hands a
page's fields to its template. The models are the contract between Python
and the `.j2` files. A field a template reads that no builder sets is a
construction error here, not a blank in the page, and every value is plain
text or a number: the templates hold every angle bracket, so crate-authored
text is always autoescaped.

`CrateMetadata` is the shared part. A crate root's descriptive fields are
read once, here, and the hero badges, the overview and distribution tables,
each composition card and every preview page draw on the same object
instead of each reading the root its own way.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, computed_field

from fairscape_artifacts import fields as f

NOT_SPECIFIED = "Not specified"


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


# -- building blocks ---------------------------------------------------------

class Link(Model):
    text: str
    href: str = ""


class Row(Model):
    """One label/value row of a key-value block.

    `kind` is text, mono, link (uses `href`), list (uses `entries`) or
    missing: a placeholder shown for a field a reader should notice is absent.
    """
    label: str
    value: str = ""
    id: str = ""
    href: str = ""
    kind: str = "text"
    entries: List[Link] = []

    @classmethod
    def of(cls, label: str, value: Any = "", *, id: str = "", href: str = "",
           kind: str = "text", items: Optional[List[Link]] = None,
           missing: str = "") -> Optional["Row"]:
        """A row, or None when there is nothing to show (and no `missing`
        placeholder to show instead)."""
        value = f.text(value) if not isinstance(value, str) else value.strip()
        if kind == "list":
            entries = [i for i in (items or []) if i.text]
            if entries:
                return cls(label=label, id=id, kind="list", entries=entries)
        elif value:
            return cls(label=label, value=value, id=id, href=href, kind=kind)
        if missing:
            return cls(label=label, id=id, kind="missing", value=missing)
        return None


def links(values: List[str]) -> List[Link]:
    """List entries, linked when they look like URLs or DOIs."""
    return [Link(text=v, href=f.link_href(v) if v.startswith(("http", "doi:", "10.")) else "")
            for v in values]


class Badge(Model):
    label: str
    href: str = ""
    title: str = ""


class Stat(Model):
    label: str
    n: str


class Tally(Model):
    label: str
    n: int


def tally(items: List[str]) -> List[Tally]:
    """Counts, commonest first, for a list of labels."""
    return [Tally(label=label, n=n) for label, n in Counter(items).most_common()]


# -- a crate's metadata ------------------------------------------------------

class CrateMetadata(Model):
    """A crate root's descriptive fields, read once and flattened.

    Everything is display text: references are followed into the graph
    (`publisher: {"@id": "#org"}` becomes the organization's name and URL),
    lists are lists of strings, and every linkable value carries its href
    beside it. `read` is the only place these fields are read from a root.
    """
    id: str = ""
    id_href: str = ""
    name: str = ""
    description: str = ""
    version: str = ""
    date_published: str = ""
    date_created: str = ""
    date_modified: str = ""
    size: str = ""
    authors: List[str] = []
    publisher: str = ""
    publisher_href: str = ""
    principal_investigator: str = ""
    contact: str = ""
    contact_href: str = ""
    identifier_label: str = ""
    identifier: str = ""
    identifier_href: str = ""
    license: str = ""
    license_href: str = ""
    terms_of_use: str = ""
    terms_href: str = ""
    confidentiality: str = ""
    keywords: List[str] = []
    citation: List[str] = []
    publications: List[str] = []
    funder: str = ""
    copyright: str = ""
    md5: str = ""
    access_href: str = ""
    data_governance: str = ""
    ethical_review: str = ""
    completeness: str = ""
    based_on: List[str] = []
    conforms_to: List[str] = []

    @classmethod
    def read(cls, root: Dict[str, Any], index: Dict[str, Dict[str, Any]],
             link_base: str = "") -> "CrateMetadata":
        root_id = root.get("@id", "")
        id_label, id_text, id_href = f.identifier(root.get("identifier"), link_base)
        publisher = f.resolved(root.get("publisher"), index)
        return cls(
            id=root_id,
            id_href=f.ark_href(root_id, link_base),
            name=f.first(root, "name") or root_id,
            description=f.first(root, "description"),
            version=f.first(root, "version"),
            date_published=f.first(root, "datePublished"),
            date_created=f.first(root, "dateCreated"),
            date_modified=f.first(root, "dateModified"),
            size=(f.human_size(root.get("contentSize"))
                  or f.human_size(root.get("evi:totalContentSizeBytes"))),
            authors=f.authors(root, index),
            publisher=f.text(publisher),
            publisher_href=f.http_url(publisher),
            principal_investigator=f.first(root, "principalInvestigator"),
            contact=f.first(root, "contactEmail"),
            contact_href=f.link_href(root.get("contactEmail")),
            identifier_label=id_label,
            identifier=id_text,
            identifier_href=id_href,
            license=f.first(root, "license"),
            license_href=f.http_url(root.get("license")),
            terms_of_use=f.first(root, "conditionsOfAccess"),
            terms_href=f.http_url(root.get("conditionsOfAccess")),
            confidentiality=f.first(root, "confidentialityLevel"),
            keywords=f.keywords(root),
            citation=f.publications(root.get("citation")),
            publications=f.publications(root.get("associatedPublication")),
            funder=f.joined(root.get("funder")),
            copyright=f.first(root, "copyrightNotice"),
            md5=f.first(root, "MD5", "md5"),
            access_href=(f.http_url(root.get("contentUrl")) or f.http_url(root.get("url"))
                         or f.http_url(root.get("sameAs"))),
            data_governance=(f.first(root, "dataGovernanceCommittee")
                             or f.additional_property(root, "Data Governance Committee")),
            ethical_review=f.first(root, "ethicalReview"),
            completeness=(f.first(root, "completeness")
                          or f.additional_property(root, "Completeness")),
            based_on=f.as_list(root.get("isBasedOn")),
            conforms_to=f.as_list(root.get("conformsTo")),
        )

    @computed_field

    @property
    def date(self) -> str:
        """The one date a card shows, to the day."""
        return f.date_only(self.date_published or self.date_created or self.date_modified)


# -- composition cards -------------------------------------------------------

class CellLine(Model):
    id: str
    name: str
    organism: str = ""


class PatternInput(Model):
    crate: Optional[str] = None     # named when the input came from another crate
    formats: List[str]


class Pattern(Model):
    """`inputs -> outputs` in formats, for a computation or experiment."""
    inputs: List[PatternInput]
    outputs: List[str]
    n: int = 1


class Patterns(Model):
    rows: List[Pattern] = []
    more: int = 0


class Details(Model):
    """Counts, formats, access, inputs and work patterns for one crate."""
    files: int = 0
    models: int = 0
    software: int = 0
    instruments: int = 0
    samples: int = 0
    experiments: int = 0
    computations: int = 0
    schemas: int = 0
    other: int = 0
    with_provenance: int = 0
    file_formats: List[Tally] = []
    software_formats: List[Tally] = []
    model_formats: List[Tally] = []
    file_access: List[Tally] = []
    software_access: List[Tally] = []
    computation_patterns: Patterns = Patterns()
    experiment_patterns: Patterns = Patterns()
    experiment_types: List[Tally] = []
    cell_lines: List[CellLine] = []
    organisms: List[Tally] = []
    input_datasets: List[Tally] = []

    @computed_field

    @property
    def activities(self) -> int:
        return self.computations + self.experiments

    @computed_field

    @property
    def inputs(self) -> int:
        return sum(row.n for row in self.input_datasets)


class ContentRow(Model):
    """One entity in a single crate's contents table."""
    kind: str
    name: str
    id: str
    id_href: str = ""
    description: str = ""
    format: str = ""
    size: str = ""
    access: str = ""
    access_href: str = ""
    search: str = ""


class Statistics(Model):
    """The QC / summary-statistics report a crate points at."""
    name: str
    href: str


class Card(Model):
    """One composition card: a constituent crate, or the crate itself."""
    position: int
    anchor: str
    meta: CrateMetadata
    size: str = ""                  # the metadata's, else measured on disk
    evidence_href: str = ""
    evidence_label: str = ""
    preview_href: str = ""
    statistics: Optional[Statistics] = None
    metadata_path: str = ""
    details: Details = Details()
    contents: List[ContentRow] = []
    notes: List[str] = []
    published: bool = True
    search: str = ""


# -- the datasheet -----------------------------------------------------------

class NavEntry(Model):
    href: str
    label: str
    children: List["NavEntry"] = []


class Links(Model):
    """Companion pages beside the datasheet, as hrefs ("" when absent)."""
    evidence_graph: str = ""
    graph_json: str = ""
    review_html: str = ""
    review_json: str = ""
    interpretation_html: str = ""
    interpretation_json: str = ""


class Summary(Model):
    description: str = ""
    truncated: bool = False
    stats: List[Stat] = []
    extra: List[Stat] = []
    entities: int = 0
    formats: List[str] = []
    formats_more: int = 0
    access_href: str = ""


class Regulatory(Model):
    """The human-subjects block. Absent fields stay empty and the template
    says so, rather than assuming a reassuring default."""
    human_subject_research: str = ""
    deidentified: str = ""
    fda_regulated: str = ""
    irb_protocol_id: str = ""
    irb: Any = ""                   # a node (name, contactPoint, address) or text
    exemptions: str = ""
    informed_consent: str = ""
    at_risk_populations: str = ""


class Segment(Model):
    """A drawn share of the review donut or a section bar."""
    label: str
    cls: str
    n: int
    length: float = 0.0
    offset: float = 0.0
    pct: float = 0.0


class Swatch(Model):
    label: str
    cls: str


class Criterion(Model):
    id: str = ""
    name: str = ""
    gating: bool = False
    score: Optional[int] = None
    label: str
    basis: List[str] = []
    evidence_count: int = 0
    error: Optional[str] = None


class ReviewSection(Model):
    number: Optional[int] = None
    title: str = ""
    gating: bool = False
    criteria: List[Criterion]
    tally: Dict[str, int]
    total: int
    estimated: int
    segments: List[Segment] = []
    points: int = 0
    possible: int = 0


class ReviewPanel(Model):
    rubric: str = ""
    rubric_version: str = ""
    generated: str = ""
    network_checks: bool = False
    sections: List[ReviewSection]
    tally: Dict[str, int]
    criteria_total: int
    estimated: int
    donut: List[Segment]
    circumference: float
    legend: List[Swatch]
    points: int
    possible: int


class CompositionSection(Model):
    cards: List[Card]
    is_release: bool = False

    @computed_field

    @property
    def count(self) -> int:
        return len(self.cards)

    @computed_field

    @property
    def show_index(self) -> bool:
        return len(self.cards) > 1


class Page(Model):
    """The hero every page shares."""
    kicker: str
    title: str
    ark: str = ""
    ark_href: str = ""
    description: str = ""
    chips: List[str] = []
    badges: List[Badge] = []
    body_class: str = ""
    source: str = ""
    generated_at: str = ""


class DatasheetPage(Page):
    nav: List[NavEntry]
    summary: Summary
    review: Optional[ReviewPanel] = None
    overview: List[Row]
    regulatory: Regulatory
    use_cases: List[Row]
    composition: CompositionSection
    distribution: List[Row]
    links: Links = Links()


# -- the preview -------------------------------------------------------------

class RefRow(Model):
    """A computation's input, output, software or model."""
    id: str
    name: str
    format: str = ""
    crate: str = ""                 # named when it came from another crate


class ComputationDetails(Model):
    inputs: List[RefRow] = []
    outputs: List[RefRow] = []
    software: List[RefRow] = []
    models: List[RefRow] = []
    command: str = ""
    parameters: List[str] = []

    @computed_field

    @property
    def any(self) -> bool:
        return bool(self.inputs or self.outputs or self.software or self.models
                    or self.command)


class SchemaProperty(Model):
    name: str
    type: str
    description: str = ""


class Entry(Model):
    """One entity row of a preview tab."""
    id: str
    id_href: str = ""
    name: str
    description: str = ""
    description_short: str = ""
    date: str = ""
    format: str = ""
    size: str = ""
    access: str = ""
    access_href: str = ""
    experiment_type: str = ""
    manufacturer: str = ""
    types: str = ""
    computation: Optional[ComputationDetails] = None
    properties: List[SchemaProperty] = []


class Tab(Model):
    key: str
    label: str
    entries: List[Entry]

    @computed_field

    @property
    def n(self) -> int:
        return len(self.entries)


class PreviewPage(Page):
    rows: List[Row]
    tabs: List[Tab]
    datasheet_href: str = ""
    evidence_href: str = ""

    @computed_field

    @property
    def entity_total(self) -> int:
        return sum(len(tab.entries) for tab in self.tabs)
