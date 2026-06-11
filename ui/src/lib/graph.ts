import { Memory, MemoryNamespace, Relation, GraphData, GraphNode, GraphLink } from '../types'
import { TYPE_COLORS, RELATION_COLORS, getNamespaceColor } from './colors'

const MIN_SHARED_TAGS = 2

function buildNamespaceMap(namespaces: MemoryNamespace[]): Map<string, string> {
  const map = new Map<string, string>()
  for (const ns of namespaces) {
    map.set(ns.id, ns.name)
  }
  return map
}

function buildNodes(
  memories: Memory[],
  nsMap: Map<string, string>,
): GraphNode[] {
  return memories.map((m) => ({
    id: m.id,
    name: m.title,
    type: m.type,
    confidence: m.confidence,
    namespace: nsMap.get(m.namespace_id) ?? 'unknown',
    tags: m.tags,
    val: Math.max(3, Math.min(12, m.tags.length * 2 + m.access_count + 3)),
    color: TYPE_COLORS[m.type] ?? '#6b7280',
  }))
}

function buildRelationLinks(relations: Relation[]): GraphLink[] {
  return relations.map((r) => ({
    source: r.source_id,
    target: r.target_id,
    type: 'relation' as const,
    label: r.relation_type,
    color: RELATION_COLORS[r.relation_type] ?? '#6b7280',
  }))
}

function buildTagLinks(memories: Memory[]): GraphLink[] {
  const links: GraphLink[] = []
  const seen = new Set<string>()

  for (let i = 0; i < memories.length; i++) {
    for (let j = i + 1; j < memories.length; j++) {
      const shared = memories[i].tags.filter((t) =>
        memories[j].tags.includes(t),
      )
      if (shared.length >= MIN_SHARED_TAGS) {
        const key = [memories[i].id, memories[j].id].sort().join('-')
        if (!seen.has(key)) {
          seen.add(key)
          links.push({
            source: memories[i].id,
            target: memories[j].id,
            type: 'shared_tags',
            label: `${shared.length} shared tags`,
            color: 'rgba(107, 114, 128, 0.2)',
          })
        }
      }
    }
  }
  return links
}

export function buildGraphData(
  memories: Memory[],
  namespaces: MemoryNamespace[],
  relations: Relation[],
  showTagLinks: boolean,
): GraphData {
  const nsMap = buildNamespaceMap(namespaces)
  const nodes = buildNodes(memories, nsMap)
  const relationLinks = buildRelationLinks(relations)
  const tagLinks = showTagLinks ? buildTagLinks(memories) : []

  return {
    nodes,
    links: [...relationLinks, ...tagLinks],
  }
}

export function getTagFrequencies(memories: Memory[]): { tag: string; count: number }[] {
  const freq = new Map<string, number>()
  for (const m of memories) {
    for (const t of m.tags) {
      freq.set(t, (freq.get(t) ?? 0) + 1)
    }
  }
  return Array.from(freq.entries())
    .map(([tag, count]) => ({ tag, count }))
    .sort((a, b) => b.count - a.count)
}

export function getTypeDistribution(memories: Memory[]) {
  const dist = new Map<string, number>()
  for (const m of memories) {
    dist.set(m.type, (dist.get(m.type) ?? 0) + 1)
  }
  return Array.from(dist.entries())
    .map(([type, count]) => ({ type, count }))
    .sort((a, b) => b.count - a.count)
}

export function getNamespaceDistribution(
  memories: Memory[],
  namespaces: MemoryNamespace[],
) {
  const nsMap = buildNamespaceMap(namespaces)
  const dist = new Map<string, number>()
  for (const m of memories) {
    const name = nsMap.get(m.namespace_id) ?? 'unknown'
    dist.set(name, (dist.get(name) ?? 0) + 1)
  }
  return Array.from(dist.entries()).map(([name, count]) => ({
    name,
    count,
    color: getNamespaceColor(name),
  }))
}

export function getConfidenceDistribution(memories: Memory[]) {
  const dist = new Map<string, number>()
  for (const m of memories) {
    dist.set(m.confidence, (dist.get(m.confidence) ?? 0) + 1)
  }
  return Array.from(dist.entries()).map(([confidence, count]) => ({
    confidence,
    count,
  }))
}

export function getTimeline(memories: Memory[]) {
  const byDate = new Map<string, number>()
  for (const m of memories) {
    const date = m.created_at.split('T')[0]
    byDate.set(date, (byDate.get(date) ?? 0) + 1)
  }
  return Array.from(byDate.entries())
    .map(([date, count]) => ({ date, count }))
    .sort((a, b) => a.date.localeCompare(b.date))
}
