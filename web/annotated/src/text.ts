// Small text helpers shared by the node cards and the detail drawer.
import {
  AnnotationData,
  Assumption,
  ComputationReviewStatus,
  normalizeImpact,
} from "./vendor/types/graph";

export const STATUS: Record<
  ComputationReviewStatus,
  { label: string; color: string; tint: string }
> = {
  clear: { label: "Clear", color: "#40916c", tint: "#e9f5ee" },
  review_recommended: { label: "Worth a closer look", color: "#b27300", tint: "#fdf3e1" },
  error_detected: { label: "Error found", color: "#d00000", tint: "#fdecea" },
};

export const IMPACT: Record<string, { label: string; color: string; tint: string }> = {
  CRITICAL: { label: "Critical", color: "#d00000", tint: "#fdecea" },
  MAJOR: { label: "Major", color: "#b27300", tint: "#fdf3e1" },
  MINOR: { label: "Minor", color: "#84939a", tint: "#f1f4f5" },
};

export const KIND_COLORS: Record<string, string> = {
  step: "#3b82c4",
  result: "#005f73",
  data: "#4fa86a",
  software: "#d39a00",
  files: "#7fa8c9",
  other: "#84939a",
};

export function statusOf(annotation?: AnnotationData | null) {
  const key = (annotation?.["evi:computationStatus"] || "clear") as ComputationReviewStatus;
  return { key, ...(STATUS[key] || STATUS.clear) };
}

export function firstSentences(text: string | undefined, n: number): string {
  const parts = (text || "")
    .trim()
    .split(/(?<=[.!?])\s+(?=[A-Z0-9"'(])/)
    .filter(Boolean);
  return parts.slice(0, n).join(" ");
}

export function clip(text: string | undefined, limit: number): string {
  const t = (text || "").replace(/\s+/g, " ").trim();
  if (t.length <= limit) return t;
  const cut = t.slice(0, limit).replace(/\s+\S*$/, "").replace(/[,;:]$/, "");
  return cut + "…";
}

// "IMAGE_EMBEDDING (U2OS)" reads as "Image Embedding (U2OS)". Short
// all-caps tokens are left alone: they are usually acronyms.
export function humanName(name: string | undefined): string {
  if (!name) return "";
  return name
    .replace(/_/g, " ")
    .split(" ")
    .map((tok) =>
      /^[A-Z][A-Z0-9]{4,}$/.test(tok) ? tok[0] + tok.slice(1).toLowerCase() : tok,
    )
    .join(" ");
}

export function headlineOf(node: any, annotation?: AnnotationData | null): string {
  const given = annotation?.["_headline"] || node?.["_headline"];
  if (given) return String(given);
  return clip(firstSentences(annotation?.["evi:stepSummary"], 1), 110);
}

export interface ShownAssumption {
  impact: string;
  name?: string;
  description: string;
  downstreamImpacts?: string;
  recommendedValidation?: string;
  reviewRecommended?: boolean;
  evidence?: { artifact?: { "@id": string }; location?: string };
}

function normalizeAll(list: any[] | undefined, concerns: any[] | undefined): ShownAssumption[] {
  if (list && list.length) {
    return list.map((a: Assumption) => ({ ...a, impact: normalizeImpact(a.impact) }));
  }
  if (concerns && concerns.length) {
    return concerns.map((c: any) => ({ impact: normalizeImpact(c.level), description: c.description }));
  }
  return [];
}

export function stepAssumptions(annotation?: AnnotationData | null): ShownAssumption[] {
  if (!annotation) return [];
  return normalizeAll(annotation["evi:assumptions"], annotation["evi:concerns"]);
}

export function codeAssumptions(ca: { assumptions?: any[]; concerns?: any[] }): ShownAssumption[] {
  return normalizeAll(ca.assumptions, ca.concerns);
}

const IMPACT_ORDER = ["CRITICAL", "MAJOR", "MINOR"];

export function byImpact(list: ShownAssumption[]): ShownAssumption[] {
  return [...list].sort(
    (a, b) => IMPACT_ORDER.indexOf(a.impact) - IMPACT_ORDER.indexOf(b.impact),
  );
}

export function refIds(value: any): string[] {
  const list = Array.isArray(value) ? value : value == null ? [] : [value];
  return list
    .map((v: any) => (typeof v === "string" ? v : v?.["@id"]))
    .filter((v: any): v is string => typeof v === "string" && !!v);
}

export function typeOf(node: any): string {
  const types = Array.isArray(node?.["@type"]) ? node["@type"] : [node?.["@type"]];
  return types
    .filter((t: any) => typeof t === "string")
    .map((t: string) => t.split(/[#/]/).pop() || "")
    .join(" ");
}
