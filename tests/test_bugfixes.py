"""Regression tests for the 2026-10-12 bug hunt (`bugs.md`).

Each test is named after its entry in `bugs.md` and is built from the repro
recorded there. Every one failed against `b2bad48` and passes with the fix.
"""

import json
import os

import pytest

from fairscape_artifacts import composition, datasheet, evidence, fields, outputs, preview
from fairscape_artifacts.crate import Crate

EVI = "https://w3id.org/EVI#"
DS = EVI + "Dataset"
COMP = EVI + "Computation"
SW = EVI + "Software"
ROCRATE = ["Dataset", EVI + "ROCrate"]


def _descriptor(root_id):
    return {"@id": "ro-crate-metadata.json", "@type": "CreativeWork", "about": {"@id": root_id}}


def _write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    return path


def _not_found(graph):
    return sorted(k for k, v in graph["@graph"].items() if v.get("error"))


# -- B1: usedDataset -> RO-Crate resolves to that crate's outputs ------------

def _b1_graph():
    return [
        _descriptor("ark:1/root"),
        {"@id": "ark:1/root", "@type": ROCRATE, "name": "r",
         EVI + "outputs": [{"@id": "ark:1/out"}]},
        {"@id": "ark:1/out", "@type": DS, "name": "out", "generatedBy": {"@id": "ark:1/comp"}},
        {"@id": "ark:1/comp", "@type": COMP, "name": "comp",
         "usedDataset": [{"@id": "ark:1/inner"}]},
        {"@id": "ark:1/inner", "@type": ROCRATE, "name": "inner",
         EVI + "outputs": [{"@id": "ark:1/innerout"}]},
        {"@id": "ark:1/innerout", "@type": DS, "name": "innerout",
         "generatedBy": {"@id": "ark:1/c0"}},
        {"@id": "ark:1/c0", "@type": COMP, "name": "c0"},
    ]


def test_B1_used_crate_resolves_to_its_outputs_in_same_graph():
    graph = evidence.build(Crate({"@graph": _b1_graph()}))
    assert _not_found(graph) == []
    nodes = graph["@graph"]
    assert nodes["ark:1/comp"]["usedDataset"] == [{"@id": "ark:1/innerout"}]
    assert nodes["ark:1/innerout"]["name"] == "innerout"
    # and the walk keeps going upstream of the inner crate's output
    assert nodes["ark:1/innerout"]["generatedBy"] == {"@id": "ark:1/c0"}
    assert "ark:1/c0" in nodes


def test_B1_used_crate_resolves_through_a_linked_crate_on_disk(tmp_path):
    up_root = "ark:1/upstream"
    _write(str(tmp_path / "upstream" / "ro-crate-metadata.json"), {"@graph": [
        _descriptor(up_root),
        {"@id": up_root, "@type": ROCRATE, "name": "Upstream",
         "hasPart": [{"@id": "ark:1/up-out"}, {"@id": "ark:1/up-run"}],
         EVI + "outputs": [{"@id": "ark:1/up-out"}]},
        {"@id": "ark:1/up-out", "@type": DS, "name": "up-out",
         "generatedBy": {"@id": "ark:1/up-run"}},
        {"@id": "ark:1/up-run", "@type": COMP, "name": "up-run"},
    ]})
    consumer = _write(str(tmp_path / "consumer" / "ro-crate-metadata.json"), {"@graph": [
        _descriptor("ark:1/consumer"),
        {"@id": "ark:1/consumer", "@type": ROCRATE, "name": "Consumer",
         "hasPart": [{"@id": "ark:1/c-out"}, {"@id": "ark:1/c-run"}],
         EVI + "outputs": [{"@id": "ark:1/c-out"}]},
        {"@id": "ark:1/c-out", "@type": DS, "name": "c-out", "generatedBy": {"@id": "ark:1/c-run"}},
        {"@id": "ark:1/c-run", "@type": COMP, "name": "c-run",
         "usedDataset": [{"@id": up_root}]},
        # the stub conversion's linking writes: no outputs of its own
        {"@id": up_root, "@type": ROCRATE, "name": "Upstream",
         "ro-crate-metadata": "../upstream/ro-crate-metadata.json"},
    ]})
    graph = evidence.build(Crate.load(consumer))
    nodes = graph["@graph"]
    assert _not_found(graph) == []
    assert nodes["ark:1/c-run"]["usedDataset"] == [{"@id": "ark:1/up-out"}]
    assert nodes["ark:1/up-out"]["generatedBy"] == {"@id": "ark:1/up-run"}


