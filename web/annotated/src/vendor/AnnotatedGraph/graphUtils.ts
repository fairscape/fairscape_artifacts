import {
  RawGraphEntity,
  EvidenceNodeData,
  EvidenceNode,
  EvidenceEdge,
} from "../types/graph";
import { GraphDataService } from "./GraphDataService";

const MAX_LABEL_LENGTH = 50;

function refIds(value: any): string[] {
  const list = Array.isArray(value) ? value : value == null ? [] : [value];
  return list
    .map((v: any) => (typeof v === "string" ? v : v?.["@id"]))
    .filter((v: any): v is string => typeof v === "string" && !!v);
}
const COLLECTION_THRESHOLD = 5;

export function getEntityType(typeUri: string | string[] | undefined): string {
  if (!typeUri) return "Unknown";

  if (Array.isArray(typeUri)) {
    // Check special types first
    if (typeUri.some((t) => t.includes("ROCrate"))) return "ROCrate";
    if (typeUri.some((t) => t.includes("AnnotatedComputation")))
      return "AnnotatedComputation";
    if (typeUri.some((t) => t.includes("AnnotatedEvidenceGraph")))
      return "AnnotatedEvidenceGraph";
    if (typeUri.some((t) => t.includes("DatasetGroup"))) return "DatasetGroup";

    const typeString = typeUri[typeUri.length - 1];
    return typeString.split(/[#\/]/).pop() || "Unknown";
  }

  const typeString = typeUri;
  return typeString.split(/[#\/]/).pop() || "Unknown";
}

export function abbreviateName(
  name: string | undefined,
  maxLength = MAX_LABEL_LENGTH,
): string {
  if (!name) return "";
  if (name.length <= maxLength) return name;
  return name.substring(0, maxLength - 3) + "...";
}

export function formatPropertyValue(value: any, propKey?: string): string {
  if (value === null || value === undefined) {
    return "<em>Not specified</em>";
  }

  if (typeof value === "string") {
    if (value.startsWith("ark:")) {
      return `<span style="font-family:monospace;font-size:12px">${value}</span>`;
    }
    const urlRegex = /^(https?:\/\/\S+)$/;
    if (urlRegex.test(value)) {
      return `<a href="${value}" target="_blank" rel="noopener noreferrer">${value}</a>`;
    }
    if (propKey === "command") {
      return `<pre>${value}</pre>`;
    }
    return String(value);
  }

  if (Array.isArray(value)) {
    return value.map((item) => formatPropertyValue(item)).join("<br/>");
  }

  if (typeof value === "object" && value !== null) {
    if (value["@id"]) {
      return formatPropertyValue(value["@id"]);
    }
    try {
      return `<pre>${JSON.stringify(value, null, 2)}</pre>`;
    } catch {
      return "[Object]";
    }
  }

  if (typeof value === "boolean") {
    return value ? "Yes" : "No";
  }

  return String(value);
}

export function getDisplayableProperties(
  entityData: RawGraphEntity | undefined,
): Record<string, any> {
  const excludeKeys = [
    "@id",
    "@type",
    "generatedBy",
    "usedDataset",
    "usedSoftware",
    "usedSample",
    "usedInstrument",
    "usedMLModel",
    "hasOutputs",
    "createdBy",
    "name",
    "label",
    "description",
    "evi:annotatedBy",
  ];

  const properties: Record<string, any> = {};
  if (!entityData) return properties;

  for (const key in entityData) {
    if (!excludeKeys.includes(key) && !key.startsWith("_")) {
      properties[key] = entityData[key];
    }
  }
  return properties;
}

export function createEvidenceNode(
  entityData: RawGraphEntity,
  visibleNodes: Set<string>,
  dataService: GraphDataService,
): EvidenceNode {
  const id = entityData["@id"];
  const type = getEntityType(entityData["@type"]);
  const label = entityData.name || entityData.label || entityData["@id"];
  const displayName = abbreviateName(label);
  const description = entityData.description || "";

  const relationships = dataService.getAllRelationships(id);
  const allRelated = [
    ...relationships.generatedBy,
    ...relationships.usedDataset,
    ...relationships.usedSoftware,
    ...relationships.usedSample,
    ...relationships.usedInstrument,
    ...relationships.usedMLModel,
    ...relationships.hasOutputs,
    ...relationships.createdBy,
  ];

  const visibleRelatedCount = allRelated.filter((node) =>
    visibleNodes.has(node["@id"]),
  ).length;

  const isExpandable = allRelated.length > visibleRelatedCount;

  // Look up annotation if this is a Computation
  let annotation = undefined;
  if (type === "Computation") {
    const ann = dataService.getAnnotationFor(id);
    if (ann) annotation = ann;
  }

  const nodeData: EvidenceNodeData = {
    id,
    type,
    label,
    displayName,
    description,
    expandable: isExpandable,
    _sourceData: entityData,
    properties: getDisplayableProperties(entityData),
    _expanded: !isExpandable,
    _annotation: annotation,
  };

  return {
    id,
    type: "evidenceNode",
    position: { x: 0, y: 0 },
    data: nodeData,
  };
}

export function createEdge(
  sourceId: string,
  targetId: string,
  relationshipType: string,
): EvidenceEdge {
  // fairscape-artifacts: the two everyday edges carry no label; the
  // layout runs producers-left, consumers-right, which says the same.
  const labelMap: { [key: string]: string } = {
    generatedBy: "",
    usedDataset: "",
    usedSoftware: "used software",
    usedSample: "used sample",
    usedInstrument: "used instrument",
    usedMLModel: "used model",
    hasOutputs: "has outputs",
    contains: "contains",
    createdBy: "created by",
  };

  const label = relationshipType in labelMap ? labelMap[relationshipType] : relationshipType;
  const edgeId = `${sourceId}_${relationshipType}_${targetId}`;

  const edge: EvidenceEdge = {
    id: edgeId,
    source: sourceId,
    target: targetId,
    type: "smoothstep",
    label,
  };

  if (relationshipType === "contains") {
    edge.animated = true;
  }

  return edge;
}

export interface GraphElements {
  nodes: EvidenceNode[];
  edges: EvidenceEdge[];
}

export class GraphBuilder {
  private dataService: GraphDataService;
  private visibleNodes: Set<string>;
  private nodes: Map<string, EvidenceNode>;
  private edges: Map<string, EvidenceEdge>;

  constructor(dataService: GraphDataService) {
    this.dataService = dataService;
    this.visibleNodes = new Set();
    this.nodes = new Map();
    this.edges = new Map();
  }

  // fairscape-artifacts: `deferSoftware` leaves Software off the first
  // drawing so a whole pipeline fits on screen; clicking a computation
  // brings its software in through expandNode as usual.
  private deferSoftware = false;

  // fairscape-artifacts: the step spine. The first drawing is every
  // computation plus the final outputs; the datasets between two steps
  // are folded into one edge from consumer to producer, labelled with
  // the dataset's name. A dataset that later becomes visible (a click on
  // either step) takes its edges back and the folded edge goes.
  private spineEdges: Map<string, Set<string>> = new Map();
  private producers: Map<string, Set<string>> = new Map();
  private consumers: Map<string, Set<string>> = new Map();

  private indexDataFlow(): void {
    this.producers.clear();
    this.consumers.clear();
    const add = (map: Map<string, Set<string>>, key: string, value: string) => {
      if (!map.has(key)) map.set(key, new Set());
      map.get(key)!.add(value);
    };
    for (const node of this.dataService.getAllNodes()) {
      if (this.dataService.isAnnotation(node)) continue;
      const id = node["@id"];
      const types = Array.isArray(node["@type"]) ? node["@type"] : [node["@type"]];
      const isComp = types.some((t) => typeof t === "string" && t.includes("Computation"));
      if (isComp) {
        for (const d of refIds(node.generated)) add(this.producers, d, id);
        for (const d of refIds(node.usedDataset)) add(this.consumers, d, id);
      }
      for (const c of refIds(node.generatedBy)) add(this.producers, id, c);
    }
  }

  private isComputationId(id: string): boolean {
    const node = this.dataService.getNode(id);
    if (!node || this.dataService.isAnnotation(node)) return false;
    const types = Array.isArray(node["@type"]) ? node["@type"] : [node["@type"]];
    return types.some((t) => typeof t === "string" && t.includes("Computation"));
  }

  buildStepSpine(finals: string[]): GraphElements {
    this.indexDataFlow();
    const steps = this.dataService
      .getAllNodes()
      .map((n) => n["@id"])
      .filter((id) => this.isComputationId(id));
    for (const id of steps) this.addNode(id);
    for (const id of finals) {
      this.addNode(id);
      const node = this.nodes.get(id);
      if (node) node.data.properties._role = "result";
    }
    for (const consumer of steps) {
      const node = this.dataService.getNode(consumer)!;
      for (const dataset of refIds(node.usedDataset)) {
        if (this.visibleNodes.has(dataset)) continue;
        const entity = this.dataService.getNode(dataset);
        const label = abbreviateName(entity?.name || entity?.label || dataset, 22);
        for (const producer of this.producers.get(dataset) || []) {
          if (producer === consumer || !this.visibleNodes.has(producer)) continue;
          const edge = createEdge(consumer, producer, "via");
          edge.id = `spine:${dataset}:${consumer}:${producer}`;
          edge.label = label;
          this.edges.set(edge.id, edge);
          if (!this.spineEdges.has(dataset)) this.spineEdges.set(dataset, new Set());
          this.spineEdges.get(dataset)!.add(edge.id);
        }
      }
    }
    return this.getElements();
  }

  // A dataset that has just appeared: drop the folded edges that stood in
  // for it and wire it to every visible step that made or used it.
  private unfold(datasetId: string): void {
    for (const edgeId of this.spineEdges.get(datasetId) || []) this.edges.delete(edgeId);
    this.spineEdges.delete(datasetId);
    for (const producer of this.producers.get(datasetId) || []) {
      if (!this.visibleNodes.has(producer)) continue;
      const edge = createEdge(datasetId, producer, "generatedBy");
      this.edges.set(edge.id, edge);
    }
    for (const consumer of this.consumers.get(datasetId) || []) {
      if (!this.visibleNodes.has(consumer)) continue;
      const edge = createEdge(consumer, datasetId, "usedDataset");
      this.edges.set(edge.id, edge);
    }
  }

  buildInitialGraph(
    depth: number = 2,
    options: { deferSoftware?: boolean } = {},
  ): GraphElements {
    this.deferSoftware = !!options.deferSoftware;
    const outputNodes = this.dataService.getOutputNodes();
    for (const outputNode of outputNodes) {
      this.addNodeAndRelationships(outputNode["@id"], depth);
    }
    if (this.deferSoftware) {
      for (const node of this.nodes.values()) {
        const software = this.dataService.getRelatedNodes(node.id, "usedSoftware");
        if (software.some((sw) => !this.visibleNodes.has(sw["@id"]))) {
          node.data.expandable = true;
          node.data._expanded = false;
        }
      }
    }
    this.deferSoftware = false;
    return this.getElements();
  }

  expandNode(nodeId: string): GraphElements {
    const nodeToExpand = this.nodes.get(nodeId);
    if (!nodeToExpand) return this.getElements();

    if (nodeToExpand.data.type === "DatasetCollection") {
      this._expandCollectionByOne(nodeToExpand);
    } else {
      const relationships = this.dataService.getAllRelationships(nodeId);

      this._processRelationship(
        nodeId,
        relationships.generatedBy,
        "generatedBy",
      );
      this._processRelationship(
        nodeId,
        relationships.usedSoftware,
        "usedSoftware",
      );
      this._processRelationship(nodeId, relationships.usedSample, "usedSample");
      this._processRelationship(
        nodeId,
        relationships.usedInstrument,
        "usedInstrument",
      );
      this._processRelationship(
        nodeId,
        relationships.usedMLModel,
        "usedMLModel",
      );
      this._processRelationship(nodeId, relationships.hasOutputs, "hasOutputs");

      if (relationships.usedDataset.length > COLLECTION_THRESHOLD) {
        this._addDatasetCollection(nodeId, relationships.usedDataset);
      } else {
        this._processRelationship(
          nodeId,
          relationships.usedDataset,
          "usedDataset",
        );
      }

      this._processCreatedByRelationship(nodeId, relationships.createdBy);

      nodeToExpand.data._expanded = true;
      nodeToExpand.data.expandable = false;
    }
    return this.getElements();
  }

  private _expandCollectionByOne(collectionNode: EvidenceNode): void {
    const { _childNodeIds, _visibleChildren = 0 } =
      collectionNode.data.properties;
    if (!_childNodeIds || _visibleChildren >= _childNodeIds.length) {
      collectionNode.data.expandable = false;
      return;
    }

    const nextChildId = _childNodeIds[_visibleChildren];
    this.addNode(nextChildId);
    const edge = createEdge(collectionNode.id, nextChildId, "contains");
    this.edges.set(edge.id, edge);

    const newVisibleCount = _visibleChildren + 1;
    collectionNode.data.properties._visibleChildren = newVisibleCount;
    collectionNode.data.displayName = `${_childNodeIds.length} Used Datasets (${newVisibleCount} shown)`;

    if (newVisibleCount >= _childNodeIds.length) {
      collectionNode.data.expandable = false;
    }
  }

  private _addDatasetCollection(
    parentNodeId: string,
    datasets: RawGraphEntity[],
  ) {
    const collectionId = `${parentNodeId}-dataset-collection`;
    if (this.nodes.has(collectionId)) return;

    const count = datasets.length;
    const childIds = datasets.map((d) => d["@id"]);

    const nodeData: EvidenceNodeData = {
      id: collectionId,
      type: "DatasetCollection",
      label: `Used Datasets Collection`,
      displayName: `${count} Used Datasets`,
      expandable: true,
      _sourceData: {},
      properties: {
        count,
        _childNodeIds: childIds,
        _parentNodeId: parentNodeId,
        _visibleChildren: 0,
      },
    };

    const collectionNode: EvidenceNode = {
      id: collectionId,
      type: "evidenceNode",
      position: { x: 0, y: 0 },
      data: nodeData,
    };

    this.nodes.set(collectionId, collectionNode);
    this.visibleNodes.add(collectionId);

    const edge = createEdge(parentNodeId, collectionId, "usedDataset");
    this.edges.set(edge.id, edge);
  }

  private addNode(nodeId: string): void {
    if (this.visibleNodes.has(nodeId)) return;
    const nodeEntity = this.dataService.getNode(nodeId);
    if (!nodeEntity) return;

    // Skip annotation entities from the graph
    if (this.dataService.isAnnotation(nodeEntity)) return;

    const node = createEvidenceNode(
      nodeEntity,
      this.visibleNodes,
      this.dataService,
    );
    this.nodes.set(nodeId, node);
    this.visibleNodes.add(nodeId);
    if (this.spineEdges.size || this.producers.size) this.unfold(nodeId);
  }

  private _processRelationship(
    sourceId: string,
    relatedNodes: RawGraphEntity[],
    type: string,
    depth?: number,
  ) {
    for (const relNode of relatedNodes) {
      // Skip annotation entities
      if (this.dataService.isAnnotation(relNode)) continue;

      if (depth) {
        this.addNodeAndRelationships(relNode["@id"], depth - 1);
      } else {
        this.addNode(relNode["@id"]);
      }
      const edge = createEdge(sourceId, relNode["@id"], type);
      this.edges.set(edge.id, edge);
    }
  }

  private _processCreatedByRelationship(
    sourceId: string,
    createdByNodes: RawGraphEntity[],
  ) {
    for (const personEntity of createdByNodes) {
      const personId = personEntity["@id"];
      if (this.visibleNodes.has(personId)) {
        const edge = createEdge(sourceId, personId, "createdBy");
        this.edges.set(edge.id, edge);
        continue;
      }

      const type = getEntityType(personEntity["@type"]);
      const label = personEntity.name || personEntity["@id"];
      const displayName = abbreviateName(label);

      const nodeData: EvidenceNodeData = {
        id: personId,
        type,
        label,
        displayName,
        description: personEntity.description || "",
        expandable: false,
        _sourceData: personEntity,
        properties: getDisplayableProperties(personEntity),
        _expanded: true,
      };

      this.nodes.set(personId, {
        id: personId,
        type: "evidenceNode",
        position: { x: 0, y: 0 },
        data: nodeData,
      });
      this.visibleNodes.add(personId);

      const edge = createEdge(sourceId, personId, "createdBy");
      this.edges.set(edge.id, edge);
    }
  }

  private addNodeAndRelationships(nodeId: string, depth: number): void {
    if (depth <= 0 || this.visibleNodes.has(nodeId)) return;

    const nodeEntity = this.dataService.getNode(nodeId);
    if (nodeEntity && this.dataService.isAnnotation(nodeEntity)) return;

    this.addNode(nodeId);
    if (depth <= 1) return;

    const relationships = this.dataService.getAllRelationships(nodeId);

    this._processRelationship(
      nodeId,
      relationships.generatedBy,
      "generatedBy",
      depth,
    );
    if (!this.deferSoftware) {
      this._processRelationship(
        nodeId,
        relationships.usedSoftware,
        "usedSoftware",
        depth,
      );
    }
    this._processRelationship(
      nodeId,
      relationships.usedSample,
      "usedSample",
      depth,
    );
    this._processRelationship(
      nodeId,
      relationships.usedInstrument,
      "usedInstrument",
      depth,
    );
    this._processRelationship(
      nodeId,
      relationships.usedMLModel,
      "usedMLModel",
      depth,
    );
    this._processRelationship(
      nodeId,
      relationships.hasOutputs,
      "hasOutputs",
      depth,
    );

    if (relationships.usedDataset.length > COLLECTION_THRESHOLD) {
      this._addDatasetCollection(nodeId, relationships.usedDataset);
    } else {
      this._processRelationship(
        nodeId,
        relationships.usedDataset,
        "usedDataset",
        depth,
      );
    }

    this._processCreatedByRelationship(nodeId, relationships.createdBy);
  }

  getElements(): GraphElements {
    return {
      nodes: Array.from(this.nodes.values()),
      edges: Array.from(this.edges.values()),
    };
  }
}
