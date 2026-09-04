"""Field readers: the shapes real crates put in a field, flattened."""

from __future__ import annotations

import pytest

from fairscape_artifacts import fields as f


def test_normalize_formats_collapses_variants_and_mime_types():
    assert f.normalize_formats([".tsv", "TSV", "tsv"]) == ["tsv", "tsv", "tsv"]
    assert f.normalize_formats(".cx / .tsv") == ["cx", "tsv"]
    assert f.normalize_formats(["text/csv", "application/gzip", "text/plain",
                                "application/x-yaml", "image/jpeg"]) == \
        ["csv", "gz", "txt", "yaml", "jpg"]
    assert f.normalize_formats(["unknown", "", None]) == []


def test_formats_of_reads_format_then_fileformat():
    assert f.formats_of({"format": "h5ad"}) == ["h5ad"]
    assert f.formats_of({"fileFormat": "raw"}) == ["raw"]
    assert f.formats_of({"format": "", "fileFormat": "csv"}) == ["csv"]


@pytest.mark.parametrize("value, expected", [
    ("30799079", "30.8 MB"),
    (19879650000000, "19.9 TB"),
    ("441.2 GB", "441.2 GB"),
    ("", ""),
    (None, ""),
    (12, "12 B"),
])
def test_human_size_uses_decimal_units_and_keeps_preformatted_text(value, expected):
    assert f.human_size(value) == expected


def test_size_bytes_parses_numbers_and_unit_strings():
    assert f.size_bytes("12239") == 12239
    assert f.size_bytes("441.2 GB") == pytest.approx(441.2e9)
    assert f.size_bytes("1.11 TB") == pytest.approx(1.11e12)
    assert f.size_bytes("lots") is None


@pytest.mark.parametrize("value, expected", [
    ("https://doi.org/10.18130/V3/HIGT4C", "https://doi.org/10.18130/V3/HIGT4C"),
    ("doi:10.1101/2024.05.21.589311", "https://doi.org/10.1101/2024.05.21.589311"),
    ("10.18130/V3/HIGT4C", "https://doi.org/10.18130/V3/HIGT4C"),
    ("ark:59853/not-a-doi", ""),
    ("", ""),
])
def test_doi_href(value, expected):
    assert f.doi_href(value) == expected


def test_link_href_makes_mailto_for_addresses():
    assert f.link_href("tideker@health.ucsd.edu") == "mailto:tideker@health.ucsd.edu"
    assert f.link_href("https://example.org") == "https://example.org"
    assert f.link_href("Trey Ideker") == ""


def test_date_only_trims_iso_timestamps():
    assert f.date_only("2026-05-22T13:23:35.699025+00:00") == "2026-05-22"
    assert f.date_only("2026-06-30") == "2026-06-30"
    assert f.date_only("02/28/2025") == "02/28/2025"


@pytest.mark.parametrize("node, expected", [
    ({"@type": ["prov:Entity", "https://w3id.org/EVI#Dataset"]}, "dataset"),
    ({"@type": ["Dataset", "https://w3id.org/EVI#ROCrate"]}, "rocrate"),
    ({"@type": "EVI:Schema"}, "schema"),
    ({"@type": ["prov:Activity", "https://w3id.org/EVI#Experiment"]}, "experiment"),
    ({"@type": ["prov:Entity", "https://w3id.org/EVI#Sample"]}, "sample"),
    ({"@type": "SoftwareSourceCode"}, "software"),
    # PROV-only nodes fall back to the PROV reading ...
    ({"@type": "prov:Entity"}, "dataset"),
    ({"@type": ["prov:Activity"]}, "computation"),
    ({"@type": "http://www.w3.org/ns/prov#Entity"}, "dataset"),
    # RO-Crate makes every file entity a File, so that does not disqualify it.
    ({"@type": ["File", "prov:Entity"]}, "dataset"),
    ({"@type": ["File", "http://www.w3.org/ns/prov#Activity"]}, "computation"),
    # ... but File alone says nothing about provenance.
    ({"@type": "File"}, "other"),
    # ... and a node with any other type beside its PROV type does not qualify.
    ({"@type": ["prov:Entity", "evi:BioChemEntity"]}, "other"),
    ({"@type": "Person"}, "other"),
    ({"@type": "DefinedTerm"}, "other"),
    ({}, "other"),
])
def test_bucket(node, expected):
    assert f.bucket(node) == expected


def test_has_provenance_reads_every_spelling():
    assert f.has_provenance({"generatedBy": [{"@id": "x"}]})
    assert f.has_provenance({"prov:wasGeneratedBy": "x"})
    assert f.has_provenance({"derivedFrom": [{"@id": "x"}]})
    assert not f.has_provenance({"generatedBy": []})
    assert not f.has_provenance({})


def test_access_status():
    assert f.access({"contentUrl": "https://x"}) == "Available"
    assert f.access({"contentUrl": "Embargoed"}) == "Embargoed"
    assert f.access({"contentUrl": ["file:///a"]}) == "Available"
    assert f.access({}) == "No link"


def test_additional_property_by_name_or_property_id():
    node = {"additionalProperty": [
        {"@type": "PropertyValue", "name": "IRB Protocol ID", "value": "IRB-1"},
        {"@type": "PropertyValue", "propertyID": "treatment", "value": "DMSO"},
    ]}
    assert f.additional_property(node, "IRB Protocol ID") == "IRB-1"
    assert f.additional_property(node, "treatment") == "DMSO"
    assert f.additional_property(node, "missing") == ""


def test_truncate_cuts_at_a_word_boundary():
    text, cut = f.truncate("alpha beta gamma delta", 12)
    assert (text, cut) == ("alpha beta", True)
    assert f.truncate("short", 12) == ("short", False)
