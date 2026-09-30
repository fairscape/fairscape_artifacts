import React, { useState, useMemo } from "react";
import styled from "styled-components";
import { viewHref } from "../links";

import { GraphDataService } from "./GraphDataService";
import { getEntityType } from "./graphUtils";
import {
  AnnotationData,
  Assumption,
  Concern,
  ComputationError,
  ComputationReviewStatus,
  normalizeImpact,
  AssumptionImpact,
} from "../types/graph";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface LevelAssumption {
  impact: AssumptionImpact;
  name?: string;
  description: string;
  downstreamImpacts?: string;
  evidenceArtifactId?: string;
  evidenceLocation?: string;
  recommendedValidation?: string;
}

interface LevelNode {
  levelLabel: string;
  computationName: string;
  assumptions: LevelAssumption[];
  errors: ComputationError[];
  computationStatus: ComputationReviewStatus;
  counts: Record<AssumptionImpact, number>;
  totalCount: number;
  children: LevelNode[];
  depth: number;
}

function computeMaxDepth(nodes: LevelNode[]): number {
  if (nodes.length === 0) return 0;
  return Math.max(
    ...nodes.map((n) => Math.max(n.depth, computeMaxDepth(n.children))),
  );
}

// ---------------------------------------------------------------------------
// Dot helpers
// ---------------------------------------------------------------------------

type StatusKey = ComputationReviewStatus | "unknown";

function getNodeStatus(node: LevelNode): StatusKey {
  // Use LLM-decided status if available
  if (node.computationStatus && node.computationStatus !== "clear") {
    return node.computationStatus;
  }
  if (node.computationStatus === "clear") return "clear";
  // Fallback for old data: derive from errors/assumptions
  if (node.errors.length > 0) return "error_detected";
  if (node.counts.CRITICAL > 0) return "review_recommended";
  return "clear";
}

// ---------------------------------------------------------------------------
// Traversal Helpers
// ---------------------------------------------------------------------------

function normalizeRefs(
  refs: { "@id": string } | Array<{ "@id": string }> | undefined,
): Array<{ "@id": string }> {
  if (!refs) return [];
  return Array.isArray(refs) ? refs : [refs];
}

function getAssumptions(annotation: AnnotationData): Array<{
  impact: AssumptionImpact;
  name?: string;
  description: string;
  downstreamImpacts?: string;
  evidence?: { artifact: { "@id": string }; location?: string };
}> {
  if (
    annotation["evi:assumptions"] &&
    annotation["evi:assumptions"].length > 0
  ) {
    return annotation["evi:assumptions"].map((a) => ({
      ...a,
      impact: normalizeImpact(a.impact),
    }));
  }
  if (annotation["evi:concerns"] && annotation["evi:concerns"].length > 0) {
    return annotation["evi:concerns"].map((c: Concern) => ({
      impact: normalizeImpact(c.level),
      description: c.description,
    }));
  }
  return [];
}

function getCodeAssumptionsFromCA(ca: {
  assumptions?: Assumption[];
  concerns?: Concern[];
}): Array<{
  impact: AssumptionImpact;
  name?: string;
  description: string;
  downstreamImpacts?: string;
  evidence?: { artifact: { "@id": string }; location?: string };
}> {
  if (ca.assumptions && ca.assumptions.length > 0) {
    return ca.assumptions.map((a) => ({
      ...a,
      impact: normalizeImpact(a.impact),
    }));
  }
  if (ca.concerns && ca.concerns.length > 0) {
    return ca.concerns.map((c: Concern) => ({
      impact: normalizeImpact(c.level),
      description: c.description,
    }));
  }
  return [];
}

// ---------------------------------------------------------------------------
// Build Level Hierarchy
// ---------------------------------------------------------------------------