def test_B1_used_crate_with_no_declared_outputs_derives_them(tmp_path):
    graph = _b1_graph()
    del graph[4][EVI + "outputs"]           # the inner crate declares nothing
    graph[4]["hasPart"] = [{"@id": "ark:1/innerout"}]
    out = evidence.build(Crate({"@graph": graph}))
    assert _not_found(out) == []
    assert out["@graph"]["ark:1/comp"]["usedDataset"] == [{"@id": "ark:1/innerout"}]


# -- B3: condensation keeps ancestors something else still uses -------------

def _b3_crate():
    g = [
        _descriptor("ark:1/root"),
        {"@id": "ark:1/root", "@type": ROCRATE, "name": "r",
         EVI + "outputs": [{"@id": "ark:1/merged"}, {"@id": "ark:1/other_out"}]},
        {"@id": "ark:1/merged", "@type": DS, "name": "merged", "generatedBy": {"@id": "ark:1/merge"}},
        {"@id": "ark:1/merge", "@type": COMP, "name": "merge",
         "usedDataset": [{"@id": f"ark:1/m{i}"} for i in range(1, 7)]},
        {"@id": "ark:1/X", "@type": DS, "name": "shared X"},
        {"@id": "ark:1/other_out", "@type": DS, "name": "other_out",
         "generatedBy": {"@id": "ark:1/c_other"}},
        {"@id": "ark:1/c_other", "@type": COMP, "name": "c_other", "usedDataset": [{"@id": "ark:1/X"}]},
    ]
    for i in range(1, 7):
        source = "ark:1/X" if i == 6 else f"ark:1/x{i}"
        if i != 6:
            g.append({"@id": source, "@type": DS, "name": f"x{i}"})
        g.append({"@id": f"ark:1/m{i}", "@type": DS, "name": f"m{i}", "format": "text/csv",
                  "generatedBy": {"@id": f"ark:1/p{i}"}})
        g.append({"@id": f"ark:1/p{i}", "@type": COMP, "name": "prep",
                  "usedDataset": [{"@id": source}]})
    return Crate({"@graph": g})


def test_B3_condensation_keeps_an_ancestor_used_outside_the_group():
    graph = evidence.build(_b3_crate())
    nodes = graph["@graph"]
    assert graph["condensation_stats"]["condensed"]
    assert "ark:1/X" in nodes
    assert nodes["ark:1/c_other"]["usedDataset"] == [{"@id": "ark:1/X"}]
    group = [n for n in nodes.values() if "DatasetGroup" in str(n.get("@type"))]
    assert len(group) == 1 and group[0]["evi:memberCount"] == 6


def test_B3_exclusive_ancestors_still_collapse():
    nodes = evidence.build(_b3_crate())["@graph"]
    # x2..x5 and their prep steps are reached only through grouped members
    for gone in ("ark:1/x2", "ark:1/x5", "ark:1/p3"):
        assert gone not in nodes


# -- B2: DatasetGroup ids are unique ----------------------------------------

def _fan_in(prefix, comp_name, software, n, fmt="text/csv"):
    """`n` datasets of `fmt`, each made by its own step that used `software`."""
    nodes, ids = [], []
    for i in range(n):
        did, pid = f"ark:1/{prefix}-d{i}", f"ark:1/{prefix}-p{i}"
        nodes.append({"@id": did, "@type": DS, "name": f"{prefix}{i}", "format": fmt,
                      "generatedBy": {"@id": pid}})
        nodes.append({"@id": pid, "@type": COMP, "name": "step",
                      "usedSoftware": [{"@id": software}]})
        ids.append(did)
    return nodes, ids


def _groups(graph):
    return [n for n in graph["@graph"].values() if "DatasetGroup" in str(n.get("@type"))]


