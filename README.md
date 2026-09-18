# fairscape-artifacts

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
straight through the stub into the upstream chain. Nodes reached that way
carry `"crate": {"@id", "name"}` in the graph JSON; the datasheet lists the
linked crates in its overview. A missing upstream is a warning and the graph
ends at the stub.

Each HTML file is self-contained — styles and the graph viewer are inlined —
so it can be mailed, dropped into a Zenodo deposit, or served from GitHub
Pages with nothing beside it.

## Commands

| Command | What it does |
|---|---|
| `datasheet` | Renders the datasheet and a preview page per crate. `--no-review` omits the AI-Ready section, `--no-previews` skips the preview pages, `--link-base URL` turns identifiers into links. |
| `evidence-graph` | Renders the graph page plus a JSON sidecar. `--node` roots it elsewhere; `--condense-threshold` tunes fan-in collapsing; `--domain` walks the domain layer of a CPM-style crate (backbone connectors give way to their `prov:specializationOf` entities). |
| `review` | Runs the AI-Ready grader and writes its evidence presentation and review page. |
| `add-io` | Writes `EVI:inputs` / `EVI:outputs` onto the crate root. **The only command that modifies the crate.** |
| `all` | Graph, review and datasheet in one pass. |

Outputs land beside the crate unless `-d/--output-dir` says otherwise.

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
viewer reads. It is parity-tested against `fairscape_graph_tools`, and goes
beyond it on PROV crates.

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
(the `fairscape-wizard` distribution from the fairscape-grader repo), and this
package only calls it and lays the result out. That grader produces a
mechanical estimate where the scoring rules apply unambiguously and leaves the
rest for a human, so **no single total is synthesized**: the datasheet reports
how many criteria carry an estimate and how many await review, and a criterion
awaiting review is never shown as a zero.

Install the review extra to enable it:

```bash
pip install -e 'artifacts[review]'
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
* `outputs.calculate` is a port of the old `fairscape-cli` rule, which is
  being retired, so `tests/outputs-golden.json` — not the CLI — is what pins
  it. The port deliberately drops the CLI's `isPartOf` containment rule: in
  this corpus that rule fires against the crate root and deletes every output
  a crate has. `tests/test_outputs_parity.py` states the divergence exactly.

## Tests

```bash
python -m pytest artifacts/tests -q
```

Parity suites skip themselves when `fairscape_graph_tools` or `fairscape-cli`
are not importable.
