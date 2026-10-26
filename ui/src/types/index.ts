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

// A memory without its body: what the graph payload carries and every chart
// needs. Content is fetched per memory (detail drawer), never in bulk.
export interface MemoryLite extends Omit<Memory, 'content' | 'source' | 'metadata'> {
  pinned: boolean
}

// Just enough of a namespace for grouping and naming.
export interface NamespaceRef {
  id: string
  name: string
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
  | 'capability'

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

export type ViewTab = 'brain' | 'list' | 'dashboard' | 'timeline' | 'heatmap'

export interface DecayScored<M = MemoryLite> {
  memory: M
  health: number
  freshness: number
  access: number
  confidence: number
  daysSinceConfirmed: number
  daysSinceAccessed: number
  dormant: boolean
}