def test_B2_two_signature_groups_with_same_format_do_not_collide():
    a_nodes, a_ids = _fan_in("a", "step", "ark:1/swA", 6)
    b_nodes, b_ids = _fan_in("b", "step", "ark:1/swB", 6)
    g = [_descriptor("ark:1/root"),
         {"@id": "ark:1/root", "@type": ROCRATE, "name": "r", EVI + "outputs": [{"@id": "ark:1/out"}]},
         {"@id": "ark:1/out", "@type": DS, "name": "out", "generatedBy": {"@id": "ark:1/merge"}},
         {"@id": "ark:1/merge", "@type": COMP, "name": "merge",
          "usedDataset": [{"@id": i} for i in a_ids + b_ids]},
         {"@id": "ark:1/swA", "@type": SW, "name": "A"},
         {"@id": "ark:1/swB", "@type": SW, "name": "B"}] + a_nodes + b_nodes
    graph = evidence.build(Crate({"@graph": g}))
    ids = [s["groupId"] for s in graph["condensation_stats"]["groups"]]
    assert len(ids) == 2 and len(set(ids)) == 2
    groups = _groups(graph)
    assert sorted(g["evi:memberCount"] for g in groups) == [6, 6]


def test_B2_same_named_computations_do_not_collide():
    a_nodes, a_ids = _fan_in("a", "step", "ark:1/sw", 6)
    b_nodes, b_ids = _fan_in("b", "step", "ark:1/sw", 6)
    g = [_descriptor("ark:1/root"),
         {"@id": "ark:1/root", "@type": ROCRATE, "name": "r",
          EVI + "outputs": [{"@id": "ark:1/out1"}, {"@id": "ark:1/out2"}]},
         {"@id": "ark:1/out1", "@type": DS, "name": "out1", "generatedBy": {"@id": "ark:1/run1"}},
         {"@id": "ark:1/out2", "@type": DS, "name": "out2", "generatedBy": {"@id": "ark:1/run2"}},
         {"@id": "ark:1/run1", "@type": COMP, "name": "cellranger count",
          "usedDataset": [{"@id": i} for i in a_ids]},
         {"@id": "ark:1/run2", "@type": COMP, "name": "cellranger count",
          "usedDataset": [{"@id": i} for i in b_ids]},
         {"@id": "ark:1/sw", "@type": SW, "name": "sw"}] + a_nodes + b_nodes
    graph = evidence.build(Crate({"@graph": g}))
    groups = _groups(graph)
    assert len(groups) == 2
    nodes = graph["@graph"]
    assert nodes["ark:1/run1"]["usedDataset"] != nodes["ark:1/run2"]["usedDataset"]


# -- E1: add-io overwrites stale fields -------------------------------------

def test_E1_add_io_replaces_stale_outputs_and_keeps_unicode(tmp_path):
    path = _write(str(tmp_path / "ro-crate-metadata.json"), {"@graph": [
        _descriptor("ark:1/root"),
        {"@id": "ark:1/root", "@type": ROCRATE, "name": "Zürich données",
         "EVI:outputs": [{"@id": "ark:1/stale"}], "EVI:inputs": [{"@id": "ark:1/stale-in"}]},
        {"@id": "ark:1/in", "@type": DS, "name": "in"},
        {"@id": "ark:1/run", "@type": COMP, "name": "run", "usedDataset": [{"@id": "ark:1/in"}]},
        {"@id": "ark:1/out", "@type": DS, "name": "out", "generatedBy": {"@id": "ark:1/run"}},
    ]})
    ok, _ = outputs.write(path)
    assert ok
    raw = open(path, encoding="utf-8").read()
    root = json.loads(raw)["@graph"][1]
    output_keys = [k for k in root if k.split("#")[-1].split(":")[-1] == "outputs"]
    input_keys = [k for k in root if k.split("#")[-1].split(":")[-1] == "inputs"]
    assert output_keys == ["EVI:outputs"] and input_keys == ["EVI:inputs"]
    assert root["EVI:outputs"] == [{"@id": "ark:1/out"}]
    assert root["EVI:inputs"] == [{"@id": "ark:1/in"}]
    assert "Zürich données" in raw


# -- C1: name + @id objects keep their link ---------------------------------

def test_C1_http_url_reads_id_of_named_object():
    lic = {"@id": "https://spdx.org/licenses/MIT", "name": "MIT"}
    assert fields.http_url(lic) == "https://spdx.org/licenses/MIT"
    assert fields.http_url([{"name": "x", "url": "https://example.org/x"}]) == "https://example.org/x"


