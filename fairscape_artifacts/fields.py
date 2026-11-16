"""Readers for the uneven fields real crates carry.

Any value a crate puts in a field may be a string, an `{"@id"}` stub, an
inline object, or a list of any of those. These helpers flatten that into
plain strings and lists so the builders and templates never branch on shape.
Everything here is pure: no I/O, no HTML.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from fairscape_artifacts.crate import PROV_TYPE_FALLBACK, evi_short_types, short_types
from fairscape_artifacts.models import CrateMetadata, Link

# -- scalars -----------------------------------------------------------------

def text(value: Any) -> str:
    """Flatten whatever a crate put in a field into something displayable."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        return str(value.get("name") or value.get("@id") or value.get("value") or "").strip()
    if isinstance(value, list):
        return ", ".join(p for p in (text(v) for v in value) if p)
    return str(value)


def first(node: Dict[str, Any], *fields: str) -> str:
    """The first populated field, flattened."""
    for field in fields:
        value = text(node.get(field))
        if value:
            return value
    return ""


def as_list(value: Any) -> List[str]:
    """A list of display strings; a bare string is one item, not split."""
    if value is None:
        return []
    items = value if isinstance(value, list) else [value]
    out: List[str] = []
    for item in items:
        s = text(item)
        if s:
            out.append(s)
    return out


def joined(value: Any, sep: str = ", ") -> str:
    return sep.join(as_list(value))


def keywords(node: Dict[str, Any]) -> List[str]:
    """`keywords` as a list; a comma- or semicolon-joined string is split."""
    raw = node.get("keywords")
    if isinstance(raw, str):
        sep = ";" if ";" in raw else ","
        return [k.strip() for k in raw.split(sep) if k.strip()]
    return as_list(raw)


def dedupe(items: List[str]) -> List[str]:
    seen = set()
    out = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def publications(value: Any) -> List[str]:
    """`associatedPublication` / `citation` as a deduplicated list."""
    return dedupe(as_list(value))


def additional_property(node: Dict[str, Any], *names: str) -> str:
    """`additionalProperty` value whose `name` or `propertyID` is one of `names`."""
    props = node.get("additionalProperty")
    if not isinstance(props, list):
        return ""
    for prop in props:
        if not isinstance(prop, dict):
            continue
        key = prop.get("name") or prop.get("propertyID")
        if key in names:
            return text(prop.get("value"))
    return ""


def yes_no(value: Any) -> str:
    if isinstance(value, bool):
        return "Yes" if value else "No"
    return text(value)


_ISO_DT_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})[T ]\d{2}:\d{2}")


def date_only(value: Any) -> str:
    """An ISO timestamp reduced to its date; anything else untouched."""
    raw = text(value)
    match = _ISO_DT_RE.match(raw)
    return match.group(1) if match else raw


def truncate(value: str, limit: int = 500) -> Tuple[str, bool]:
    """Cut at a word boundary; returns `(text, was_truncated)`."""
    value = value or ""
    if len(value) <= limit:
        return value, False
    cut = value[:limit].rsplit(" ", 1)[0]
    return cut, True


# -- sizes -------------------------------------------------------------------

_UNITS = ("B", "KB", "MB", "GB", "TB", "PB")
_SIZE_RE = re.compile(r"^\s*([0-9]*\.?[0-9]+)\s*([KMGTP]?i?B)?\s*$", re.I)


