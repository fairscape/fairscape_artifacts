// fairscape-artifacts: rewritten around the offline page. The web client's
// viewer drew from the outputs to a fixed depth and opened a modal from a
// tooltip; this one draws the step spine, sizes itself to the drawing, and
// opens the detail in a pop-up over the graph (src/Drawer.tsx).
import React, { useEffect, useState, useCallback, useRef } from "react";
import { createPortal } from "react-dom";
import styled from "styled-components";
import ReactFlow, {
  Controls,
  Background,
  BackgroundVariant,
  useNodesState,
  useEdgesState,
  ReactFlowProvider,
  useReactFlow,
  Node,
  Edge,
  OnNodesChange,
  OnEdgesChange,
  NodeChange,
  EdgeChange,
} from "reactflow";
import "reactflow/dist/style.css";

import { RawGraphData, EvidenceNode, EvidenceEdge } from "../types/graph";
import { GraphDataService } from "./GraphDataService";
import { GraphBuilder } from "./graphUtils";
import { getLayoutedElements } from "../EvidenceGraph/utils/layoutUtils";
import StepNode from "../../StepNode";
import Drawer from "../../Drawer";

export const GraphDataServiceContext =
  React.createContext<GraphDataService | null>(null);

const resetHandlers = new Set<() => void>();
export function resetGraph(): void {
  resetHandlers.forEach((fn) => fn());
}

const MIN_HEIGHT = 420;
const MAX_HEIGHT = 780;

const ViewerWrapper = styled.div<{ $height: number }>`
  width: 100%;
  height: ${(e) => e.$height}px;
  position: relative;
  border: 1px solid #e2e8ea;
  border-radius: 3px;
  background-color: #fff;
  overflow: hidden;

  .flow { width: 100%; height: 100%; position: relative; }
  .toolbar { position: absolute; top: 10px; right: 10px; z-index: 5; display: flex; gap: 6px; }
  .toolbar button { font: 600 12px Inter, system-ui, sans-serif; color: #51626b; background: #fff; border: 1px solid #c3ced2; border-radius: 3px; padding: 4px 10px; cursor: pointer; }
  .toolbar button:hover { color: #005f73; border-color: #005f73; }
  .react-flow__edge path { stroke: #b8c4c9; stroke-width: 1.4px; transition: stroke 0.2s ease; }
  .react-flow__edge.selected path, .react-flow__edge:hover path { stroke: #005f73; }
  .react-flow__edge-textbg { fill: #fff; }
  .react-flow__edge-text { font-size: 10px; fill: #51626b; font-family: ui-monospace, Menlo, monospace; }
  .react-flow__attribution { font-size: 9px; opacity: 0.6; }
  .react-flow__node { cursor: pointer; }

`;

const Backdrop = styled.div`
  position: fixed;
  inset: 0;
  z-index: 10000;
  background: rgba(10, 33, 39, 0.45);
  display: flex;
  align-items: flex-start;
  justify-content: center;
  padding: 6vh 16px 16px;
  overflow-y: auto;
`;

const Dialog = styled.div`
  width: min(760px, 100%);
  max-height: 86vh;
  overflow-y: auto;
  background: #fff;
  border-radius: 4px;
  box-shadow: 0 12px 40px rgba(0, 0, 0, 0.25);
  outline: none;
`;

const LoadingOverlay = styled.div`
  position: absolute;
  inset: 0;
  background: rgba(255, 255, 255, 0.6);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 10;
  font-size: 13px;
  color: #84939a;
  font-family: Inter, system-ui, sans-serif;
`;

const nodeTypes = { evidenceNode: StepNode };
type RFNode = Node<EvidenceNode["data"]>;
type RFEdge = Edge<EvidenceEdge>;

interface GraphRendererProps {
  dataService: GraphDataService | null;
  selectedId: string | null;
  onSelect: (id: string | null) => void;
  finals: string[];
  spine: boolean;
  initialDepth: number;
  deferSoftware: boolean;
}