def test_C1_license_badge_and_rows_are_linked():
    crate = Crate({"@graph": [
        _descriptor("ark:1/root"),
        {"@id": "ark:1/root", "@type": ROCRATE, "name": "r",
         "license": {"@id": "https://spdx.org/licenses/MIT", "name": "MIT"},
         "publisher": {"@id": "https://example.org/pub", "name": "Pub"}},
    ]})
    badge = [b for b in datasheet.badges(crate, "") if b["label"] == "License"][0]
    assert badge["href"] == "https://spdx.org/licenses/MIT" and badge["title"] == "MIT"
    ctx = preview.build_context(crate, crate.index, {})
    rows = {r["label"]: r for r in ctx["rows"]}
    assert rows["License"]["value"] == "MIT"
    assert rows["License"]["href"] == "https://spdx.org/licenses/MIT"
    assert rows["Publisher"]["href"] == "https://example.org/pub"


# -- D1: --link-base links ARKs only ----------------------------------------

def test_D1_ark_href_only_for_arks():
    base = "https://fairscape.net"
    assert fields.ark_href("ark:59852/x", base) == "https://fairscape.net/ark:59852/x"
    assert fields.ark_href("https://example.org/x", base) == "https://example.org/x"
    assert fields.ark_href("data/a.csv", base) == ""
    assert fields.ark_href("./", base) == ""
    assert fields.ark_href("#local", base) == ""


# -- D2 / D3: sub-crate paths come from the file that loaded ----------------

def _release(tmp_path, pointer, local_path=None, sub_dir="sub"):
    _write(str(tmp_path / sub_dir / "ro-crate-metadata.json"), {"@graph": [
        _descriptor("ark:1/sub"),
        {"@id": "ark:1/sub", "@type": ROCRATE, "name": "Sub"},
    ]})
    stub = {"@id": "ark:1/sub", "@type": ROCRATE, "name": "Sub", "ro-crate-metadata": pointer}
    if local_path:
        stub["localPath"] = local_path
    return _write(str(tmp_path / "release" / "ro-crate-metadata.json"), {"@graph": [
        _descriptor("ark:1/release"),
        {"@id": "ark:1/release", "@type": ROCRATE, "name": "Release", "hasPart": [{"@id": "ark:1/sub"}]},
        stub,
    ]})


def test_D2_directory_pointer_gets_its_own_preview(tmp_path):
    path = _release(tmp_path, "../sub")
    crate = Crate.load(path)
    sub = crate.sub_crates()[0]
    assert sub.rel_dir == "../sub"
    comp = composition.build(crate, out_dir=crate.dir)
    assert comp.items[0].preview_path != os.path.join(crate.dir, "ro-crate-preview.html")
    assert os.path.normpath(comp.items[0].preview_path) == \
        str(tmp_path / "sub" / "ro-crate-preview.html")


def test_D3_local_path_fallback_records_the_path_that_loaded(tmp_path):
    real = str(tmp_path / "elsewhere" / "ro-crate-metadata.json")
    path = _release(tmp_path, "gone/ro-crate-metadata.json", local_path=real, sub_dir="elsewhere")
    crate = Crate.load(path)
    sub = crate.sub_crates()[0]
    assert sub.rel_dir == "../elsewhere"
    assert sub.metadata_path == "../elsewhere/ro-crate-metadata.json"


# -- C4: computation outputs without link-inverses --------------------------

def _uninversed_crate():
    return Crate({"@graph": [
        _descriptor("ark:1/root"),
        {"@id": "ark:1/root", "@type": ROCRATE, "name": "r"},
        {"@id": "ark:1/in", "@type": DS, "name": "in.h5", "format": "h5"},
        {"@id": "ark:1/run", "@type": COMP, "name": "run", "usedDataset": [{"@id": "ark:1/in"}]},
        {"@id": "ark:1/out", "@type": DS, "name": "out.h5ad", "format": "h5ad",
         "generatedBy": {"@id": "ark:1/run"}},
    ]})


def test_C4_preview_lists_outputs_from_generatedBy():
    crate = _uninversed_crate()
    ctx = preview.build_context(crate, crate.index, {})
    comps = [t for t in ctx["tabs"] if t["key"] == "computations"][0]["entries"]
    assert [o["id"] for o in comps[0]["computation"]["outputs"]] == ["ark:1/out"]


