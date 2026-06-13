export interface MemoryNamespace {
  id: string
  name: string
  path: string | null
  description: string | null
  created_at: string
  updated_at: string
}

export interface Memory {
  id: string
  namespace_id: string
  type: MemoryType
  title: string
  content: string
  confidence: Confidence
  source: string | null
  created_at: string
  updated_at: string
  last_accessed: string
  last_confirmed: string
  access_count: number
  metadata: string | null
  tags: string[]
}

export interface Relation {
  source_id: string
  target_id: string
  relation_type: RelationType
  created_at: string
}

export interface MemoryExport {
  format_version: number
  exported_at: string
  namespaces: MemoryNamespace[]
  memories: Memory[]
  relations: Relation[]
}

export type MemoryType =
  | 'fact'
  | 'decision'
  | 'pattern'
  | 'bug'
  | 'architecture'
  | 'preference'
  | 'workflow'
  | 'context'

// Mirrors the backend Confidence enum (src/gingugu/models.py). `stale` is a
// legacy value (no longer auto-assigned under the never-forget model) but is
// kept so existing data renders; `deprecated` is set by memory_forget.
export type Confidence = 'verified' | 'inferred' | 'stale' | 'deprecated'

export type RelationType =
  | 'supersedes'
  | 'related_to'
  | 'caused_by'
  | 'contradicts'
  | 'parent_of'
  | 'child_of'

export interface GraphNode {
  id: string
  name: string
  type: MemoryType
  confidence: string
  namespace: string
  tags: string[]
  val: number
  color: string
}

export interface GraphLink {
  source: string
  target: string
  type: 'relation' | 'shared_tags'
  label?: string
  color: string
}

export interface GraphData {
  nodes: GraphNode[]
  links: GraphLink[]
}

export type ViewTab = 'graph' | 'dashboard' | 'timeline' | 'heatmap'

export interface GraphFilters {
  text: string
  types: Set<MemoryType>
  namespaces: Set<string>
  confidences: Set<Confidence>
}

export interface GraphLayoutSettings {
  nodeSizeMultiplier: number
  linkDistance: number
  chargeStrength: number
}

export interface DecayScored {
  memory: Memory
  health: number
  freshness: number
  access: number
  confidence: number
  daysSinceConfirmed: number
  daysSinceAccessed: number
  dormant: boolean
}
