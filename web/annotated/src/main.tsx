// Mounts the annotated evidence graph on an offline page.
//
// Contract: a page provides <div id="annotated-graph"> and sets
// window.__ANNOTATED_GRAPH__ to an AnnotatedEvidenceGraph before this runs.
// window.FairscapeAnnotatedGraph.select(id) opens a node's pop-up and pans
// to it; highlight(id) is the older name for the same thing. reset() goes
// back to the first drawing.
import React, { useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import AnnotatedGraphViewer, { resetGraph } from "./vendor/AnnotatedGraph/AnnotatedGraphViewer";

type Listener = (id: string | null) => void;
const listeners = new Set<Listener>();

export function select(id: string | null): void {
  listeners.forEach((fn) => fn(id));
  if (id) document.getElementById("annotated-graph")?.scrollIntoView({ behavior: "smooth", block: "center" });
}

export const highlight = select;

export function reset(): void {
  resetGraph();
}

function isComputation(node: any): boolean {
  const types = Array.isArray(node?.["@type"]) ? node["@type"] : [node?.["@type"]];
  return types.some((t: any) => typeof t === "string" && t.includes("Computation"));
}

// Fallback depth for a graph without computations, drawn the web client's
// way from the outputs.
function depthFor(graph: Record<string, any>): number {
  const steps = Object.values(graph).filter(isComputation).length;
  return steps <= 15 ? 2 * steps + 3 : 3;
}

const OUTPUT_KEYS = ["evi:outputs", "EVI:outputs", "https://w3id.org/EVI#outputs"];

function refs(value: any): string[] {
  const list = Array.isArray(value) ? value : value == null ? [] : [value];
  return list
    .map((v: any) => (typeof v === "string" ? v : v?.["@id"]))
    .filter((v: any): v is string => typeof v === "string" && !!v);
}

function isStep(node: any): boolean {
  return isComputation(node) && !String(node["@type"]).includes("Annotated");
}

// Where the graph ends. The web client reads `evi:outputs` on the root;
// crates written by other tools spell it differently or leave it out, and
// its own fallback (the last Dataset in hasPart) can land on a config file.
// Last resort: every dataset some computation made and none consumed.
function outputsFor(data: any, graph: Record<string, any>): string[] {
  const root = graph[data?.["evi:annotates"]?.["@id"]] || {};
  for (const key of OUTPUT_KEYS) {
    const ids = refs(root[key]).filter((id) => id in graph);
    if (ids.length) return ids;
  }
  const used = new Set<string>();
  const made = new Set<string>();
  for (const node of Object.values(graph)) {
    if (!isStep(node)) continue;
    refs(node.usedDataset).forEach((id) => used.add(id));
    refs(node.generated).forEach((id) => made.add(id));
  }
  return [...made].filter((id) => !used.has(id) && id in graph);
}

// A workflow-run computation (Nextflow's, say) lists every stage's output
// as its own, so drawn as-is it wires into every step. On this page's copy
// of the graph such an umbrella is left out of the drawing; the steps it
// wraps stay the producers of everything. Its annotation is still on the
// page, in the step list. The document itself is untouched.
function withoutUmbrellas(graph: Record<string, any>): Record<string, any> {
  const producers = new Map<string, Set<string>>();
  for (const [id, node] of Object.entries(graph)) {
    if (!isStep(node)) continue;
    refs(node.generated).forEach((out) => {
      if (!producers.has(out)) producers.set(out, new Set());
      producers.get(out)!.add(id);
    });
  }
  for (const [id, node] of Object.entries(graph)) {
    refs(node.generatedBy).forEach((comp) => {
      if (!producers.has(id)) producers.set(id, new Set());
      producers.get(id)!.add(comp);
    });
  }
  const umbrellas = new Set<string>();
  for (const id of Object.keys(graph)) {
    if (!isStep(graph[id])) continue;
    const partners = new Set<string>();
    for (const comps of producers.values()) {
      if (comps.size > 1 && comps.has(id)) comps.forEach((c) => c !== id && partners.add(c));
    }
    if (partners.size >= 2) umbrellas.add(id);
  }
  if (!umbrellas.size) return graph;

  const out: Record<string, any> = {};
  for (const [id, node] of Object.entries(graph)) {
    if (umbrellas.has(id)) continue;
    const kept = refs(node.generatedBy).filter((c) => !umbrellas.has(c));
    out[id] = kept.length === refs(node.generatedBy).length ? node : { ...node, generatedBy: kept.map((c) => ({ "@id": c })) };
  }
  return out;
}

function App({ data }: { data: any }) {
  const [selected, setSelected] = useState<string | null>(null);
  useEffect(() => {
    listeners.add(setSelected);
    return () => void listeners.delete(setSelected);
  }, []);
  const view = useMemo(() => {
    const graph = withoutUmbrellas(data["@graph"] || {});
    const finals = outputsFor(data, graph);
    const spine = Object.values(graph).some(isStep);
    return {
      graphData: { "@graph": graph, outputs: finals.map((id) => ({ "@id": id })) },
      finals,
      spine,
      depth: depthFor(graph),
    };
  }, [data]);
  return (
    <AnnotatedGraphViewer
      graphData={view.graphData}
      selectedId={selected}
      onSelect={setSelected}
      finals={view.finals}
      spine={view.spine}
      initialDepth={view.depth}
      deferSoftware
    />
  );
}

export function mount(el: HTMLElement, data: any): void {
  createRoot(el).render(<App data={data} />);
}

const el = document.getElementById("annotated-graph");
const payload = (window as any).__ANNOTATED_GRAPH__;
if (el && payload) mount(el, payload);
