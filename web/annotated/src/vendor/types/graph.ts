import { Node, Edge } from "reactflow";

export interface RawGraphEntity {
  "@id": string;
  "@type": string | string[];
  name?: string;
  label?: string;
  description?: string;
  generatedBy?: { "@id": string } | Array<{ "@id": string }>;
  usedDataset?: { "@id": string } | Array<{ "@id": string }>;
  usedSoftware?: { "@id": string } | Array<{ "@id": string }>;
  usedSample?: { "@id": string } | Array<{ "@id": string }>;
  usedInstrument?: { "@id": string } | Array<{ "@id": string }>;
  usedMLModel?: { "@id": string } | Array<{ "@id": string }>;
  hasOutputs?: { "@id": string } | Array<{ "@id": string }>;
  createdBy?: string | { "@id": string } | Array<string | { "@id": string }>;
  "evi:annotatedBy"?: Array<{ "@id": string }>;
  [key: string]: any;
}

export interface RawGraphData {
  "@graph": { [arkId: string]: RawGraphEntity };
  outputs?: Array<{ "@id": string }>;
  [key: string]: any;
}

export type AssumptionImpact = "CRITICAL" | "MAJOR" | "MINOR";
export type ComputationReviewStatus =
  "clear" | "review_recommended" | "error_detected";

export interface ComputationError {
  description: string;
  severity: "CRITICAL" | "MAJOR";
  evidence?: EvidencePointer;
  affectedOutputs?: string;
}

/** Normalize legacy impact values (FOUNDATIONAL, SIGNIFICANT, INCIDENTAL, CONSEQUENTIAL) to current names. */
export function normalizeImpact(raw: string): AssumptionImpact {
  switch (raw) {
    case "CRITICAL":
      return "CRITICAL";
    case "MAJOR":
      return "MAJOR";
    case "MINOR":
      return "MINOR";
    // Legacy names
    case "FOUNDATIONAL":
      return "CRITICAL";
    case "SIGNIFICANT":
      return "MAJOR";
    case "INCIDENTAL":
      return "MINOR";
    case "CONSEQUENTIAL":
      return "MAJOR";
    case "MODERATE":
      return "MAJOR";
    default:
      return "MINOR";
  }
}

export interface EvidencePointer {
  artifact: { "@id": string };
  location?: string;
}

export interface Assumption {
  impact: AssumptionImpact;
  name?: string;
  description: string;
  downstreamImpacts?: string;
  evidence?: EvidencePointer;
  reviewRecommended?: boolean;
  recommendedValidation?: string;
}

export interface GraphAssumption {
  impact: AssumptionImpact;
  name?: string;
  description: string;
  downstreamImpacts?: string;
  evidence?: EvidencePointer;
  reviewRecommended?: boolean;
  recommendedValidation?: string;
  sourceAnnotation: { "@id": string };
}

// Backward compat aliases for old data with evi:concerns
export type ConcernLevel = "CRITICAL" | "MODERATE" | "MINOR";

export interface Concern {
  level: ConcernLevel;
  description: string;
}

export interface GraphConcern {
  level: ConcernLevel;
  description: string;
  sourceAnnotation: { "@id": string };
}

export interface DataOverview {
  dataDescription: string;
  pipelineDescription?: string;
  pipelineSteps?: string[];
  dataFormats: string[];
  keywords: string[];
  license?: string;
  conditionsOfAccess?: string;
  topAssumptions: GraphAssumption[];
}

export interface AudiencePerspective {
  targetAudience: string;
  audienceLabel: string;
  executiveSummary: string;
  narrativeSummary: string;
  keyFindings?: string[];
  assumptions?: GraphAssumption[];
}

export interface AnnotationData {
  "@id": string;
  "evi:stepSummary": string;
  "evi:codeAnalysis"?: Array<{
    software: { "@id": string };
    name?: string;
    summary: string;
    keyFunctions?: string[];
    assumptions?: Assumption[];
    // Backward compat
    concerns?: Concern[];
  }>;
  "evi:inputSummaries"?: Array<{
    dataset: { "@id": string };
    name?: string;
    role?: string;
    description?: string;
    dataQuality?: string;
  }>;
  "evi:outputSummaries"?: Array<{
    dataset: { "@id": string };
    name?: string;
    role?: string;
    description?: string;
    dataQuality?: string;
  }>;
  "evi:assumptions"?: Assumption[];
  "evi:errors"?: ComputationError[];
  "evi:computationStatus"?: ComputationReviewStatus;
  // Backward compat
  "evi:concerns"?: Concern[];
  "evi:llmModel": string;
  "evi:llmTemperature"?: number;
  dateCreated: string;
  "evi:interpreterVersion"?: string;
}

export interface EvidenceNodeData {
  id: string;
  type: string;
  label: string;
  displayName: string;
  description?: string;
  expandable: boolean;
  properties: Record<string, any> & {
    count?: number;
    _childNodeIds?: string[];
    _parentNodeId?: string;
    _visibleChildren?: number;
  };
  _sourceData: RawGraphEntity | {};
  _expanded?: boolean;
  _annotation?: AnnotationData;
}

export type EvidenceNode = Node<EvidenceNodeData>;
export type EvidenceEdge = Edge;

export interface AnnotatedEvidenceGraphData {
  "@id": string;
  "@type": string | string[];
  name: string;
  description: string;
  "@graph": { [arkId: string]: RawGraphEntity };
  "evi:annotates": { "@id": string };
  "evi:executiveSummary": string;
  "evi:narrativeSummary": string;
  "evi:keyFindings"?: string[];
  "evi:assumptions"?: GraphAssumption[];
  "evi:overview"?: DataOverview;
  "evi:audiences"?: AudiencePerspective[];
  // Backward compat
  "evi:concerns"?: GraphConcern[];
  "evi:stepAnnotations"?: Array<{ "@id": string }>;
  "evi:llmModel": string;
  dateCreated: string;
  [key: string]: any;
}
