import { Node, Edge } from "reactflow";
import dagre from "dagre";

// fairscape-artifacts: the card in src/StepNode.tsx is 220 wide and up to
// 84 tall; the gaps are tighter than the web client's (200 / 120) so a
// whole pipeline fits the page at a readable zoom.
const NODE_WIDTH = 220;
const NODE_HEIGHT = 84;
const RANK_SEP = 130;
const NODE_SEP = 28;
const ALIGNMENT = "DL";
const MARGIN_X = 50;
const MARGIN_Y = 50;

// Above this many nodes dagre becomes too slow; fall back to a plain grid.
const MAX_DAGRE_NODES = 800;

const getGridLayout = (
  nodes: Node[],
  edges: Edge[],
): { nodes: Node[]; edges: Edge[] } => {
  const columns = Math.ceil(Math.sqrt(nodes.length));
  return {
    nodes: nodes.map((node, index) => ({
      ...node,
      position: {
        x: MARGIN_X + (index % columns) * (NODE_WIDTH + NODE_SEP),
        y: MARGIN_Y + Math.floor(index / columns) * (NODE_HEIGHT + NODE_SEP),
      },
      width: NODE_WIDTH,
      height: NODE_HEIGHT,
    })),
    edges,
  };
};

export const getLayoutedElements = (
  nodes: Node[],
  edges: Edge[],
  direction = "LR", // Default to Left-to-Right
): { nodes: Node[]; edges: Edge[] } => {
  if (!nodes || nodes.length === 0) {
    return { nodes, edges };
  }

  if (nodes.length > MAX_DAGRE_NODES) {
    console.warn(
      `Graph has ${nodes.length} nodes (> ${MAX_DAGRE_NODES}); using grid layout instead of dagre.`,
    );
    return getGridLayout(nodes, edges);
  }

  const dagreGraph = new dagre.graphlib.Graph();
  dagreGraph.setDefaultEdgeLabel(() => ({}));

  dagreGraph.setGraph({
    rankdir: direction,
    nodesep: NODE_SEP,
    ranksep: RANK_SEP,
    align: ALIGNMENT,
    marginx: MARGIN_X,
    marginy: MARGIN_Y,
  });

  nodes.forEach((node) => {
    dagreGraph.setNode(node.id, {
      width: NODE_WIDTH,
      height: NODE_HEIGHT,
    });
  });

  const nodeIds = new Set(nodes.map((n) => n.id));

  edges.forEach((edge) => {
    if (nodeIds.has(edge.source) && nodeIds.has(edge.target)) {
      dagreGraph.setEdge(edge.source, edge.target);
    } else {
      console.warn(
        `Skipping edge ${edge.id} due to missing source/target node in current layout batch.`,
      );
    }
  });

  try {
    dagre.layout(dagreGraph);
  } catch (e) {
    console.error("Dagre layout calculation failed:", e);
    return { nodes, edges };
  }

  const layoutedNodes = nodes.map((node): Node => {
    const nodeWithPosition = dagreGraph.node(node.id);
    if (nodeWithPosition) {
      const position = {
        x: nodeWithPosition.x - NODE_WIDTH / 2,
        y: nodeWithPosition.y - NODE_HEIGHT / 2,
      };
      return {
        ...node,
        position,
        // Include explicit width and height in the node data
        width: NODE_WIDTH,
        height: NODE_HEIGHT,
      };
    } else {
      console.warn(
        `Node ${node.id} not found in Dagre layout results. Keeping original position.`,
      );
      return {
        ...node,
        position: node.position || {
          x: Math.random() * 300,
          y: Math.random() * 300,
        },
      };
    }
  });

  return { nodes: layoutedNodes, edges };
};