def test_C4_card_shows_patterns_and_says_they_were_derived(tmp_path):
    crate = _uninversed_crate()
    comp = composition.build(crate, out_dir=str(tmp_path), previews=False)
    card = comp.items[0].context
    rows = card["details"]["computation_patterns"]["rows"]
    assert rows and rows[0]["outputs"] == ["h5ad"]
    assert any("link-inverses" in note for note in card["notes"])


def test_C4_no_note_when_inverses_are_stored(tmp_path):
    crate = _uninversed_crate()
    crate.index["ark:1/run"]["generated"] = [{"@id": "ark:1/out"}]
    card = composition.build(crate, out_dir=str(tmp_path), previews=False).items[0].context
    assert not any("link-inverses" in note for note in card["notes"])


# -- C5: inputs derived when only outputs are stored ------------------------

def test_C5_inputs_derived_when_only_outputs_stored(tmp_path):
    crate = _uninversed_crate()
    crate.root[EVI + "outputs"] = [{"@id": "ark:1/out"}]
    inputs, outs = outputs.ensure(crate)
    assert inputs == [{"@id": "ark:1/in"}] and outs == [{"@id": "ark:1/out"}]
    card = composition.build(crate, out_dir=str(tmp_path), previews=False).items[0].context
    assert card["details"]["inputs"] == 1
    assert any("add-io" in note for note in card["notes"])


# ===========================================================================
# LOW
# ===========================================================================

import re

from fairscape_artifacts import cli


def _hrefs(page):
    html = open(page, encoding="utf-8").read()
    return [h for h in re.findall(r'href="([^"#]+)', html)
            if not re.match(r"^(https?:|mailto:|data:|javascript:|\$|')", h) and "+" not in h]


def _release_with_sub(tmp_path, sub_root_extra=None, stub_extra=None):
    """release/ with one constituent at release/sub/ (dataset + computation)."""
    release = tmp_path / "release"
    sub_root = {"@id": "ark:1/sub", "@type": ROCRATE, "name": "Sub",
                "hasPart": [{"@id": "ark:1/sub-in"}, {"@id": "ark:1/sub-out"}, {"@id": "ark:1/sub-run"}],
                **(sub_root_extra or {})}
    _write(str(release / "sub" / "ro-crate-metadata.json"), {"@graph": [
        _descriptor("ark:1/sub"), sub_root,
        {"@id": "ark:1/sub-in", "@type": DS, "name": "in", "format": "csv"},
        {"@id": "ark:1/sub-run", "@type": COMP, "name": "run", "usedDataset": [{"@id": "ark:1/sub-in"}]},
        {"@id": "ark:1/sub-out", "@type": DS, "name": "out", "format": "csv",
         "generatedBy": {"@id": "ark:1/sub-run"}},
    ]})
    return _write(str(release / "ro-crate-metadata.json"), {"@graph": [
        _descriptor("ark:1/release"),
        {"@id": "ark:1/release", "@type": ROCRATE, "name": "Release", "hasPart": [{"@id": "ark:1/sub"}]},
        {"@id": "ark:1/sub", "@type": ROCRATE, "name": "Sub",
         "ro-crate-metadata": "sub/ro-crate-metadata.json", **(stub_extra or {})},
    ]})


# -- D4: -d / -o links ------------------------------------------------------

def test_D4_d_flag_links_never_leave_the_output_dir(tmp_path):
    path = _release_with_sub(tmp_path, stub_extra={"localEvidenceGraph": {"@id": "sub/prov.html"}})
    open(str(tmp_path / "release" / "sub" / "prov.html"), "w").write("<html></html>")
    out = tmp_path / "out"
    assert cli.main(["all", path, "-d", str(out), "--no-review", "-q"]) == 0
    pages = [str(out / "ro-crate-datasheet.html"), str(out / "sub" / "ro-crate-preview.html")]
    for page in pages:
        for href in _hrefs(page):
            target = os.path.normpath(os.path.join(os.path.dirname(page), href))
            assert target.startswith(str(out)), (page, href)
    # the crate-internal file is linked as if `out` sat at the crate root
    assert "sub/prov.html" in _hrefs(pages[0])


