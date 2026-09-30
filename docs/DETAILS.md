# fairscape-artifacts: details

Reference notes behind the [README](../README.md).


One command turns a local RO-Crate into the things people actually look at:
an HTML datasheet, an interactive evidence graph, an AI-Ready review, and
derived input/output links.

Everything runs offline against a crate on disk. No server, no database, and
`jinja2` is the only required dependency.

```bash
fairscape-artifacts all path/to/crate
```

```
path/to/crate/
  ro-crate-datasheet.html         datasheet, with the review inline
  ro-crate-preview.html           every entity in the crate, tabulated by kind
  ro-crate-evidence-graph.html    interactive provenance graph
  ro-crate-evidence-graph.json    the graph as data
  ai-ready-presentation.json      per-criterion evidence
  ai-ready-review.html            the grader's human-review page
```

A release crate — one whose graph lists constituent crates by the path of
their `ro-crate-metadata.json` — gets one composition card per constituent
and a `ro-crate-preview.html` inside each constituent's directory.

A crate that *points at* another one — a node with that same
`ro-crate-metadata` field that the root does not list in `hasPart`, the way
`fairscape_conversion`'s linked-crates pass writes it when an input of this
crate was an output of that one — is not a release. It renders as itself, the
linked crate is loaded and layered under it, and the evidence graph walks
straight through the stub into the upstream chain. A missing upstream is a
warning and the graph ends at the stub.

### References that leave the crate

A reference in a crate is a bare `@id`. Nothing in it says which crate holds
the entity, so an entity described next door reads as a dangling id and the
provenance chain stops there. The evidence graph therefore resolves ids
against more than one crate, in this order:

1. **this crate**, always first;
2. **every crate it points at** — constituents and linked crates, and theirs.
   Their own copy of an entity wins over a stub of it here, because the stub
   deliberately carries no provenance and the copy does;
3. **`--reference` crates** (`pool=` to `evidence.build`), for a crate that is
   related but not pointed at. Consulted last and only for ids still
   unresolved, so a wide pool fills gaps and never overrules a crate that was
   actually named.

Sibling constituents of a release are what needs (3): in the CM4AI September
release the perturb-seq cell-atlas crate's processing computation consumes 90
datasets described only in the SRA crate beside it, and neither crate mentions
the other. Without the sibling its graph is 97 nodes of which 90 are dangling;
with it, 17 nodes and none — the 90 inputs condense into one `DatasetGroup`.

Nodes reached outside this crate carry `"crate": {"@id", "name"}` in the graph
JSON, and the graph page names the crates it had to reach into under the
header. The datasheet lists linked crates in its overview.

Each HTML file is self-contained — styles and the graph viewer are inlined —
so it can be mailed, dropped into a Zenodo deposit, or served from GitHub
Pages with nothing beside it.

## Commands

| Command | What it does |
|---|---|
| `datasheet` | Renders the datasheet and a preview page per crate. `--no-review` omits the AI-Ready section, `--no-previews` skips the preview pages, `--link-base URL` turns identifiers into links. |
| `evidence-graph` | Renders the graph page plus a JSON sidecar. `--node` roots it elsewhere; `--condense-threshold` tunes fan-in collapsing; `--reference` adds a crate to resolve ids against; `--domain` walks the domain layer of a CPM-style crate (backbone connectors give way to their `prov:specializationOf` entities). |
| `review` | Runs the AI-Ready grader and writes its evidence presentation and review page. |
| `add-io` | Writes `EVI:inputs` / `EVI:outputs` onto the crate root. **Modifies the crate.** |
| `link-inverses` | Adds the missing inverse of every EVI provenance link (`generated` for `generatedBy`, `datasetUsedBy` for `usedDataset`, …), so the crate reads the same walked in either direction. Existing values are kept, nothing is removed. **Modifies the crate.** |
| `interpret` | Has an LLM read the crate's provenance and code and writes the annotated evidence graph plus its page. Never part of `all`; see below. |
| `all` | Graph, review and datasheet in one pass. |

Outputs land beside the crate unless `-d/--output-dir` says otherwise.

## AI interpretation

```bash
pip install "fairscape-artifacts[interpret]"
fairscape-artifacts interpret path/to/crate --model anthropic:claude-haiku-4-5-20251001 --api-key $ANTHROPIC_API_KEY
```

```
path/to/crate/
  ro-crate-interpretation.json    the AnnotatedEvidenceGraph
  ro-crate-interpretation.html    executive and narrative summary, key
                                  findings, ranked assumptions, one entry per
                                  computation, audience perspectives
```

The engine is `fairscape-graph-tools`, the same package the FAIRSCAPE server
runs: it condenses the provenance graph, has the model annotate every
computation from its code, inputs and outputs, then synthesizes the whole.
This package only supplies the engine's four ports over a crate on disk
(`fairscape_artifacts.interpret`) and renders what comes back
(`fairscape_artifacts.interpretation`). Nothing about what an interpretation
says is decided here.

`--model` takes a pydantic-ai `provider:name` string and `--api-key` is put
in that provider's environment variable, exactly as `fairscape-grade` does.
Constituent and linked crates are read automatically; `--reference` adds
any other crate to resolve ids against. `--save-condensed` keeps the
condensed crate, `--debug-llm` traces every raw model response, and
`--render-only` rebuilds the page from an existing JSON without a model.

Interpretation costs one call per computation plus a few for synthesis and
can take minutes, so `all` never runs it. `all` and `datasheet` link to an
interpretation that is already beside the crate.

## The datasheet

The page follows the FAIRSCAPE datasheet layout, with a docked table of
contents on wide screens:

* **Datasheet Summary** — the description, statistics tiles (size, counts by
  kind, formats), an *Access data* button, and the AI-Ready review at a
  glance: a donut of criteria by outcome and one bar per rubric section.