export function buildLevelHierarchy(
  datasetId: string,
  dataService: GraphDataService,
): LevelNode[] {
  const visited = new Set<string>();

  function processDataset(dsId: string, depth: number): LevelNode[] {
    const entity = dataService.getNode(dsId);
    if (!entity) return [];

    const entityType = getEntityType(entity["@type"]);
    if (
      entityType !== "Dataset" &&
      entityType !== "Sample" &&
      entityType !== "DatasetCollection" &&
      entityType !== "DatasetGroup"
    ) {
      return [];
    }

    const generatedByRefs = normalizeRefs(entity.generatedBy);
    const levelNodes: LevelNode[] = [];

    for (const compRef of generatedByRefs) {
      const compId = compRef["@id"];
      if (visited.has(compId)) continue;
      visited.add(compId);

      const comp = dataService.getNode(compId);
      if (!comp) continue;

      const assumptions: LevelAssumption[] = [];
      let errors: ComputationError[] = [];
      let computationStatus: ComputationReviewStatus = "clear";
      const annotation = dataService.getAnnotationFor(compId);

      if (annotation) {
        for (const a of getAssumptions(annotation)) {
          assumptions.push({
            impact: a.impact,
            name: a.name,
            description: a.description,
            downstreamImpacts: a.downstreamImpacts,
            evidenceArtifactId: a.evidence?.artifact?.["@id"],
            evidenceLocation: a.evidence?.location,
            recommendedValidation: (a as any).recommendedValidation,
          });
        }

        const codeAnalysisList = annotation["evi:codeAnalysis"] || [];
        for (const ca of codeAnalysisList) {
          for (const a of getCodeAssumptionsFromCA(ca)) {
            assumptions.push({
              impact: a.impact,
              name: a.name,
              description: a.description,
              downstreamImpacts: a.downstreamImpacts,
              evidenceArtifactId: a.evidence?.artifact?.["@id"],
              evidenceLocation: a.evidence?.location,
              recommendedValidation: (a as any).recommendedValidation,
            });
          }
        }

        errors = annotation["evi:errors"] || [];
        computationStatus =
          (annotation["evi:computationStatus"] as ComputationReviewStatus) ||
          "clear";
      }

      const counts: Record<AssumptionImpact, number> = {
        CRITICAL: 0,
        MAJOR: 0,
        MINOR: 0,
      };
      for (const a of assumptions) {
        counts[a.impact]++;
      }

      const children: LevelNode[] = [];
      const usedDatasetRefs = normalizeRefs(comp.usedDataset);
      for (const dsRef of usedDatasetRefs) {
        children.push(...processDataset(dsRef["@id"], depth + 1));
      }

      levelNodes.push({
        levelLabel: "",
        computationName: comp.name || comp.label || compId,
        assumptions,
        errors,
        computationStatus,
        counts,
        totalCount: assumptions.length,
        children,
        depth,
      });
    }

    for (let i = 0; i < levelNodes.length; i++) {
      const letter = levelNodes.length > 1 ? String.fromCharCode(65 + i) : "";
      levelNodes[i].levelLabel = `Level ${depth}${letter}`;
    }

    return levelNodes;
  }

  return processDataset(datasetId, 1);
}

// ---------------------------------------------------------------------------
// Colors
// ---------------------------------------------------------------------------

const DOT_COLORS: Record<StatusKey, { fill: string; border: string }> = {
  clear: { fill: "#27ae60", border: "#1e8449" },
  review_recommended: { fill: "#8e44ad", border: "#6c3483" },
  error_detected: { fill: "#e74c3c", border: "#c0392b" },
  unknown: { fill: "#ffffff", border: "#bdc3c7" },
};

const IMPACT_COLORS: Record<
  string,
  { bg: string; border: string; text: string }
> = {
  CRITICAL: { bg: "#f3e8f9", border: "#7b2d8e", text: "#7b2d8e" },
  MAJOR: { bg: "#fef9e7", border: "#d68910", text: "#d68910" },
  MINOR: { bg: "#eaf4fb", border: "#1a5276", text: "#1a5276" },
};

// ---------------------------------------------------------------------------
// Styled Components — Modal shell
// ---------------------------------------------------------------------------