def test_D4_datasheet_o_links_previews_to_the_moved_datasheet(tmp_path):
    path = _release_with_sub(tmp_path)
    target = tmp_path / "elsewhere" / "sheet.html"
    assert cli.main(["datasheet", path, "-o", str(target), "--no-review"]) == 0
    preview_page = str(tmp_path / "release" / "sub" / "ro-crate-preview.html")
    resolved = [os.path.normpath(os.path.join(os.path.dirname(preview_page), h))
                for h in _hrefs(preview_page)]
    assert str(target) in resolved


def test_D4_evidence_graph_o_writes_json_beside_the_html(tmp_path):
    path = _release_with_sub(tmp_path)
    target = tmp_path / "graphs" / "g.html"
    assert cli.main(["evidence-graph", path, "-o", str(target)]) == 0
    assert (tmp_path / "graphs" / "g.json").exists()
    assert not (tmp_path / "release" / "ro-crate-evidence-graph.json").exists()


@pytest.mark.parametrize("command", ["all", "add-io", "link-inverses"])
def test_D4_commands_that_ignore_o_reject_it(tmp_path, command):
    path = _release_with_sub(tmp_path)
    with pytest.raises(SystemExit) as err:
        cli.main([command, path, "-o", str(tmp_path / "x.html")])
    assert err.value.code == 2


# -- D5: localEvidenceGraph resolves against the node that carried it ------

def test_D5_local_evidence_graph_on_sub_crate_root_is_sub_relative(tmp_path):
    path = _release_with_sub(tmp_path, sub_root_extra={"localEvidenceGraph": {"@id": "prov.html"}})
    open(str(tmp_path / "release" / "sub" / "prov.html"), "w").write("<html></html>")
    crate = Crate.load(path)
    card = composition.build(crate, out_dir=crate.dir, previews=False).items[0].context
    assert card["evidence_href"] == "sub/prov.html"


def test_D5_local_evidence_graph_on_release_stub_stays_release_relative(tmp_path):
    path = _release_with_sub(tmp_path, stub_extra={"localEvidenceGraph": {"@id": "sub/prov.html"}})
    open(str(tmp_path / "release" / "sub" / "prov.html"), "w").write("<html></html>")
    crate = Crate.load(path)
    card = composition.build(crate, out_dir=crate.dir, previews=False).items[0].context
    assert card["evidence_href"] == "sub/prov.html"


# -- B9: hint when ids are unresolved and the crate is part of a release -----

def test_B9_unresolved_ids_print_a_reference_hint(tmp_path, capsys):
    path = _write(str(tmp_path / "c" / "ro-crate-metadata.json"), {"@graph": [
        _descriptor("ark:1/c"),
        {"@id": "ark:1/c", "@type": ROCRATE, "name": "C", "isPartOf": [{"@id": "ark:1/release"}],
         EVI + "outputs": [{"@id": "ark:1/out"}]},
        {"@id": "ark:1/out", "@type": DS, "name": "out", "generatedBy": {"@id": "ark:1/run"}},
        {"@id": "ark:1/run", "@type": COMP, "name": "run", "usedDataset": [{"@id": "ark:1/elsewhere"}]},
    ]})
    assert cli.main(["evidence-graph", path]) == 0
    err = capsys.readouterr().err
    assert "1 referenced id" in err and "--reference" in err and "ark:1/release" in err


def test_B9_no_hint_when_everything_resolves(tmp_path, capsys):
    path = _release_with_sub(tmp_path)
    assert cli.main(["evidence-graph", os.path.join(os.path.dirname(path), "sub")]) == 0
    assert "--reference" not in capsys.readouterr().err


# -- C3: identifier labelled by what it is ----------------------------------

def _id_crate(identifier):
    return Crate({"@graph": [
        _descriptor("ark:1/root"),
        {"@id": "ark:1/root", "@type": ROCRATE, "name": "r", "identifier": identifier},
    ]})


@pytest.mark.parametrize("identifier,label,href", [
    ("ark:59852/x", "ARK", "https://fairscape.net/ark:59852/x"),
    ("10.1234/abc", "DOI", "https://doi.org/10.1234/abc"),
    ("https://doi.org/10.1234/abc", "DOI", "https://doi.org/10.1234/abc"),
    ({"@type": "PropertyValue", "propertyID": "RRID", "value": "RRID:SCR_1"}, "RRID", ""),
    ("local-42", "Identifier", ""),
])
def test_C3_identifier_label_matches_its_kind(identifier, label, href):
    crate = _id_crate(identifier)
    base = "https://fairscape.net"
    rows = {r["label"]: r for r in preview.build_context(crate, crate.index, {}, link_base=base)["rows"]}
    assert label in rows and rows[label]["href"] == href
    assert "DOI" not in rows or label == "DOI"
    badge_labels = [b["label"] for b in datasheet.badges(crate, base)]
    assert label in badge_labels


