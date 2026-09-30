---
name: agentic-interpret
description: Interpret an RO-Crate's provenance with the host agent as the LLM — no API key. `fairscape-artifacts interpret --agentic prepare` writes one packet per computation, parallel subagents write each step's annotation.json, `--agentic steps` validates them and writes the synthesis packet, one more pass writes synthesis.json, and `--agentic assemble` builds ro-crate-interpretation.json/.html (the same AnnotatedEvidenceGraph the web client's annotated graph viewer reads).
---

# Agentic interpretation — the host agent as the model

`fairscape-artifacts interpret` normally has `fairscape_graph_tools` call a
model through pydantic-ai. `--agentic` runs the same engine code for
condensing, prompt building, validation and AEG assembly, and cuts out only
the two model calls: one per computation, and one graph-level synthesis.
You and your subagents answer those instead.

Tell the user this up front, in a sentence or two: it costs no API key, runs
one subagent per computation plus one synthesis pass, and produces the same
`ro-crate-interpretation.json` + `.html` that the API-key path writes.

## Preconditions

- The `fairscape` conda env: `/home/oj/anaconda3/envs/fairscape/bin` first on
  PATH (it has `fairscape-artifacts` with the `interpret` extra).
- A crate directory or `ro-crate-metadata.json` with at least one Computation.
- Pick `-d OUT` if the crate folder must stay untouched; otherwise outputs
  land beside the crate. The work folder defaults to
  `OUT/interpretation-work`; pass `--work-dir` to move it.
- Record which model the answers come from: `--model-label agentic:<model-id>`
  on `prepare` (it is fixed there and stamped on every step and the AEG).

## 1. Prepare (deterministic)

```bash
fairscape-artifacts interpret CRATE -d OUT --agentic prepare \
    --model-label agentic:claude-opus-5-5 [--reference OTHER_CRATE ...]
```

Prints one `steps/NN/packet.md` per computation. Check
`interpretation-work/state.json` → `missing_source`: software with no source
found. Tell the user; those steps will be judged on metadata alone.

Each packet has three parts: **Instructions** (the engine's datasci system
prompt), **Input** (the exact user prompt the engine would send) and
**Answer** (the output path and the `LLMComputationAnnotation` JSON schema).

## 2. Annotate every step (parallel subagents, one message)

Dispatch one `general-purpose` subagent per step directory, all in a single
message. Each prompt, with `NN` and paths filled in:

> You are the LLM in an interpretation pipeline for a scientific provenance
> graph (RO-Crate). Read `<work>/steps/NN/packet.md` in full and follow its
> Instructions exactly.
> - Base the annotation on the packet. The inlined source may be repo
>   boilerplate from a GitHub fetch; you MAY read local source files the
>   packet names (paths in **Command**, a local checkout matching a GitHub
>   Content URL, the workflow file and its config) to cite real functions,
>   defaults and line numbers. Nothing else: no other crates, no result data.
> - Only the exact `@id`s in the packet for software_id, dataset_id and
>   evidence artifacts; line or function in evidence `location`.
> - One codeAnalysis per Software entry, one summary per listed input and
>   output.
> - Don't invent data statistics.
> - Write ONLY the JSON object to `<work>/steps/NN/annotation.json`, check it
>   parses, and modify nothing else. Reply with one line: status and counts.

Local-source reading is the one deliberate departure from the API-key path.
It makes code analysis much better when the inlined source is a repo dump.
Mention it to the user.

## 3. Validate and build the synthesis packet

```bash
fairscape-artifacts interpret CRATE -d OUT --agentic steps
```

On failure it lists every step to fix: missing file, schema error, or a
software/input/output the answer skipped (the same gaps the engine re-prompts
for). Send each failure back to that step's subagent with the error text and
rerun until it passes. It then writes `synthesis/packet.md`.

## 4. Synthesis (one pass)

Do it yourself, or give it to one subagent. Read `synthesis/packet.md`,
follow its Instructions, and write `synthesis/synthesis.json` matching
`GraphSynthesisResult` (executiveSummary, narrativeSummary, pipelineSteps,
keyFindings, assumptions, pipelineDescription). Keep it to what the step
annotations say; don't pull in outside knowledge of the project.

## 5. Assemble and render

```bash
fairscape-artifacts interpret CRATE -d OUT --agentic assemble
```

Writes `OUT/ro-crate-interpretation.json` (the AnnotatedEvidenceGraph, built
by the engine's `build_aeg`) and `OUT/ro-crate-interpretation.html`. A later
`fairscape-artifacts all` or `datasheet` links the interpretation when it sits
beside the crate. `--render-only` rebuilds the page from the JSON.

## Report back

Steps annotated, how many flagged `review_recommended` / `error_detected`,
CRITICAL assumptions, any `missing_source`, and the paths written. Don't
paste the narrative into chat; point at the page.