const ChainModalOverlay = styled.div`
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.5);
  z-index: 10000;
  display: flex;
  align-items: stretch;
  justify-content: center;
  padding: 24px;
`;

const ChainModalContent = styled.div`
  background: #fff;
  border-radius: 2px;
  width: 100%;
  max-width: 1400px;
  display: flex;
  flex-direction: column;
  font-family: sans-serif;
  overflow: hidden;
`;

const ChainModalHeader = styled.div`
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px 24px;
  border-bottom: 1px solid #ebf2f4;
  flex-shrink: 0;

  h2 {
    margin: 0;
    font-size: 18px;
    color: #2c3e50;
  }

  .close-btn {
    background: none;
    border: none;
    font-size: 24px;
    cursor: pointer;
    color: #666;
    &:hover {
      color: #000;
    }
  }
`;

const EmptyMessage = styled.div`
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #888;
  font-size: 15px;
  padding: 60px;
`;

// ---------------------------------------------------------------------------
// Styled Components — Dot pyramid
// ---------------------------------------------------------------------------

const TreeContainer = styled.div`
  flex: 1;
  overflow-y: auto;
  padding: 24px 24px 32px;
  position: relative;
  display: flex;

  @media (max-width: 768px) {
    padding: 16px 12px 24px;
    overflow-x: auto;
  }
`;

const FlowArrow = styled.div`
  display: flex;
  flex-direction: column;
  align-items: center;
  width: 36px;
  flex-shrink: 0;
  padding: 4px 0 8px;
  user-select: none;
`;

const FlowArrowLabel = styled.span`
  font-size: 9px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.4px;
  color: #aaa;
  text-align: center;
  line-height: 1.2;
`;

const PyramidColumn = styled.div`
  flex: 1;
  min-width: 0;
  position: relative;
`;

const FloatingLegend = styled.div`
  position: absolute;
  top: 16px;
  right: 16px;
  background: #fff;
  border: 1px solid #ddd;
  border-radius: 2px;
  padding: 10px 14px;
  z-index: 1;
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 11px;
  color: #555;
  user-select: none;
`;

const LegendItem = styled.div`
  display: flex;
  align-items: center;
  gap: 6px;
`;

const LegendDot = styled.span<{ $fill: string; $border: string }>`
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: ${(e) => e.$fill};
  border: 1.5px solid ${(e) => e.$border};
  flex-shrink: 0;
`;

const DepthRowWrapper = styled.div<{ $depth: number; $maxDepth: number }>`
  width: ${(e) => Math.min(100, 40 + (e.$depth / Math.max(e.$maxDepth, 1)) * 60)}%;
  margin: 0 auto 12px;
  display: flex;
  gap: 10px;
`;

const NodeCard = styled.div<{ $selected: boolean }>`
  flex: 1;
  min-width: 0;
  border: 1px solid ${(e) => (e.$selected ? "#2c3e50" : "#EBF2F4")};
  border-radius: 2px;
  background: ${(e) => (e.$selected ? "#fafbfc" : "#fff")};
  box-shadow: ${(e) => (e.$selected ? "0 0 0 2px #2c3e50" : "none")};
  transition:
    box-shadow 0.15s,
    border-color 0.15s;
  overflow: hidden;
  cursor: pointer;

  &:hover {
    border-color: #84939a;
  }
`;

const NodeCardHeader = styled.div`
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 14px;
`;

const SeverityDot = styled.span<{ $fill: string; $border: string }>`
  width: 20px;
  height: 20px;
  border-radius: 50%;
  background: ${(e) => e.$fill};
  border: 2px solid ${(e) => e.$border};
  flex-shrink: 0;
`;

const NodeName = styled.span`
  font-size: 13px;
  font-weight: 600;
  color: #2c3e50;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex: 1;
`;

const NodeMeta = styled.span`
  font-size: 11px;
  color: #999;
  white-space: nowrap;
  flex-shrink: 0;
`;

