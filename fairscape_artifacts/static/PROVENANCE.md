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

## Caveat

`viewer.js` is minified with no source map and the TypeScript source is gone.
It can be used and re-inlined, but not meaningfully edited. Changing the viewer
means rewriting `web/` from scratch — treat that as a separate project, and
until then treat this file as a binary blob.
