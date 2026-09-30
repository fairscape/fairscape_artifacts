// The node card. A step shows its name, a one-line headline and a status
// dot; data shows its name. Everything longer lives in the drawer, which a
// click opens. Producers sit to the left of consumers (the layout runs
// right-to-left over generatedBy edges), so the handles are swapped.
import React from "react";
import { Handle, NodeProps, Position } from "reactflow";
import Tippy from "@tippyjs/react";
import "tippy.js/dist/tippy.css";
import "tippy.js/themes/light.css";
import styled from "styled-components";
import { EvidenceNodeData } from "./vendor/types/graph";
import {
  KIND_COLORS,
  clip,
  firstSentences,
  headlineOf,
  humanName,
  statusOf,
  stepAssumptions,
} from "./text";

export const CARD_WIDTH = 220;
export const CARD_HEIGHT = 84;

export function kindOf(data: EvidenceNodeData): { key: string; label: string } {
  if (data.properties?._role === "result") return { key: "result", label: "Result" };
  switch (data.type) {
    case "Computation":
      return {
        key: "step",
        label: data._annotation?.["_number"] ? `Step ${data._annotation["_number"]}` : "Step",
      };
    case "Dataset":
    case "DatasetGroup":
    case "Sample":
      return { key: "data", label: "Data" };
    case "DatasetCollection":
      return { key: "files", label: "Files" };
    case "Software":
    case "MLModel":
    case "Instrument":
      return { key: "software", label: data.type === "MLModel" ? "Model" : "Software" };
    default:
      return { key: "other", label: data.type };
  }
}

const Card = styled.div<{ $accent: string; $selected: boolean; $expandable: boolean; $step: boolean }>`
  position: relative;
  box-sizing: border-box;
  width: ${CARD_WIDTH}px;
  min-height: ${(e) => (e.$step ? CARD_HEIGHT : 52)}px;
  padding: 7px 12px 8px 12px;
  border: 1px solid ${(e) => (e.$selected ? "#005f73" : "#d5dde0")};
  border-left: 4px solid ${(e) => e.$accent};
  border-radius: 3px;
  background: #fff;
  cursor: pointer;
  box-shadow: ${(e) => (e.$selected ? "0 0 0 3px rgba(0, 95, 115, 0.18)" : "0 1px 2px rgba(0,0,0,0.05)")};
  transition: box-shadow 0.15s, border-color 0.15s;
  font-family: Inter, system-ui, -apple-system, "Segoe UI", sans-serif;
  color: #18242a;

  &:hover {
    border-color: #005f73;
    border-left-color: ${(e) => e.$accent};
    box-shadow: 0 2px 8px rgba(0, 0, 0, 0.12);
  }
`;

const Kind = styled.div<{ $color: string }>`
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: ${(e) => e.$color};
  line-height: 1.2;
  margin-bottom: 3px;
`;

const Dot = styled.span<{ $color: string }>`
  width: 9px;
  height: 9px;
  border-radius: 50%;
  background: ${(e) => e.$color};
  box-shadow: 0 0 0 2px #fff;
`;

const Name = styled.div`
  font-size: 13px;
  font-weight: 600;
  line-height: 1.3;
  overflow-wrap: anywhere;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
`;

const Headline = styled.div`
  margin-top: 3px;
  font-size: 11px;
  line-height: 1.35;
  color: #51626b;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
`;

const More = styled.div`
  position: absolute;
  right: 8px;
  bottom: 5px;
  font-size: 10px;
  color: #84939a;
`;

const Tip = styled.div`
  max-width: 320px;
  font-family: Inter, system-ui, sans-serif;
  font-size: 12.5px;
  line-height: 1.45;
  color: #18242a;
  padding: 4px 2px;
  b { display: block; margin-bottom: 3px; }
  .hint { color: #84939a; font-size: 11.5px; margin-top: 6px; }
`;

const handleStyle = { background: "#84939a", width: 7, height: 7, border: "1px solid #fff" };

const StepNode: React.FC<NodeProps<EvidenceNodeData>> = ({ data, selected, isConnectable }) => {
  const kind = kindOf(data);
  const annotation = data._annotation;
  const isStep = data.type === "Computation";
  const status = statusOf(annotation);
  const accent = isStep && annotation ? status.color : KIND_COLORS[kind.key] || KIND_COLORS.other;
  const name = isStep ? humanName(data.label) : data.displayName || data.label;
  const headline = isStep ? headlineOf(data._sourceData, annotation) : "";

  const tip = (() => {
    if (isStep && annotation) {
      const n = stepAssumptions(annotation).length;
      return (
        <Tip>
          <b>{name}</b>
          {clip(firstSentences(annotation["evi:stepSummary"], 2), 260)}
          <div className="hint">
            {status.label}
            {n ? ` · ${n} assumption${n === 1 ? "" : "s"}` : ""} · click for details
          </div>
        </Tip>
      );
    }
    const desc = data.description || "";
    return (
      <Tip>
        <b>{name}</b>
        {desc ? clip(desc, 240) : `${kind.label}`}
        <div className="hint">{data.expandable ? "Click to see what it connects to" : "Click for details"}</div>
      </Tip>
    );
  })();

  return (
    <Tippy content={tip} theme="light" placement="top" delay={[450, 0]} appendTo={() => document.body} maxWidth={340}>
      <Card $accent={accent} $selected={!!selected} $expandable={!!data.expandable} $step={isStep}>
        <Handle type="target" position={Position.Right} isConnectable={isConnectable} style={handleStyle} />
        <Handle type="source" position={Position.Left} isConnectable={isConnectable} style={handleStyle} />
        <Kind $color={accent}>
          <span>{kind.label}</span>
          {isStep && annotation && <Dot $color={status.color} title={status.label} />}
        </Kind>
        <Name title={data.label}>{name}</Name>
        {headline && <Headline>{headline}</Headline>}
        {!isStep && data.expandable && <More>+</More>}
      </Card>
    </Tippy>
  );
};

export default React.memo(StepNode);