const CountBadges = styled.div`
  display: flex;
  gap: 4px;
  flex-shrink: 0;
`;

const CountBadge = styled.span<{ $color: string; $bg: string }>`
  display: inline-flex;
  align-items: center;
  gap: 2px;
  padding: 1px 6px;
  border-radius: 2px;
  font-size: 10px;
  font-weight: 600;
  background: ${(e) => e.$bg};
  color: ${(e) => e.$color};
`;

// ---------------------------------------------------------------------------
// Styled Components — Expanded detail panel
// ---------------------------------------------------------------------------

const DetailPanel = styled.div`
  border-top: 1px solid #ebf2f4;
  padding: 12px 14px;
  background: #f7f9f9;
  max-height: 300px;
  overflow-y: auto;
`;

const AssumptionGroupLabel = styled.div<{ $color: string }>`
  font-size: 11px;
  font-weight: 700;
  color: ${(e) => e.$color};
  text-transform: uppercase;
  letter-spacing: 0.4px;
  margin: 10px 0 4px;

  &:first-child {
    margin-top: 0;
  }
`;

const AssumptionRow = styled.div<{ $borderColor: string; $bg: string }>`
  background: ${(e) => e.$bg};
  border-left: 3px solid ${(e) => e.$borderColor};
  border-radius: 2px;
  margin: 3px 0;
  font-size: 12px;
  line-height: 1.5;
  color: #444;
  overflow: hidden;
`;

const AssumptionSummaryRow = styled.div`
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 5px 10px;
  cursor: pointer;
  user-select: none;

  &:hover {
    filter: brightness(0.96);
  }
`;

const AssumptionChevron = styled.span`
  font-size: 9px;
  color: #999;
  flex-shrink: 0;
  width: 10px;
`;

const AssumptionTitle = styled.span`
  font-weight: 600;
  color: #2c3e50;
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
`;

const AssumptionExpandedBody = styled.div`
  padding: 4px 10px 8px 26px;
  font-size: 12px;
  line-height: 1.5;
  color: #555;
`;

const DownstreamImpact = styled.div`
  background: #fff8e1;
  border-left: 3px solid #ffb300;
  padding: 3px 8px;
  border-radius: 2px;
  margin-top: 4px;
  font-size: 11px;
  color: #6d5800;
`;

const EvidenceLink = styled.a`
  font-size: 11px;
  color: #007bff;
  text-decoration: none;
  &:hover {
    text-decoration: underline;
  }
`;

const ExpandableAssumption: React.FC<{
  assumption: LevelAssumption;
  colors: { bg: string; border: string; text: string };
}> = ({ assumption, colors }) => {
  const [open, setOpen] = useState(false);
  const displayName =
    assumption.name ||
    (assumption.description.length > 80
      ? assumption.description.slice(0, 80) + "..."
      : assumption.description);

  return (
    <AssumptionRow $borderColor={colors.border} $bg={colors.bg}>
      <AssumptionSummaryRow
        onClick={(e) => {
          e.stopPropagation();
          setOpen(!open);
        }}
      >
        <AssumptionChevron>{open ? "\u25BC" : "\u25B6"}</AssumptionChevron>
        <AssumptionTitle title={assumption.name || assumption.description}>
          {displayName}
        </AssumptionTitle>
      </AssumptionSummaryRow>
      {open && (
        <AssumptionExpandedBody>
          {assumption.name && (
            <div style={{ marginBottom: 4 }}>{assumption.description}</div>
          )}
          {assumption.downstreamImpacts && (
            <DownstreamImpact>
              <strong
                style={{
                  fontSize: 10,
                  textTransform: "uppercase",
                  color: "#888",
                }}
              >
                If wrong:{" "}
              </strong>
              {assumption.downstreamImpacts}
            </DownstreamImpact>
          )}
          {assumption.evidenceArtifactId && (
            <div style={{ marginTop: 3 }}>
              <EvidenceLink
                href={viewHref(assumption.evidenceArtifactId)}
                target="_blank"
                rel="noopener noreferrer"
                onClick={(e) => e.stopPropagation()}
              >
                {assumption.evidenceArtifactId}
              </EvidenceLink>
              {assumption.evidenceLocation && (
                <span style={{ color: "#888", marginLeft: 4, fontSize: 11 }}>
                  ({assumption.evidenceLocation})
                </span>
              )}
            </div>
          )}
          {assumption.recommendedValidation && (
            <div
              style={{
                marginTop: 3,
                background: "#e8f5e9",
                borderLeft: "3px solid #43a047",
                padding: "3px 8px",
                borderRadius: 2,
                fontSize: 11,
              }}
            >
              <strong
                style={{
                  fontSize: 10,
                  textTransform: "uppercase",
                  color: "#2e7d32",
                }}
              >
                Validate:{" "}
              </strong>
              {assumption.recommendedValidation}
            </div>
          )}
        </AssumptionExpandedBody>
      )}
    </AssumptionRow>
  );
};