const GraphRenderer: React.FC<GraphRendererProps> = ({
  dataService,
  selectedId,
  onSelect,
  finals,
  spine,
  initialDepth,
  deferSoftware,
}) => {
  const [nodes, setNodes, onNodesChangeInternal] = useNodesState<RFNode["data"]>([]);
  const [edges, setEdges, onEdgesChangeInternal] = useEdgesState<RFEdge>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [height, setHeight] = useState(MIN_HEIGHT);
  const { fitView } = useReactFlow();
  const graphBuilderRef = useRef<GraphBuilder | null>(null);

  const draw = useCallback(() => {
    if (!dataService) return null;
    graphBuilderRef.current = new GraphBuilder(dataService);
    return spine
      ? graphBuilderRef.current.buildStepSpine(finals)
      : graphBuilderRef.current.buildInitialGraph(initialDepth, { deferSoftware });
  }, [dataService, spine, finals, initialDepth, deferSoftware]);

  const applyLayout = useCallback(
    (elements: { nodes: EvidenceNode[]; edges: EvidenceEdge[] }, focus: string[] | "all" | null) => {
      setIsLoading(true);
      setTimeout(() => {
        try {
          const { nodes: laid, edges: laidEdges } = getLayoutedElements(
            elements.nodes as Node[],
            elements.edges as Edge[],
            "RL",
          );
          const ys = laid.map((n) => n.position.y);
          const span = laid.length ? Math.max(...ys) - Math.min(...ys) + 84 : 0;
          setHeight(Math.max(MIN_HEIGHT, Math.min(MAX_HEIGHT, span + 150)));
          setNodes(laid as RFNode[]);
          setEdges(laidEdges as RFEdge[]);
          if (focus === "all") {
            setTimeout(() => fitView({ padding: 0.12, duration: 300, maxZoom: 1 }), 80);
          } else if (focus && focus.length) {
            setTimeout(() => fitView({ nodes: focus.map((id) => ({ id })), padding: 0.25, duration: 300, maxZoom: 1 }), 80);
          }
        } catch (e) {
          console.error("Layout failed:", e);
          setNodes(elements.nodes as RFNode[]);
          setEdges(elements.edges as RFEdge[]);
        } finally {
          setIsLoading(false);
        }
      }, 10);
    },
    [setNodes, setEdges, fitView],
  );

  useEffect(() => {
    if (!dataService) {
      setNodes([]);
      setEdges([]);
      return;
    }
    const elements = draw();
    if (elements) applyLayout(elements, "all");
  }, [dataService, applyLayout, draw, setNodes, setEdges]);

  // Back to the first drawing. Also reachable from the page as
  // window.FairscapeAnnotatedGraph.reset().
  const reset = useCallback(() => {
    onSelect(null);
    const elements = draw();
    if (elements) applyLayout(elements, "all");
  }, [draw, applyLayout, onSelect]);
  useEffect(() => {
    resetHandlers.add(reset);
    return () => void resetHandlers.delete(reset);
  }, [reset]);

  // Esc closes the pop-up.
  useEffect(() => {
    if (!selectedId) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onSelect(null);
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [selectedId, onSelect]);

  // Mirror the page's selection onto the nodes, and bring it into view.
  useEffect(() => {
    setNodes((nds) => nds.map((n) => (!!n.selected === (n.id === selectedId) ? n : { ...n, selected: n.id === selectedId })));
    if (!selectedId) return;
    const timer = setTimeout(() => {
      if (nodes.some((n) => n.id === selectedId)) {
        fitView({ nodes: [{ id: selectedId }], padding: 1.2, duration: 400, maxZoom: 1 });
      }
    }, 60);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedId]);

  const onNodeClick = useCallback(
    (_event: React.MouseEvent, node: RFNode) => {
      if (!dataService || !graphBuilderRef.current) return;
      onSelect(node.id);
      if (!node.data.expandable) return;
      setIsLoading(true);
      setTimeout(() => {
        if (!graphBuilderRef.current) return;
        const before = new Set(graphBuilderRef.current.getElements().nodes.map((n) => n.id));
        const elements = graphBuilderRef.current.expandNode(node.id);
        const fresh = elements.nodes.map((n) => n.id).filter((id) => !before.has(id));
        // Refit around the click: the drawer has just taken part of the
        // frame, and the whole drawing would come out too small.
        applyLayout(elements, [node.id, ...fresh]);
      }, 10);
    },
    [dataService, applyLayout, onSelect],
  );

  const handleNodesChange: OnNodesChange = useCallback(
    (changes: NodeChange[]) => {
      const relevant = changes.filter(
        (c) => c.type !== "select" && (!isLoading || (c.type === "position" && c.dragging === true)),
      );
      if (relevant.length) onNodesChangeInternal(relevant);
    },
    [isLoading, onNodesChangeInternal],
  );

  const handleEdgesChange: OnEdgesChange = useCallback(
    (changes: EdgeChange[]) => onEdgesChangeInternal(changes),
    [onEdgesChangeInternal],
  );

  return (
    <GraphDataServiceContext.Provider value={dataService}>
      <ViewerWrapper $height={height}>
        <div className="flow">
          {isLoading && <LoadingOverlay>Drawing…</LoadingOverlay>}
          <div className="toolbar">
            <button type="button" onClick={reset} title="Back to the first drawing">Reset graph</button>
          </div>
          <ReactFlow
            nodes={nodes}
            edges={edges}
            onNodesChange={handleNodesChange}
            onEdgesChange={handleEdgesChange}
            nodeTypes={nodeTypes}
            onNodeClick={onNodeClick}
            onPaneClick={() => onSelect(null)}
            nodesDraggable={!isLoading}
            nodesConnectable={false}
            elementsSelectable={false}
            minZoom={0.1}
            maxZoom={3}
            fitView={false}
          >
            <Background variant={BackgroundVariant.Dots} gap={16} size={0.6} color="#d5dde0" />
            <Controls showInteractive={false} />
          </ReactFlow>
        </div>
      </ViewerWrapper>
      {selectedId && dataService && createPortal(
        <Backdrop onMouseDown={(e) => e.target === e.currentTarget && onSelect(null)}>
          <Dialog role="dialog" aria-modal="true">
            <Drawer nodeId={selectedId} dataService={dataService} onClose={() => onSelect(null)} onPick={(id) => onSelect(id)} />
          </Dialog>
        </Backdrop>,
        document.body,
      )}
    </GraphDataServiceContext.Provider>
  );
};

interface AnnotatedGraphViewerProps {
  graphData: RawGraphData | null;
  selectedId: string | null;
  onSelect: (id: string | null) => void;
  finals?: string[];
  spine?: boolean;
  initialDepth?: number;
  deferSoftware?: boolean;
}

const AnnotatedGraphViewer: React.FC<AnnotatedGraphViewerProps> = ({
  graphData,
  selectedId,
  onSelect,
  finals = [],
  spine = true,
  initialDepth = 3,
  deferSoftware = true,
}) => {
  const [dataService, setDataService] = useState<GraphDataService | null>(null);

  useEffect(() => {
    setDataService(graphData ? new GraphDataService(graphData) : null);
  }, [graphData]);

  if (!graphData) {
    return (
      <div style={{ height: 200, display: "flex", alignItems: "center", justifyContent: "center", border: "1px solid #ddd", borderRadius: 8, background: "#f8f9fa", color: "#666" }}>
        No annotated evidence graph data available
      </div>
    );
  }

  return (
    <ReactFlowProvider>
      <GraphRenderer
        dataService={dataService}
        selectedId={selectedId}
        onSelect={onSelect}
        finals={finals}
        spine={spine}
        initialDepth={initialDepth}
        deferSoftware={deferSoftware}
      />
    </ReactFlowProvider>
  );
};

export default AnnotatedGraphViewer;
