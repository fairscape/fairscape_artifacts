import {
  RawGraphData,
  RawGraphEntity,
  AnnotationData,
} from "../types/graph";

export class GraphDataService {
  private graphDict: { [arkId: string]: RawGraphEntity };
  private outputs: Array<{ "@id": string }>;

  constructor(rawData: RawGraphData) {
    this.graphDict = rawData["@graph"] || {};

    // Extract outputs from evi:outputs on the ROCrate root entity
    this.outputs = rawData.outputs || [];
    if (this.outputs.length === 0) {
      // Find the ROCrate root and use its evi:outputs
      for (const entity of Object.values(this.graphDict)) {
        const types = Array.isArray(entity["@type"])
          ? entity["@type"]
          : [entity["@type"]];
        if (types.some((t) => t && t.includes("ROCrate"))) {
          this.outputs = entity["evi:outputs"] || [];
          break;
        }
      }
    }

    // Fallback: scan hasPart from the end, find first Dataset
    if (this.outputs.length === 0) {
      for (const entity of Object.values(this.graphDict)) {
        const types = Array.isArray(entity["@type"])
          ? entity["@type"]
          : [entity["@type"]];
        if (types.some((t) => t && t.includes("ROCrate"))) {
          const hasPart = entity["hasPart"];
          if (Array.isArray(hasPart)) {
            for (let i = hasPart.length - 1; i >= 0; i--) {
              const partRef = hasPart[i];
              const partId =
                typeof partRef === "string" ? partRef : partRef?.["@id"];
              if (!partId) continue;
              const partEntity = this.graphDict[partId];
              if (!partEntity) continue;
              const partTypes = Array.isArray(partEntity["@type"])
                ? partEntity["@type"]
                : [partEntity["@type"]];
              if (partTypes.some((t) => t && t.includes("Dataset"))) {
                this.outputs = [{ "@id": partId }];
                break;
              }
            }
          }
          break;
        }
      }
    }
  }

  getNode(id: string): RawGraphEntity | null {
    return this.graphDict[id] || null;
  }

  getOutputNodes(): RawGraphEntity[] {
    return this.outputs
      .map((ref) => this.getNode(ref["@id"]))
      .filter((node): node is RawGraphEntity => node !== null);
  }

  getAllNodes(): RawGraphEntity[] {
    return Object.values(this.graphDict);
  }

  getNodesByType(type: string): RawGraphEntity[] {
    return Object.values(this.graphDict).filter((node) => {
      const nodeType = Array.isArray(node["@type"])
        ? node["@type"]
        : [node["@type"]];
      return nodeType.some((t) => t && t.includes(type));
    });
  }

  resolveReference(
    ref: { "@id": string } | string | undefined,
  ): RawGraphEntity | null {
    if (!ref) return null;
    const id = typeof ref === "string" ? ref : ref["@id"];
    return this.getNode(id);
  }

  resolveReferences(
    refs: { "@id": string } | Array<{ "@id": string }> | undefined,
  ): RawGraphEntity[] {
    if (!refs) return [];
    const refsArray = Array.isArray(refs) ? refs : [refs];
    return refsArray
      .map((ref) => this.resolveReference(ref))
      .filter((node): node is RawGraphEntity => node !== null);
  }

  getRelatedNodes(
    nodeId: string,
    relationshipType: keyof RawGraphEntity,
  ): RawGraphEntity[] {
    const node = this.getNode(nodeId);
    if (!node) return [];
    const refs = node[relationshipType];
    if (!refs) return [];
    return this.resolveReferences(refs as any);
  }

  getAllRelationships(nodeId: string): {
    generatedBy: RawGraphEntity[];
    usedDataset: RawGraphEntity[];
    usedSoftware: RawGraphEntity[];
    usedSample: RawGraphEntity[];
    usedInstrument: RawGraphEntity[];
    usedMLModel: RawGraphEntity[];
    hasOutputs: RawGraphEntity[];
    createdBy: RawGraphEntity[];
  } {
    return {
      generatedBy: this.getRelatedNodes(nodeId, "generatedBy"),
      usedDataset: this.getRelatedNodes(nodeId, "usedDataset"),
      usedSoftware: this.getRelatedNodes(nodeId, "usedSoftware"),
      usedSample: this.getRelatedNodes(nodeId, "usedSample"),
      usedInstrument: this.getRelatedNodes(nodeId, "usedInstrument"),
      usedMLModel: this.getRelatedNodes(nodeId, "usedMLModel"),
      hasOutputs: this.getRelatedNodes(nodeId, "hasOutputs"),
      createdBy: this.getCreatedByNodes(nodeId),
    };
  }

  getCreatedByNodes(nodeId: string): RawGraphEntity[] {
    const node = this.getNode(nodeId);
    if (!node || !node.createdBy) return [];

    const createdByRefs = Array.isArray(node.createdBy)
      ? node.createdBy
      : [node.createdBy];

    const results: RawGraphEntity[] = [];
    for (const ref of createdByRefs) {
      if (typeof ref === "string") {
        const personId = `person:${ref}`;
        const existingNode = this.getNode(personId);
        if (existingNode) {
          results.push(existingNode);
        } else {
          results.push({
            "@id": personId,
            "@type": "Person",
            name: ref,
          });
        }
      } else if (ref && ref["@id"]) {
        const resolved = this.resolveReference(ref);
        if (resolved) results.push(resolved);
      }
    }
    return results;
  }

  /**
   * Get the annotation for a computation node.
   * Checks evi:annotatedBy on the computation, or scans for AnnotatedComputation
   * entities whose evi:annotates points to this computation.
   */
  getAnnotationFor(computationId: string): AnnotationData | null {
    const node = this.getNode(computationId);
    if (!node) return null;

    // Check evi:annotatedBy
    const annotatedBy = node["evi:annotatedBy"];
    if (annotatedBy && Array.isArray(annotatedBy) && annotatedBy.length > 0) {
      const annotationEntity = this.getNode(annotatedBy[0]["@id"]);
      if (annotationEntity)
        return annotationEntity as unknown as AnnotationData;
    }

    return null;
  }

  /**
   * Check if an entity is an AnnotatedComputation (should be hidden from graph)
   */
  isAnnotation(entity: RawGraphEntity): boolean {
    const types = Array.isArray(entity["@type"])
      ? entity["@type"]
      : [entity["@type"]];
    return types.some(
      (t) =>
        t &&
        (t.includes("AnnotatedComputation") ||
          t.includes("AnnotatedEvidenceGraph")),
    );
  }

  findPath(startId: string, targetId: string): string[] | null {
    const visited = new Set<string>();
    const queue: { id: string; path: string[] }[] = [
      { id: startId, path: [startId] },
    ];

    while (queue.length > 0) {
      const { id, path } = queue.shift()!;
      if (id === targetId) return path;
      if (visited.has(id)) continue;
      visited.add(id);

      const node = this.getNode(id);
      if (!node) continue;

      const relationships = [
        "generatedBy",
        "usedDataset",
        "usedSoftware",
        "usedSample",
        "usedInstrument",
        "usedMLModel",
        "hasOutputs",
        "createdBy",
      ];

      for (const rel of relationships) {
        const relatedNodes = this.getRelatedNodes(
          id,
          rel as keyof RawGraphEntity,
        );
        for (const relatedNode of relatedNodes) {
          if (!visited.has(relatedNode["@id"])) {
            queue.push({
              id: relatedNode["@id"],
              path: [...path, relatedNode["@id"]],
            });
          }
        }
      }
    }
    return null;
  }
}