// ---------------------------------------------------------------------------
// Flatten tree into depth-grouped rows
// ---------------------------------------------------------------------------

interface FlatRow {
  siblings: LevelNode[];
  depth: number;
}

function flattenToRows(nodes: LevelNode[], depth: number = 1): FlatRow[] {
  if (nodes.length === 0) return [];
  const result: FlatRow[] = [{ siblings: nodes, depth }];
  const allChildren: LevelNode[] = [];
  for (const node of nodes) {
    allChildren.push(...node.children);
  }
  result.push(...flattenToRows(allChildren, depth + 1));
  return result;
}

// ---------------------------------------------------------------------------
// Node Card Component
// ---------------------------------------------------------------------------

const IMPACT_ORDER: AssumptionImpact[] = ["CRITICAL", "MAJOR", "MINOR"];

const LevelNodeCard: React.FC<{
  node: LevelNode;
  selected: boolean;
  onToggle: () => void;
  activeSeverities: Set<AssumptionImpact>;
}> = ({ node, selected, onToggle, activeSeverities }) => {
  const status = getNodeStatus(node);
  const dotColors = DOT_COLORS[status];

  const grouped = useMemo(() => {
    const g: Record<AssumptionImpact, LevelAssumption[]> = {
      CRITICAL: [],
      MAJOR: [],
      MINOR: [],
    };
    for (const a of node.assumptions) g[a.impact].push(a);
    return g;
  }, [node.assumptions]);

  return (
    <NodeCard $selected={selected} onClick={onToggle}>
      <NodeCardHeader>
        <SeverityDot $fill={dotColors.fill} $border={dotColors.border} />
        <NodeName title={node.computationName}>{node.computationName}</NodeName>
        <NodeMeta>{node.levelLabel}</NodeMeta>
        {(node.totalCount > 0 || node.errors.length > 0) && (
          <CountBadges>
            {node.errors.length > 0 && (
              <CountBadge $color="#c0392b" $bg="#fdecea">
                {node.errors.length}E
              </CountBadge>
            )}
            {IMPACT_ORDER.map((impact) => {
              const count = node.counts[impact];
              if (count === 0) return null;
              const colors = IMPACT_COLORS[impact];
              return (
                <CountBadge key={impact} $color={colors.text} $bg={colors.bg}>
                  {count}
                </CountBadge>
              );
            })}
          </CountBadges>
        )}
      </NodeCardHeader>

      {selected &&
        (() => {
          const visibleGroups = IMPACT_ORDER.filter(
            (impact) =>
              activeSeverities.has(impact) && grouped[impact].length > 0,
          );
          const hasErrors = node.errors.length > 0;
          if (visibleGroups.length === 0 && !hasErrors) {
            return (
              <DetailPanel>
                <div
                  style={{ color: "#999", fontSize: 12, fontStyle: "italic" }}
                >
                  {node.totalCount === 0
                    ? "No assumptions identified for this computation."
                    : "No assumptions match the selected severity levels."}
                </div>
              </DetailPanel>
            );
          }
          return (
            <DetailPanel>
              {hasErrors && (
                <>
                  <AssumptionGroupLabel $color="#c0392b">
                    ERRORS ({node.errors.length})
                  </AssumptionGroupLabel>
                  {node.errors.map((err, i) => (
                    <AssumptionRow
                      key={`err-${i}`}
                      $borderColor="#e74c3c"
                      $bg="#fdecea"
                    >
                      <AssumptionSummaryRow
                        onClick={(e) => e.stopPropagation()}
                      >
                        <span
                          style={{
                            fontWeight: 600,
                            color: "#c0392b",
                            fontSize: 11,
                            textTransform: "uppercase",
                            flexShrink: 0,
                          }}
                        >
                          {err.severity}
                        </span>
                        <AssumptionTitle
                          style={{ color: "#7f1d1d" }}
                          title={err.description}
                        >
                          {err.description.length > 80
                            ? err.description.slice(0, 80) + "..."
                            : err.description}
                        </AssumptionTitle>
                      </AssumptionSummaryRow>
                      {err.affectedOutputs && (
                        <div
                          style={{
                            padding: "2px 10px 6px 26px",
                            fontSize: 11,
                            color: "#7f1d1d",
                          }}
                        >
                          <strong>Affected: </strong>
                          {err.affectedOutputs}
                        </div>
                      )}
                    </AssumptionRow>
                  ))}
                </>
              )}
              {visibleGroups.map((impact) => {
                const items = grouped[impact];
                const colors = IMPACT_COLORS[impact];
                return (
                  <React.Fragment key={impact}>
                    <AssumptionGroupLabel $color={colors.text}>
                      {impact} ({items.length})
                    </AssumptionGroupLabel>
                    {items.map((a, i) => (
                      <ExpandableAssumption
                        key={i}
                        assumption={a}
                        colors={colors}
                      />
                    ))}
                  </React.Fragment>
                );
              })}
            </DetailPanel>
          );
        })()}
    </NodeCard>
  );
};

