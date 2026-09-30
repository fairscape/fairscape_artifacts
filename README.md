# fairscape-artifacts

Turns a finished RO-Crate into pages people can read: an HTML datasheet, an
interactive evidence graph, a preview of every entity, and an AI-Ready review.
It runs offline on a crate folder. You don't need a server.

It is the **view** step of [FAIRSCAPE](https://fairscape.github.io). Run it
once your crate is built with
[fairscape_models](https://github.com/fairscape/fairscape_models) or
[fairscape_conversion](https://github.com/fairscape/fairscape_conversion).

## Install

```bash
pip install git+https://github.com/fairscape/fairscape_artifacts
```

## Example

```bash
fairscape-artifacts all path/to/crate
```

```
path/to/crate/
  ro-crate-datasheet.html         the datasheet
  ro-crate-preview.html           every entity in the crate, grouped by kind
  ro-crate-evidence-graph.html    interactive provenance graph
  ro-crate-evidence-graph.json    the same graph as data
```

Each HTML file is self-contained, so you can email it, put it in a Zenodo
deposit or serve it from GitHub Pages.

## Commands

| Command | What it does |
|---|---|
| `all` | Builds the evidence graph, the review and the datasheet in one pass. |
| `datasheet` | Builds the datasheet and preview pages. |
| `evidence-graph` | Builds the graph page and its JSON. |
| `review` | Runs the AI-Ready grader and writes the review page. |
| `add-io` | Writes `EVI:inputs` / `EVI:outputs` onto the crate root. Changes the crate. |
| `link-inverses` | Adds the other half of every EVI link: a computation's `generated` for a dataset's `generatedBy`, and so on. Changes the crate. |
| `interpret` | Has an LLM read the crate's provenance and code, then writes an annotated summary. `all` never runs it. |

Outputs are written next to the crate unless you pass `-d DIR`.

## Details

- **AI-Ready review.** Scores come from the
  [AIreadiness-grader](https://github.com/fairscape/AIreadiness-grader).
  Install it with `pip install "fairscape-artifacts[review]"`. Without it, every other
  command still works and the datasheet leaves the review section out.
- **LLM interpretation.** Install `pip install "fairscape-artifacts[interpret]"`
  and run
  `fairscape-artifacts interpret CRATE --model anthropic:claude-haiku-4-5-20251001 --api-key $ANTHROPIC_API_KEY`.
- **Releases and linked crates.** A release crate gets one card per
  sub-crate. A crate linked to an upstream crate gets one evidence graph that
  spans both. Use `--reference OTHER_CRATE` to look up ids in a related crate
  that this one doesn't point to.
- **Tests.** `python -m pytest tests -q`.

How the graph is built, what the datasheet contains and how PROV-only crates
are handled: [`docs/DETAILS.md`](docs/DETAILS.md).