# -- B8: the copy with provenance wins --------------------------------------

def test_B8_copy_with_provenance_beats_a_bare_copy_loaded_later(tmp_path):
    release = tmp_path / "release"
    shared = "ark:1/shared"
    _write(str(release / "a" / "ro-crate-metadata.json"), {"@graph": [
        _descriptor("ark:1/a"), {"@id": "ark:1/a", "@type": ROCRATE, "name": "A"},
        {"@id": shared, "@type": DS, "name": "shared", "generatedBy": {"@id": "ark:1/a-run"}},
        {"@id": "ark:1/a-run", "@type": COMP, "name": "a-run"},
    ]})
    _write(str(release / "b" / "ro-crate-metadata.json"), {"@graph": [
        _descriptor("ark:1/b"), {"@id": "ark:1/b", "@type": ROCRATE, "name": "B"},
        {"@id": shared, "@type": DS, "name": "shared"},
    ]})
    path = _write(str(release / "ro-crate-metadata.json"), {"@graph": [
        _descriptor("ark:1/release"),
        {"@id": "ark:1/release", "@type": ROCRATE, "name": "R",
         "hasPart": [{"@id": "ark:1/a"}, {"@id": "ark:1/b"}]},
        {"@id": "ark:1/a", "@type": ROCRATE, "ro-crate-metadata": "a/ro-crate-metadata.json"},
        {"@id": "ark:1/b", "@type": ROCRATE, "ro-crate-metadata": "b/ro-crate-metadata.json"},
    ]})
    crate = Crate.load(path)
    index, owner = crate.provenance_index()
    assert index[shared].get("generatedBy") and owner[shared] == "ark:1/a"
    comp = composition.build(crate, out_dir=str(release), previews=False)
    assert comp.index[shared].get("generatedBy") and comp.owner[shared] == "ark:1/a"


# -- C7: binary units -------------------------------------------------------

def test_C7_binary_units_use_1024():
    assert fields.size_bytes("1.5 KiB") == 1536
    assert fields.size_bytes("2 MiB") == 2 * 1024 ** 2
    assert fields.size_bytes("1.5 KB") == 1500


# -- C8: references resolved through the index ------------------------------

def test_C8_irb_and_publisher_references_resolve():
    crate = Crate({"@graph": [
        _descriptor("ark:1/root"),
        {"@id": "ark:1/root", "@type": ROCRATE, "name": "r",
         "irb": {"@id": "#irb"}, "publisher": {"@id": "#pub"}},
        {"@id": "#irb", "@type": "Organization", "name": "UCSD IRB",
         "contactPoint": {"email": "irb@ucsd.edu"}},
        {"@id": "#pub", "@type": "Organization", "name": "UC San Diego", "url": "https://ucsd.edu"},
    ]})
    assert datasheet.regulatory(crate)["irb"]["name"] == "UCSD IRB"
    rows = {r["label"]: r for r in preview.build_context(crate, crate.index, {})["rows"]}
    assert rows["Publisher"]["value"] == "UC San Diego"
    assert rows["Publisher"]["href"] == "https://ucsd.edu"


# -- C9: release summary counts the release root's own entities -------------

def test_C9_release_summary_counts_entities_on_the_release_root(tmp_path):
    path = _release_with_sub(tmp_path)
    data = json.load(open(path))
    data["@graph"].append({"@id": "ark:1/top", "@type": DS, "name": "top-level", "format": "pdf"})
    data["@graph"][1]["hasPart"].append({"@id": "ark:1/top"})
    _write(path, data)
    crate = Crate.load(path)
    comp = composition.build(crate, out_dir=crate.dir, previews=False)
    summary = datasheet.summary(crate, comp)
    assert summary["entities"] == 4      # 3 in the sub-crate + 1 on the release
    assert "pdf" in [x.lower() for x in summary["formats"]]


# -- C10: attribution keyed by crate id, not name ---------------------------