// ---------------------------------------------------------------------------
// Modal Component
// ---------------------------------------------------------------------------

interface AssumptionChainModalProps {
  datasetId: string;
  datasetName: string;
  dataService: GraphDataService;
  onClose: () => void;
}

const SeverityFilterBar = styled.div`
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 24px;
  border-bottom: 1px solid #ebf2f4;
  background: #f7f9f9;
  flex-shrink: 0;
`;

const SeverityFilterLabel = styled.span`
  font-size: 11px;
  color: #888;
  margin-right: 4px;
`;

const SeverityToggle = styled.button<{
  $color: string;
  $bg: string;
  $active: boolean;
}>`
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px 10px;
  border-radius: 2px;
  font-size: 11px;
  font-weight: 600;
  cursor: pointer;
  border: 1.5px solid ${(e) => (e.$active ? e.$color : "#ddd")};
  background: ${(e) => (e.$active ? e.$bg : "#fff")};
  color: ${(e) => (e.$active ? e.$color : "#bbb")};
  transition: all 0.15s;

  &:hover {
    border-color: ${(e) => e.$color};
    color: ${(e) => e.$color};
  }
`;

const SeverityDotSmall = styled.span<{ $fill: string; $active: boolean }>`
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: ${(e) => (e.$active ? e.$fill : "#ccc")};
  flex-shrink: 0;
`;

