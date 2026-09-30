// src/components/shared/nodeColors.ts
//
// Recovered from ghcr.io/fairscape/fairscapefrontend:RELEASE.2026-08-04.v2.
// Replaces the duplicated getNodeColor switch statements that previously lived
// in EvidenceNode.tsx and AnnotatedEvidenceNode.tsx. Note the reconciliation:
// Computation is the blue #7EB6E6 (from the annotated graph), while Experiment
// keeps the red #FD9A9A it had in the evidence graph.
export const NODE_COLORS: Record<string, string> = {
  Dataset: "#8AE68A",
  Sample: "#8AE68A",
  ROCrate: "#64C2A6",
  Computation: "#7EB6E6",
  Experiment: "#FD9A9A",
  Software: "#FFC107",
  Instrument: "#FFC107",
  MLModel: "#C8A2FF",
  Annotation: "#FFA07A",
  AnnotatedComputation: "#FFA07A",
  DatasetCollection: "#B5DEFF",
  DatasetGroup: "#B5DEFF",
  Person: "#87CEEB",
};

export const DEFAULT_NODE_COLOR = "#E0E0E0";

export const getNodeColor = (type: string): string =>
  NODE_COLORS[type] || DEFAULT_NODE_COLOR;
