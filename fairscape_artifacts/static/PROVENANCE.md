# Recovered static assets

These three files were **recovered by extraction** from generated artifact pages
in `Planning/artifact/`, after the machine holding the `web/` viewer source was
lost. They are checked in as build outputs so `pip install` users never need a
node toolchain.

| File | Source block | Recovered from |
|---|---|---|
| `style.css` | 1st `<style>` | `evidence-graph-domain.html` (byte-identical in both pages) |
| `reactflow.css` | 2nd `<style>` | `reactflow/dist/style.css`, same in both pages |
| `viewer.js` | 2nd `<script>` | `evidence-graph-domain.html` |

`style.css` is the "Direction A — Technical Precision" theme; its own header
comment notes the palette mirrors the lost `web/theme.ts`.

## Why the domain build

The two recovered pages carry slightly different viewer bundles
(563,986 vs 564,274 chars; common prefix 370,225, common suffix 184,425).
The `-domain` build is a strict superset: identical occurrence counts for
`usedDataset`, `usedSoftware`, `usedSample`, `usedInstrument`, `usedMLModel`,
`hasOutputs`, `generatedBy` and `evi:annotatedBy`, **plus** 12 occurrences of
`derivedFrom`, which the older build does not traverse at all. Mount contract is
identical in both.

## Mount contract

`viewer.js` is a self-mounting Vite IIFE build exposing the global
`FairscapeEvidenceGraph`. On load it does:

```js
window.__FS_LINK_BASE__ === void 0 && (window.__FS_LINK_BASE__ = "");
const el = document.getElementById("evidence-graph");
el && window.__EVIDENCE_GRAPH__ && mount(el, window.__EVIDENCE_GRAPH__);
```

So a page only needs `<div id="evidence-graph">` plus
`window.__EVIDENCE_GRAPH__ = {...}` set before the bundle runs. It also exports
`FairscapeEvidenceGraph.mount(el, data)` for explicit mounting.

`__FS_LINK_BASE__` defaulting to `""` is what renders ARKs as plain text in
serverless files instead of links into a running server.

## Local additions

`style.css` carries a clearly-marked addendum at the end. Everything above
that marker is the recovered stylesheet byte-for-byte; anything below is
maintained here. CSS is editable in a way `viewer.js` is not, so fixes belong
in the addendum rather than in the recovered rules.

## What the bundle expects of the graph JSON

Read out of the minified source, because the data has to meet the viewer
rather than the other way round.

**Edges it resolves.** `getAllRelationships` enumerates exactly
`generatedBy`, `derivedFrom`, `usedDataset`, `usedSoftware`, `usedSample`,
`usedInstrument`, `usedMLModel`, `hasOutputs`, `createdBy`. A field outside
that list is not an edge: it is shown in the details panel and nothing more.
In particular `evi:representativeDataset` is **not** traversed, so a
`DatasetGroup` that carries only that is a dead end and everything upstream of
it is unreachable — which is why `evidence.EvidenceGraph._inherit_provenance`
copies the representative's own `generatedBy` / `derivedFrom` onto the group.

**Group expansion.** A `DatasetGroup` is expanded through `evi:memberIds`:

```js
y._childNodeIds = E.filter(A => typeof A == "string" && A.startsWith("ark:")
                                && a.getNode(A) !== null)
expandable: (y._childNodeIds?.length) > 0
```

so a member id counts only if it is a **string**, **starts with `ark:`**, and
**resolves to a node in `@graph`**. Clicking then reveals them ten at a time
with a `contains` edge, relabelling to *"90 Grouped Datasets (10 shown, click
for 10 more)"*. Two consequences:

- The members have to be in the graph. Condensation used to drop them, which
  left the group offering its representative alone — *"1 Grouped Datasets (all
  shown)"* under a node named "… (and 89 similar)".
  `evidence.EvidenceGraph._project_members` puts them back as leaves.
- The `ark:` prefix is hard-coded. A crate whose entities are identified by
  resolver URLs (`https://fairscape.net/api/ark:59853/…`, which is how the
  CM4AI release is published) fails that check, so its groups do not expand
  however correct the data is. The chain *through* the group still works,
  because that goes over an inherited edge and not over `memberIds`.

**Mount contract** is above; `#evidence-graph` plus `window.__EVIDENCE_GRAPH__`.
The initial view is `buildInitialGraph(2)`: the crate's outputs and two hops,
everything else on click.

## Caveat

`viewer.js` is minified with no source map and the TypeScript source is gone.
It can be used and re-inlined, but not meaningfully edited. Changing the viewer
means rewriting `web/` from scratch — treat that as a separate project, and
until then treat this file as a binary blob. The section above is the reason
that matters: several of its assumptions can only be met from the data side.

## `annotated_viewer.js` / `annotated_viewer.css`

Unlike the three files above, these have their source in the repo:
`web/annotated/` (see its README). They are a Vite IIFE build of the web
client's annotated-graph data layer plus this package's own node card,
drawer and mount shell. The interpretation page uses them; rebuild with
`npx vite build` in `web/annotated` after editing anything under `src/`.