const AssumptionChainModal: React.FC<AssumptionChainModalProps> = ({
  datasetId,
  datasetName,
  dataService,
  onClose,
}) => {
  const levels = useMemo(
    () => buildLevelHierarchy(datasetId, dataService),
    [datasetId, dataService],
  );

  const maxDepth = useMemo(() => computeMaxDepth(levels), [levels]);
  const rows = useMemo(() => flattenToRows(levels), [levels]);
  const hasChain = rows.length > 0;

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [activeSeverities, setActiveSeverities] = useState<
    Set<AssumptionImpact>
  >(() => new Set(["CRITICAL"]));

  const handleToggle = (id: string) => {
    setSelectedId((prev) => (prev === id ? null : id));
  };

  const toggleSeverity = (impact: AssumptionImpact) => {
    setActiveSeverities((prev) => {
      const next = new Set(prev);
      if (next.has(impact)) next.delete(impact);
      else next.add(impact);
      return next;
    });
  };

  return (
    <ChainModalOverlay onClick={onClose}>
      <ChainModalContent onClick={(e) => e.stopPropagation()}>
        <ChainModalHeader>
          <h2>Assumption Chain: {datasetName}</h2>
          <button className="close-btn" onClick={onClose}>
            &times;
          </button>
        </ChainModalHeader>

        {hasChain && (
          <SeverityFilterBar>
            <SeverityFilterLabel>Show:</SeverityFilterLabel>
            {IMPACT_ORDER.map((impact) => {
              const colors = IMPACT_COLORS[impact];
              const active = activeSeverities.has(impact);
              return (
                <SeverityToggle
                  key={impact}
                  $color={colors.text}
                  $bg={colors.bg}
                  $active={active}
                  onClick={() => toggleSeverity(impact)}
                >
                  <SeverityDotSmall $fill={colors.border} $active={active} />
                  {impact.charAt(0) + impact.slice(1).toLowerCase()}
                </SeverityToggle>
              );
            })}
          </SeverityFilterBar>
        )}

        {hasChain ? (
          <TreeContainer>
            <FloatingLegend>
              <LegendItem>
                <LegendDot
                  $fill={DOT_COLORS.clear.fill}
                  $border={DOT_COLORS.clear.border}
                />
                <span>Low priority</span>
              </LegendItem>
              <LegendItem>
                <LegendDot
                  $fill={DOT_COLORS.error_detected.fill}
                  $border={DOT_COLORS.error_detected.border}
                />
                <span>Error</span>
              </LegendItem>
              <LegendItem>
                <LegendDot
                  $fill={DOT_COLORS.review_recommended.fill}
                  $border={DOT_COLORS.review_recommended.border}
                />
                <span>Verify</span>
              </LegendItem>
            </FloatingLegend>
            <FlowArrow>
              <FlowArrowLabel>Outputs</FlowArrowLabel>
              <svg
                width="12"
                style={{ flex: 1, minHeight: 40 }}
                preserveAspectRatio="none"
              >
                <defs>
                  <marker
                    id="arrowUp"
                    viewBox="0 0 10 10"
                    refX="5"
                    refY="0"
                    markerWidth="6"
                    markerHeight="6"
                    orient="auto"
                  >
                    <path d="M0,10 L5,0 L10,10" fill="#bbb" />
                  </marker>
                </defs>
                <line
                  x1="6"
                  y1="100%"
                  x2="6"
                  y2="0"
                  stroke="#bbb"
                  strokeWidth="1.5"
                  markerEnd="url(#arrowUp)"
                />
              </svg>
              <FlowArrowLabel>Inputs</FlowArrowLabel>
            </FlowArrow>
            <PyramidColumn>
              {rows.map((row, ri) => (
                <DepthRowWrapper
                  key={`row-${row.depth}-${ri}`}
                  $depth={row.depth}
                  $maxDepth={maxDepth}
                >
                  {row.siblings.map((node, ni) => {
                    const cardId = `${row.depth}-${ni}`;
                    return (
                      <LevelNodeCard
                        key={cardId}
                        node={node}
                        selected={selectedId === cardId}
                        onToggle={() => handleToggle(cardId)}
                        activeSeverities={activeSeverities}
                      />
                    );
                  })}
                </DepthRowWrapper>
              ))}
            </PyramidColumn>
          </TreeContainer>
        ) : (
          <EmptyMessage>
            No provenance chain available for this dataset.
          </EmptyMessage>
        )}
      </ChainModalContent>
    </ChainModalOverlay>
  );
};

export default AssumptionChainModal;