* **Release Overview** — identifier, DOI, dates, authors, publisher, contact,
  governance, copyright, licence, terms of use, confidentiality, keywords,
  citation, funding, completeness, publications, plus the human-subjects and
  regulatory block. Absent fields say *Not specified* rather than assuming a
  reassuring default.
* **AI Ready Details** — the `rai:` characterization fields (intended uses,
  limitations, prohibited uses, bias, maintenance, collection, ...).
* **AI-Ready Review** — every criterion with its estimate and basis.
* **Composition** — for a release, a filterable "datasets at a glance" index
  and one collapsible card per constituent crate: its metadata, a content
  summary (files by format and access, ML models, software and instruments, inputs by
  origin, experiments and computations as `inputs -> outputs` patterns), and
  links to its provenance graph, QC report and preview page. A crate with no
  constituents gets a single card for itself.
* **Distribution Information**.

Sub-crate statistics are computed from each constituent's own metadata; a
release root that carries `evi:*` counters is trusted for the summary tiles.

## What is and is not computed here

The **evidence graph** is: BFS backwards from the crate's outputs, condensation
of sibling fan-in into `DatasetGroup` nodes, then projection into the JSON the
viewer reads. The node index it walks spans the crates above; a node a group
replaced is reported as that group wherever something still names it, rather
than as a node that could not be found. It is parity-tested against
`fairscape_graph_tools`, and goes beyond it on PROV crates.

A **DatasetGroup** is a summary the reader can open, so it carries two things
the viewer needs — see `static/PROVENANCE.md` for the bundle's own rules:

* its **members**, as leaf nodes. The viewer expands a group through
  `evi:memberIds` and only offers the control when those ids resolve in the
  graph, so dropping them (as condensation did, and as the upstream engine
  still does) left a node named "… (and 89 similar)" that opened to *"1
  Grouped Datasets (all shown)"*. They come back without provenance of their
  own: the producers only they reached were collapsed with them.
* the **representative's upstream edge**, copied onto the group.
  `evi:representativeDataset` is not an edge the viewer resolves, so without
  it the provenance chain stopped at the group — on the CM4AI cell-atlas
  crate, everything from the representative's `cellranger count` back to the
  BioSample and the sequencer was in the JSON and unreachable.

How big the group's member list is bounds how big this makes the graph;
`condense.condense_cache(max_member_ids=…)` truncates it, at the cost of the
members past the cut no longer being expandable.

**PROV-only crates** — no EVI vocabulary anywhere — are supported end to end.
Every EVI reading is tried first and PROV is the fallback, so a hybrid crate
behaves exactly as it always did:

| | EVI | PROV fallback |
|---|---|---|
| entity / activity | `EVI#Dataset`, `EVI#Computation` | `prov:Entity`, `prov:Activity` |
| generation | `generatedBy` | `prov:wasGeneratedBy` |
| derivation (only when nothing generated it) | `derivedFrom` | `prov:wasDerivedFrom` |
| consumption | `usedDataset` | `prov:used`, projected as `usedDataset` |

Each PROV term is read prefixed (`prov:used`), bare (`used`) or as a full IRI
(`http://www.w3.org/ns/prov#used`). The crate root is identified by the
metadata descriptor's `about`, not by an `EVI#ROCrate` type, so a PROV-only
crate's plain `Dataset` root still starts the walk at what the crate produced
instead of rendering as one lonely node — and is never listed among its own
outputs.

The **AI-Ready review** is not. The rubric — *Rubric for Human Review of
AI-readiness Evaluation Criteria* v1.5 — lives in `aireadiness_evidence`
(the `aireadiness-grader` package, installed by the `review` extra), and this
package only calls it and lays the result out. That grader produces a
mechanical estimate where the scoring rules apply unambiguously and leaves the
rest for a human, so **no single total is synthesized**: the datasheet reports
how many criteria carry an estimate and how many await review, and a criterion
awaiting review is never shown as a zero.

Install the review extra to enable it:

```bash
pip install "fairscape-artifacts[review]"
```

Without it, every other command works and the datasheet omits the section.

## Notes

* `static/viewer.js` is a checked-in build of the React Flow viewer, recovered
  by extraction rather than compiled here. It can be re-inlined but not
  edited — see `fairscape_artifacts/static/PROVENANCE.md` before touching it.
  `static/style.css` is recovered too; datasheet and preview styling lives in
  `static/datasheet.css`, which is ours to edit.
* Python computes plain data (`datasheet.py`, `composition.py`, `preview.py`)
  and the templates hold every angle bracket; crate-authored text is always
  autoescaped.
* Network access is off by default. Pass `--network` to let the grader resolve
  URLs and registries.
* Building an evidence graph never modifies the crate; if a crate has no
  declared outputs they are derived in memory. Use `add-io` to persist them.
* No builder here needs the inverse EVI links; they are read from the declared
  side. `link-inverses` exists for consumers that walk the graph the other
  way, and replaces the `augment link-inverses` step the workflow reporters
  used to get from `fairscape-cli`. The `owl:inverseOf` pairs are pinned in
  `inverses.py`; `tests/test_inverses.py` re-derives them from the EVI
  ontology when rdflib and the CLI checkout are available.
* `outputs.calculate` is pinned by `tests/outputs-golden.json`. It
  deliberately drops the `fairscape-cli` `isPartOf` containment rule: in
  this corpus that rule fires against the crate root and deletes every output
  a crate has. `tests/test_outputs_parity.py` states the divergence exactly.

## Tests

```bash
python -m pytest tests -q
```

Parity suites skip themselves when `fairscape_graph_tools` or `fairscape-cli`
are not importable.
