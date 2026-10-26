// Turns the /graph wire payload into what the views draw.
import type { GraphPayload } from '../api/types'
import { DORMANT_AFTER_DAYS, scoreMemory } from '../lib/decay'
import type { Confidence, MemoryLite, MemoryType, RelationType } from '../types'

export interface BrainNode {
  id: string
  title: string
  nsId: string
  nsName: string
  type: MemoryType
  conf: Confidence
  pinned: boolean
  access: number
  created: string
  updated: string
  accessed: string
  confirmed: string | null
  tags: string[]
  health: number
  dormant: boolean
}

export interface BrainLink {
  source: string
  target: string
  kind: 'relation' | 'tag'
  relType?: RelationType
  weight: number
}

export interface Brain {
  nodes: BrainNode[]
  links: BrainLink[]
  namespaces: GraphPayload['namespaces']
  byId: Map<string, BrainNode>
  titleIndex: Map<string, string>
}

export function toLiteMemories(graph: GraphPayload): MemoryLite[] {
  return graph.nodes.map((n) => ({
    id: n.id,
    namespace_id: n.ns,
    type: n.type,
    title: n.title,
    confidence: n.conf,
    created_at: n.created,
    updated_at: n.updated,
    last_accessed: n.accessed,
    last_confirmed: n.confirmed ?? '',
    access_count: n.access,
    tags: n.tags.map((i) => graph.tags[i]).filter((t) => t !== undefined),
    pinned: n.pinned,
  }))
}

export function toBrain(graph: GraphPayload, now: Date = new Date()): Brain {
  const nsName = new Map(graph.namespaces.map((n) => [n.id, n.name]))
  const lite = toLiteMemories(graph)
  const nodes: BrainNode[] = graph.nodes.map((n, i) => {
    const scored = scoreMemory(lite[i], now)
    return {
      id: n.id,
      title: n.title,
      nsId: n.ns,
      nsName: nsName.get(n.ns) ?? 'unknown',
      type: n.type,
      conf: n.conf,
      pinned: n.pinned,
      access: n.access,
      created: n.created,
      updated: n.updated,
      accessed: n.accessed,
      confirmed: n.confirmed,
      tags: lite[i].tags,
      health: scored.health,
      dormant: scored.daysSinceAccessed >= DORMANT_AFTER_DAYS,
    }
  })
  const byId = new Map(nodes.map((n) => [n.id, n]))

  const titleIndex = new Map<string, string>()
  for (const n of nodes) {
    const key = n.title.trim().toLowerCase()
    const held = titleIndex.get(key)
    if (!held || n.updated > byId.get(held)!.updated) titleIndex.set(key, n.id)
  }

  const links: BrainLink[] = []
  for (const r of graph.relations) {
    if (byId.has(r.s) && byId.has(r.t))
      links.push({ source: r.s, target: r.t, kind: 'relation', relType: r.type, weight: 1 })
  }
  for (const e of graph.tag_edges) {
    if (byId.has(e.s) && byId.has(e.t))
      links.push({ source: e.s, target: e.t, kind: 'tag', weight: e.w })
  }
  return { nodes, links, namespaces: graph.namespaces, byId, titleIndex }
}