def size_bytes(value: Any) -> Optional[float]:
    """Bytes for a `contentSize` written as a number, '12239', or '441.2 GB'."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = _SIZE_RE.match(str(value))
    if not match:
        return None
    raw_unit = (match.group(2) or "B").upper()
    base = 1024 if "I" in raw_unit else 1000      # KiB, MiB, ... are binary
    number, unit = float(match.group(1)), raw_unit.replace("I", "")
    return number * (base ** _UNITS.index(unit))


def human_size(value: Any) -> str:
    """Decimal units, one decimal: the convention release crates already use."""
    if value is None or value == "":
        return ""
    if isinstance(value, str) and not value.strip().replace(".", "", 1).isdigit():
        return value.strip()      # already formatted ("441.2 GB")
    size = size_bytes(value)
    if size is None:
        return text(value)
    for unit in _UNITS:
        if size < 1000 or unit == "PB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1000
    return text(value)


# -- links -------------------------------------------------------------------

def http_url(value: Any) -> str:
    """The first http(s) URL in a field, or ''.

    An object is read for its `@id` / `url` before its display text, so
    `{"@id": "https://spdx.org/licenses/MIT", "name": "MIT"}` still links.
    """
    items = value if isinstance(value, list) else [value]
    for item in items:
        candidates = ([item.get("@id"), item.get("url"), text(item)]
                      if isinstance(item, dict) else [text(item)])
        for candidate in candidates:
            if isinstance(candidate, str) and candidate.startswith(("http://", "https://")):
                return candidate.strip()
    return ""


def doi_href(value: Any) -> str:
    """A DOI in any spelling as a resolvable URL, or '' when it is not one."""
    raw = text(value)
    if not raw:
        return ""
    if raw.startswith(("http://", "https://")):
        return raw
    if raw.lower().startswith("doi:"):
        return "https://doi.org/" + raw[4:].strip()
    if re.match(r"^10\.\d{4,}/", raw):
        return "https://doi.org/" + raw
    return ""


def link_href(value: Any) -> str:
    """Any URL-ish string as an href: http(s), a DOI, or a mailto."""
    raw = text(value)
    if not raw:
        return ""
    if "@" in raw and " " not in raw and not raw.startswith("http"):
        return "mailto:" + raw
    return doi_href(raw)


def resolved(value: Any, index: Dict[str, Dict[str, Any]]) -> Any:
    """A bare `{"@id": ...}` reference swapped for the node it names, so a
    field written as a reference displays like one written inline."""
    if isinstance(value, list):
        return [resolved(v, index) for v in value]
    if isinstance(value, dict) and set(value) <= {"@id", "@type"} and value.get("@id") in index:
        return index[value["@id"]]
    return value


def authors(root: Dict[str, Any], index: Dict[str, Dict[str, Any]]) -> List[str]:
    """Author names, following `{@id}` stubs into the graph."""
    raw = root.get("author")
    entries = raw if isinstance(raw, list) else ([raw] if raw else [])
    names = []
    for entry in entries:
        if isinstance(entry, dict):
            name = text(entry.get("name"))
            if not name and entry.get("@id") in index:
                name = first(index[entry["@id"]], "name")
            names.append(name or text(entry))
        elif isinstance(entry, str):
            if entry in index and first(index[entry], "name"):
                names.append(first(index[entry], "name"))
            elif entry.strip():
                names.append(entry.strip())
    return [n for n in names if n]


def identifier(value: Any, link_base: str = "") -> Tuple[str, str, str]:
    """`(label, text, href)` for an `identifier`, labelled by what it is.

    A DOI links to doi.org and an ARK through `link_base`; a PropertyValue
    is labelled by its `propertyID`; anything else is just "Identifier".
    """
    if isinstance(value, list):
        value = next((v for v in value if text(v)), None)
    label = ""
    if isinstance(value, dict) and value.get("propertyID"):
        label = text(value.get("propertyID"))
        raw = text(value.get("value") or value.get("@id") or value.get("name"))
    else:
        raw = text(value)
    if not raw:
        return "", "", ""
    lowered = raw.lower()
    if lowered.startswith("doi:") or re.match(r"^10\.\d{4,}/", raw) or "doi.org/" in lowered:
        return label or "DOI", raw, doi_href(raw)
    if raw.startswith("ark:"):
        return label or "ARK", raw, ark_href(raw, link_base)
    href = raw if raw.startswith(("http://", "https://")) else ""
    return label or "Identifier", raw, href


def ark_href(ark: str, link_base: str) -> str:
    """Where an id resolves: an ARK under the server base, when one was
    given; an http(s) id as itself; anything else (a local path, `./`, a
    `#fragment`) has nowhere to go."""
    if not ark:
        return ""
    if ark.startswith(("http://", "https://")):
        return ark
    if not link_base or not ark.startswith("ark:"):
        return ""
    return link_base.rstrip("/") + "/" + ark


# -- formats, access, provenance --------------------------------------------

_MIME_RE = re.compile(r"^[a-z]+/[a-z0-9.+_-]+$")

#: MIME subtypes whose everyday name is an extension.
_MIME_LABELS = {
    "gzip": "gz", "plain": "txt", "tab-separated-values": "tsv",
    "ld+json": "jsonld", "yaml": "yaml", "markdown": "md", "octet-stream": "binary",
    "jpeg": "jpg", "svg+xml": "svg", "vnd.ms-excel": "xls",
    "vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
}


def normalize_formats(raw: Any) -> List[str]:
    """Canonical, lowercase, dotless format tokens.

    Splits combined values (".cx / .tsv" -> ["cx", "tsv"]), collapses "tsv",
    ".tsv" and "TSV" to one token, keeps only the subtype of a MIME type
    ("text/csv" -> "csv"), and drops blanks and "unknown".
    """
    out: List[str] = []
    for item in (raw if isinstance(raw, list) else [raw]):
        if not isinstance(item, str):
            continue
        item = item.strip().lower()
        if _MIME_RE.match(item):
            subtype = item.split("/", 1)[1]
            if subtype.startswith("x-"):
                subtype = subtype[2:]
            out.append(_MIME_LABELS.get(subtype, subtype))
            continue
        for part in re.split(r"[\/,;]", item):
            token = part.strip().lstrip(".").strip()
            if token and token != "unknown":
                out.append(token)
    return out


def formats_of(node: Dict[str, Any]) -> List[str]:
    """Normalized formats for an entity, from `format` or `fileFormat`."""
    raw = node.get("format")
    if raw in (None, "", []):
        raw = node.get("fileFormat")
    return normalize_formats(raw)


def format_label(node: Dict[str, Any]) -> str:
    return ", ".join(formats_of(node))


def access(node: Dict[str, Any]) -> str:
    """Available / Embargoed / No link, from `contentUrl`."""
    url = node.get("contentUrl")
    if isinstance(url, list):
        url = url[0] if url else None
    if not url:
        return "No link"
    if str(url).strip().lower() == "embargoed":
        return "Embargoed"
    return "Available"


# -- classification ----------------------------------------------------------

#: EVI type -> display bucket.
_BUCKET_OF = {
    "Dataset": "dataset", "MLModel": "mlmodel", "Software": "software",
    "Instrument": "instrument", "Sample": "sample", "Experiment": "experiment",
    "Computation": "computation", "Schema": "schema", "ROCrate": "rocrate",
}


_EVI_PREFERENCE = ("ROCrate", "Computation", "Software", "MLModel", "Experiment",
                   "Schema", "Sample", "Instrument", "Dataset")

#: Types that say nothing about *which kind* of entity a node is: the bare
#: PROV classes, and RO-Crate's data-carrying types, which every file entity
#: in a PROV-only crate carries alongside `prov:Entity`.
_NOT_A_KIND = ("Entity", "Activity", "File", "MediaObject")


def bucket(node: Dict[str, Any]) -> str:
    """Which composition bucket an entity belongs to.

    The strict EVI type wins. A node with only PROV types (`prov:Entity`,
    `prov:Activity`) is read as a dataset or computation, but a node that
    carries any other type — `evi:BioChemEntity`, `Person`, `DefinedTerm` —
    is "other", even when a PROV type sits beside it: the PROV fallback is
    for crates converted from PROV bundles, not a license to count every
    entity as data.

    RO-Crate's own data types are the exception: a PROV-only crate spells its
    files `["File", "prov:Entity"]` because RO-Crate requires the `File`, so
    `File`/`MediaObject` sitting beside a PROV type does not disqualify it.
    `File` alone, with no PROV type, is still "other".
    """
    strict = evi_short_types(node)
    for preferred in _EVI_PREFERENCE:
        if preferred in strict:
            return _BUCKET_OF[preferred]
    shorts = short_types(node)
    if any(t in ("SoftwareSourceCode", "SoftwareApplication") for t in shorts):
        return "software"
    loose = [t for t in shorts if t not in _NOT_A_KIND]
    if loose:
        return "other"
    raw = node.get("@type") or []
    raw = [raw] if isinstance(raw, str) else [str(t) for t in raw]
    for candidate in raw:
        read_as = PROV_TYPE_FALLBACK.get(candidate)
        if read_as:
            return _BUCKET_OF[read_as]
    return "other"


# -- page models -------------------------------------------------------------

def links(values: List[str]) -> List[Link]:
    """List entries, linked when they look like URLs or DOIs."""
    return [Link(text=v, href=link_href(v) if v.startswith(("http", "doi:", "10.")) else "")
            for v in values]


def crate_metadata(root: Dict[str, Any], index: Dict[str, Dict[str, Any]],
                   link_base: str = "") -> CrateMetadata:
    """A crate root's descriptive fields, flattened into a `CrateMetadata`."""
    root_id = root.get("@id", "")
    id_label, id_text, id_href = identifier(root.get("identifier"), link_base)
    publisher = resolved(root.get("publisher"), index)
    date_published = first(root, "datePublished")
    date_created = first(root, "dateCreated")
    date_modified = first(root, "dateModified")
    return CrateMetadata(
        id=root_id,
        id_href=ark_href(root_id, link_base),
        name=first(root, "name") or root_id,
        description=first(root, "description"),
        version=first(root, "version"),
        date_published=date_published,
        date_created=date_created,
        date_modified=date_modified,
        date=date_only(date_published or date_created or date_modified),
        size=(human_size(root.get("contentSize"))
              or human_size(root.get("evi:totalContentSizeBytes"))),
        authors=authors(root, index),
        publisher=text(publisher),
        publisher_href=http_url(publisher),
        principal_investigator=first(root, "principalInvestigator"),
        contact=first(root, "contactEmail"),
        contact_href=link_href(root.get("contactEmail")),
        identifier_label=id_label,
        identifier=id_text,
        identifier_href=id_href,
        license=first(root, "license"),
        license_href=http_url(root.get("license")),
        terms_of_use=first(root, "conditionsOfAccess"),
        terms_href=http_url(root.get("conditionsOfAccess")),
        confidentiality=first(root, "confidentialityLevel"),
        keywords=keywords(root),
        citation=publications(root.get("citation")),
        publications=publications(root.get("associatedPublication")),
        funder=joined(root.get("funder")),
        copyright=first(root, "copyrightNotice"),
        md5=first(root, "MD5", "md5"),
        access_href=(http_url(root.get("contentUrl")) or http_url(root.get("url"))
                     or http_url(root.get("sameAs"))),
        data_governance=(first(root, "dataGovernanceCommittee")
                         or additional_property(root, "Data Governance Committee")),
        ethical_review=first(root, "ethicalReview"),
        completeness=(first(root, "completeness")
                      or additional_property(root, "Completeness")),
        based_on=as_list(root.get("isBasedOn")),
        conforms_to=as_list(root.get("conformsTo")),
    )
