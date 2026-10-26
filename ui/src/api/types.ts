// Wire types for the Explorer read API (src/gingugu/ui_api/*).
import type { Confidence, MemoryType, RelationType } from '../types'

export interface ApiNamespace {
  id: string
  name: string
  count: number
}

export interface ApiNode {
  id: string
  ns: string
  type: MemoryType
  conf: Confidence
  title: string
  pinned: boolean
  access: number
  created: string
  updated: string
  accessed: string
  confirmed: string | null
  tags: number[]
}

export interface ApiRelation {
  s: string
  t: string
  type: RelationType
  created: string
}

export interface ApiTagEdge {
  s: string
  t: string
  w: number
}

export interface GraphPayload {
  ok: boolean
  version: string
  namespaces: ApiNamespace[]
  tags: string[]
  nodes: ApiNode[]
  relations: ApiRelation[]
  tag_edges: ApiTagEdge[]
}

export type SourceMode = 'local' | 'remote' | 'static'

export interface InfoPayload {
  ok: boolean
  version: string
  schema_version: number
  memories: number
  relations: number
  graph_version: string
  mode: SourceMode
  brain: string
  persona: string | null
}

export interface DetailMemory {
  id: string
  namespace_id: string
  namespace: string
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
  pinned: boolean
  provenance: string | null
  about: string | null
  tags: string[]
}

export interface LinkedMemory {
  id: string
  type: RelationType
  title: string
}

export interface ClaimRow {
  kind: string
  ref: string
  state: string
  resolved_state: string | null
  resolved_at: string | null
}

export interface TripwireRow {
  id: string
  tool_pattern: string
  input_pattern: string | null
}

export interface MemoryDetailPayload {
  ok: boolean
  memory: DetailMemory
  relations: { out: LinkedMemory[]; in: LinkedMemory[] }
  claims: ClaimRow[]
  tripwires: TripwireRow[]
  accesses: string[]
}

export interface SearchHit {
  id: string
  title: string
  namespace: string
  type: MemoryType
  confidence: Confidence
  score: number | null
  score_breakdown: Record<string, number>
}

export interface SearchPayload {
  ok: boolean
  count: number
  hits: SearchHit[]
}

export interface StatsPayload {
  ok: boolean
  stats: Record<string, unknown>
}

export interface ExplorerEvent {
  kind: string
  at: string
  [key: string]: unknown
}

export interface EventsPayload {
  ok: boolean
  events: ExplorerEvent[]
  cursor: string | null
}
