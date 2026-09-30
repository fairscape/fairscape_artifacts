# Annotated graph viewer (source for `static/annotated_viewer.{js,css}`)

The interpretation page (`ro-crate-interpretation.html`) leads with the
annotated evidence graph from the FAIRSCAPE web client. This folder builds it
as one self-mounting IIFE so the page stays a single offline file.

```bash
cd web/annotated
npm install
npx vite build        # writes ../../fairscape_artifacts/static/annotated_viewer.js/.css
```

Commit the rebuilt `static/annotated_viewer.*`; `pip install` users never
need node.

## What is vendored

`src/vendor/` is copied from `fairscape_web_client` (commit e4ce77f,
2026-09-02): `components/AnnotatedGraph/{AnnotatedGraphViewer,
AnnotatedEvidenceNode, AssumptionChainModal}.tsx`, `GraphDataService.ts`,
`graphUtils.ts`, `components/EvidenceGraph/utils/layoutUtils.ts`,
`components/shared/nodeColors.ts`, `types/graph.ts`.

Local changes, each marked `fairscape-artifacts` in the code:

- `/view/<id>` links go through `links.ts`, so they point at
  `window.__FS_LINK_BASE__` when set and are plain text otherwise.
- `AnnotatedGraphViewer` is rewritten for the page: it draws the *step
  spine* (`GraphBuilder.buildStepSpine`: every computation plus the final
  outputs, the datasets between two steps folded into one labelled edge),
  sizes its frame to the drawing, lays out producers-left with dagre's
  `RL`, and holds the selection. A click selects a node and, if it has
  more to show, unfolds its datasets and software; a dataset that appears
  takes its own edges back from the folded one.
- The web client's `AnnotatedEvidenceNode` (tooltip + modal) is replaced
  by `src/StepNode.tsx` (the card: kind, name, one-line headline, status
  dot) and `src/Drawer.tsx` (the pop-up over the graph: gist, reads /
  makes / runs, assumptions as rows, the full analysis behind a toggle).
  A "Reset graph" button in the frame redraws the spine.
  `AssumptionChainModal` is kept and opened from a dataset's drawer.
- `src/text.ts` holds the shared wording: status labels and colours (the
  page palette, not the client's purple), `humanName`, sentence helpers.
- The dagre spacing is tighter so a whole pipeline fits the page.

The page can hand the viewer two page-only keys on an annotation node,
`_headline` and `_number`; `interpretation.summarize` derives them. Without
them the card falls back to the step summary's first sentence.

## Mount contract

`src/main.tsx`: the page provides `<div id="annotated-graph">` and sets
`window.__ANNOTATED_GRAPH__` to an AnnotatedEvidenceGraph. The graph starts
from the root's outputs (`evi:outputs`, `EVI:outputs` or the full IRI). If none
are set, it starts from datasets some computation made and none used. A
workflow-run computation that claims every stage's outputs is left out of
the drawing (its annotation stays on the page). `window.FairscapeAnnotatedGraph.select(id)`
opens a node's pop-up and pans to it; `highlight(id)` is the older name
for the same call; `reset()` redraws the first drawing.