def test_C10_same_named_constituents_still_attribute_cross_crate_inputs(tmp_path):
    release = tmp_path / "release"
    _write(str(release / "a" / "ro-crate-metadata.json"), {"@graph": [
        _descriptor("ark:1/a"), {"@id": "ark:1/a", "@type": ROCRATE, "name": "Same"},
        {"@id": "ark:1/a-data", "@type": DS, "name": "a-data", "format": "csv"},
    ]})
    _write(str(release / "b" / "ro-crate-metadata.json"), {"@graph": [
        _descriptor("ark:1/b"), {"@id": "ark:1/b", "@type": ROCRATE, "name": "Same"},
        {"@id": "ark:1/b-run", "@type": COMP, "name": "b-run", "usedDataset": [{"@id": "ark:1/a-data"}]},
        {"@id": "ark:1/b-out", "@type": DS, "name": "b-out", "format": "h5",
         "generatedBy": {"@id": "ark:1/b-run"}},
    ]})
    path = _write(str(release / "ro-crate-metadata.json"), {"@graph": [
        _descriptor("ark:1/release"),
        {"@id": "ark:1/release", "@type": ROCRATE, "name": "R",
         "hasPart": [{"@id": "ark:1/a"}, {"@id": "ark:1/b"}]},
        {"@id": "ark:1/a", "@type": ROCRATE, "ro-crate-metadata": "a/ro-crate-metadata.json"},
        {"@id": "ark:1/b", "@type": ROCRATE, "ro-crate-metadata": "b/ro-crate-metadata.json"},
    ]})
    crate = Crate.load(path)
    comp = composition.build(crate, out_dir=str(release), previews=False)
    b_card = comp.items[1].context
    assert b_card["details"]["computation_patterns"]["rows"][0]["inputs"][0]["crate"] == "Same"
    ctx = preview.build_context(comp.items[1].crate, comp.index, comp.owner, names=comp.names)
    run = [t for t in ctx["tabs"] if t["key"] == "computations"][0]["entries"][0]
    assert run["computation"]["inputs"][0]["crate"] == "Same"


# -- D6: contentUrl paths ---------------------------------------------------

def test_D6_percent_encoded_content_url_is_decoded(tmp_path):
    (tmp_path / "my file.csv").write_text("x")
    crate = Crate({"@graph": [_descriptor("./"), {"@id": "./", "@type": "Dataset"}]},
                  path=str(tmp_path / "ro-crate-metadata.json"))
    assert crate.file_path({"contentUrl": "file:///my%20file.csv"}) == str(tmp_path / "my file.csv")


def test_D6_content_url_cannot_escape_the_crate(tmp_path):
    crate = Crate({"@graph": [_descriptor("./"), {"@id": "./", "@type": "Dataset"}]},
                  path=str(tmp_path / "c" / "ro-crate-metadata.json"))
    assert crate.file_path({"contentUrl": "../../etc/passwd"}) is None
    assert crate.file_path({"contentUrl": "file:///../outside.csv"}) is None


# -- E4: add-io counts every activity's consumption -------------------------

def test_E4_dataset_used_by_an_annotation_is_not_an_output():
    graph = [
        _descriptor("ark:1/root"),
        {"@id": "ark:1/root", "@type": ROCRATE, "name": "r"},
        {"@id": "ark:1/raw", "@type": DS, "name": "raw"},
        {"@id": "ark:1/ann", "@type": EVI + "Annotation", "name": "label",
         "usedDataset": [{"@id": "ark:1/raw"}]},
        {"@id": "ark:1/labels", "@type": DS, "name": "labels", "generatedBy": {"@id": "ark:1/ann"}},
    ]
    inputs, outs = outputs.calculate(graph)
    assert outs == [{"@id": "ark:1/labels"}]
    assert inputs == [{"@id": "ark:1/raw"}]


def test_E4_derived_only_dataset_is_not_an_input():
    graph = [
        _descriptor("./"), {"@id": "./", "@type": "Dataset"},
        {"@id": "a.csv", "@type": "prov:Entity", "name": "a"},
        {"@id": "b.csv", "@type": "prov:Entity", "name": "b", "prov:wasDerivedFrom": {"@id": "a.csv"}},
    ]
    inputs, outs = outputs.calculate(graph)
    assert inputs == [{"@id": "a.csv"}]
    assert outs == [{"@id": "b.csv"}]
