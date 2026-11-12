"""A release whose constituent crates are not published beside it.

Controlled-access releases (CHoRUS V3) list their sub-crates as stubs whose
`ro-crate-metadata` pointer is a bare `ro-crate-metadata.json`. Resolved
against the release's directory that is the release's own metadata file, so
every constituent used to load as the release itself and the composition
showed the root crate once per stub.
"""

import json
import os

from fairscape_artifacts import composition, datasheet
from fairscape_artifacts.crate import Crate

EVI = "https://w3id.org/EVI#"
ROCRATE = [EVI + "Dataset", EVI + "ROCrate"]
ROOT = "https://doi.org/10.0/RELEASE"


def _stub(name, sub, pointer="ro-crate-metadata.json"):
    return {"@id": f"file:///prod/{sub}/ro-crate-metadata.json", "@type": ROCRATE,
            "name": name, "description": f"{name} description",
            "isPartOf": [{"@id": ROOT}], "ro-crate-metadata": pointer}


def _release(tmp_path, stubs):
    root = {"@id": ROOT, "@type": ROCRATE, "name": "Release",
            "description": "The release", "hasPart": [{"@id": s["@id"]} for s in stubs]}
    data = {"@graph": [{"@id": "ro-crate-metadata.json", "@type": "CreativeWork",
                        "about": {"@id": ROOT}}, root] + stubs}
    path = tmp_path / "ro-crate-metadata.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return Crate.load(str(path))


def test_a_stub_pointing_at_the_release_is_not_the_release(tmp_path, capsys):
    crate = _release(tmp_path, [_stub("Images", "images"), _stub("EHR", "omop"),
                                _stub("Waveforms", "waveforms")])
    subs = crate.sub_crates()
    assert [s.crate.name for s in subs] == ["Images", "EHR", "Waveforms"]
    assert all(not s.published for s in subs)
    assert ROOT not in {s.crate.root_id for s in subs}
    assert "not published" in capsys.readouterr().err


def test_a_missing_sub_crate_is_kept_from_its_stub(tmp_path):
    crate = _release(tmp_path, [_stub("Gone", "gone", pointer="gone/ro-crate-metadata.json")])
    (sub,) = crate.sub_crates()
    assert not sub.published and sub.crate.name == "Gone"


def test_unpublished_cards_borrow_nothing_from_the_release(tmp_path):
    (tmp_path / composition.GRAPH_HTML).write_text("<html></html>", encoding="utf-8")
    crate = _release(tmp_path, [_stub("Images", "images"), _stub("EHR", "omop")])
    comp = composition.build(crate, out_dir=str(tmp_path))
    assert comp.is_release
    cards = [item.card for item in comp.items]
    assert [c.meta.name for c in cards] == ["Images", "EHR"]
    for item, c in zip(comp.items, cards):
        assert not c.published
        assert c.size == "" and c.evidence_href == "" and c.preview_href == ""
        assert item.preview_path == ""
        assert c.meta.description.endswith("description")
        assert c.notes and "not published" in c.notes[0]
    html = datasheet.html(datasheet.build(crate, composition=comp))
    assert html.count("Not published with this release") == 2
    assert "Content summary" not in html


def test_a_published_sub_crate_still_loads(tmp_path):
    sub_root = "ark:1/sub"
    sub_dir = tmp_path / "sub"
    sub_dir.mkdir()
    (sub_dir / "ro-crate-metadata.json").write_text(json.dumps({"@graph": [
        {"@id": "ro-crate-metadata.json", "about": {"@id": sub_root}},
        {"@id": sub_root, "@type": ROCRATE, "name": "Real sub"}]}), encoding="utf-8")
    stub = _stub("Stub name", "sub", pointer="sub/ro-crate-metadata.json")
    stub["@id"] = sub_root
    crate = _release(tmp_path, [stub])
    (sub,) = crate.sub_crates()
    assert sub.published and sub.crate.name == "Real sub" and sub.rel_dir == "sub"
