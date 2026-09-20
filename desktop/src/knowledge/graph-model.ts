export const DEFAULT_FOCUS_HOPS = 2;
export const MAX_FOCUS_HOPS = 3;
export const FOCUS_NEIGHBOR_LIMIT = 10;
export const ON_SCREEN_NODE_CAP = 24;

export interface KnowledgeEvidence {
  kind: "seed" | "ledger";
  sourceEventIds: string[];
  chatId: string;
  confirmedAt: string | null;
  retracted: boolean;
}

export interface KnowledgeNode {
  id: string;
  label: string;
  category: string;
  importance: number;
  updatedAt: number;
  evidence: KnowledgeEvidence;
}

export interface KnowledgeEdge {
  source: string;
  relation: string;
  target: string;
  context: string;
  weight: number;
  roomId: string;
  validFrom: string;
  validTo: string;
  evidenceMessageId: string;
  evidence: KnowledgeEvidence;
}

export interface KnowledgeGraph {
  nodes: KnowledgeNode[];
  edges: KnowledgeEdge[];
}

export interface KnowledgeView extends KnowledgeGraph {
  focusId: string | null;
  hops: number;
}

const EMPTY_EVIDENCE: KnowledgeEvidence = Object.freeze({
  kind: "seed",
  sourceEventIds: [],
  chatId: "",
  confirmedAt: null,
  retracted: false,
});

function objectValue(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function stringValue(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function numberValue(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

function parseEvidence(value: unknown): KnowledgeEvidence {
  const item = objectValue(value);
  if (!item) return { ...EMPTY_EVIDENCE, sourceEventIds: [] };
  const ids = Array.isArray(item.source_event_ids)
    ? item.source_event_ids.filter((entry): entry is string => typeof entry === "string").slice(0, 16)
    : [];
  return {
    kind: item.kind === "ledger" ? "ledger" : "seed",
    sourceEventIds: ids,
    chatId: stringValue(item.chat_id),
    confirmedAt: typeof item.confirmed_at === "string" ? item.confirmed_at : null,
    retracted: item.retracted === true,
  };
}

export function parseKnowledgeGraph(payload: Record<string, unknown> | null): KnowledgeGraph {
  const nodes = Array.isArray(payload?.nodes)
    ? payload.nodes.flatMap((raw): KnowledgeNode[] => {
      const item = objectValue(raw);
      if (!item) return [];
      const id = stringValue(item.id);
      const label = stringValue(item.label);
      if (!id || !label || id.startsWith("message:") || id.startsWith("msg:")) return [];
      return [{
        id,
        label,
        category: stringValue(item.category),
        importance: Math.max(0, Math.min(100, numberValue(item.importance))),
        updatedAt: Math.max(0, numberValue(item.updated_at)),
        evidence: parseEvidence(item.evidence),
      }];
    })
    : [];
  const nodeIds = new Set(nodes.map((node) => node.id));
  const edges = Array.isArray(payload?.edges)
    ? payload.edges.flatMap((raw): KnowledgeEdge[] => {
      const item = objectValue(raw);
      if (!item) return [];
      const source = stringValue(item.source);
      const target = stringValue(item.target);
      if (!nodeIds.has(source) || !nodeIds.has(target)) return [];
      return [{
        source,
        relation: stringValue(item.relation),
        target,
        context: stringValue(item.context),
        weight: Math.max(0, numberValue(item.weight)),
        roomId: stringValue(item.room_id),
        validFrom: stringValue(item.valid_from),
        validTo: stringValue(item.valid_to),
        evidenceMessageId: stringValue(item.evidence_message_id),
        evidence: parseEvidence(item.evidence),
      }];
    })
    : [];
  return { nodes, edges };
}

function subgraph(graph: KnowledgeGraph, ids: string[], focusId: string | null, hops: number): KnowledgeView {
  const keep = new Set(ids.slice(0, ON_SCREEN_NODE_CAP));
  return {
    nodes: graph.nodes.filter((node) => keep.has(node.id)),
    edges: graph.edges.filter((edge) => keep.has(edge.source) && keep.has(edge.target)),
    focusId,
    hops,
  };
}

export function overviewGraph(graph: KnowledgeGraph): KnowledgeView {
  const ids = [...graph.nodes]
    .sort((left, right) => right.importance - left.importance || left.id.localeCompare(right.id))
    .slice(0, ON_SCREEN_NODE_CAP)
    .map((node) => node.id);
  return subgraph(graph, ids, null, 0);
}

export function kHopNodeIds(
  graph: KnowledgeGraph,
  rootId: string,
  hops: number,
  neighborLimit = FOCUS_NEIGHBOR_LIMIT,
  nodeCap = ON_SCREEN_NODE_CAP,
): string[] {
  if (!graph.nodes.some((node) => node.id === rootId)) return [];
  const boundedHops = Math.min(MAX_FOCUS_HOPS, Math.max(0, Math.floor(hops)));
  const cap = Math.max(1, Math.floor(nodeCap));
  const ordered = [rootId];
  const visited = new Set(ordered);
  let frontier = new Set(ordered);

  for (let hop = 0; hop < boundedHops && ordered.length < cap; hop += 1) {
    const strongest = new Map<string, number>();
    for (const edge of graph.edges) {
      let other = "";
      if (frontier.has(edge.source)) other = edge.target;
      else if (frontier.has(edge.target)) other = edge.source;
      if (!other || visited.has(other)) continue;
      strongest.set(other, Math.max(strongest.get(other) ?? 0, edge.weight));
    }
    const next = [...strongest.entries()]
      .sort((left, right) => right[1] - left[1] || left[0].localeCompare(right[0]))
      .slice(0, Math.max(1, neighborLimit))
      .map(([id]) => id)
      .slice(0, cap - ordered.length);
    if (next.length === 0) break;
    next.forEach((id) => {
      visited.add(id);
      ordered.push(id);
    });
    frontier = new Set(next);
  }
  return ordered;
}

export class KnowledgeDrilldown {
  private focusId: string | null = null;
  private hops = 0;

  constructor(private readonly graph: KnowledgeGraph) {}

  current(): KnowledgeView {
    if (!this.focusId) return overviewGraph(this.graph);
    return subgraph(
      this.graph,
      kHopNodeIds(this.graph, this.focusId, this.hops),
      this.focusId,
      this.hops,
    );
  }

  clickNode(nodeId: string): KnowledgeView {
    if (!this.graph.nodes.some((node) => node.id === nodeId)) return this.current();
    this.focusId = nodeId;
    this.hops = DEFAULT_FOCUS_HOPS;
    return this.current();
  }

  expandOneHop(): KnowledgeView {
    if (this.focusId) this.hops = Math.min(MAX_FOCUS_HOPS, Math.max(DEFAULT_FOCUS_HOPS, this.hops + 1));
    return this.current();
  }

  reset(): KnowledgeView {
    this.focusId = null;
    this.hops = 0;
    return this.current();
  }
}
