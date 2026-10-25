import { Confidence, MemoryLite, NamespaceRef } from '../types'
import { getNamespaceColor } from './colors'

export function getTagFrequencies(memories: MemoryLite[]): { tag: string; count: number }[] {
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

export function getTypeDistribution(memories: MemoryLite[]) {
  const dist = new Map<string, number>()
  for (const m of memories) {
    dist.set(m.type, (dist.get(m.type) ?? 0) + 1)
  }
  return Array.from(dist.entries())
    .map(([type, count]) => ({ type, count }))
    .sort((a, b) => b.count - a.count)
}

export function getNamespaceDistribution(
  memories: MemoryLite[],
  namespaces: NamespaceRef[],
) {
  const nsMap = new Map(namespaces.map((ns) => [ns.id, ns.name]))
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

export function getConfidenceDistribution(memories: MemoryLite[]) {
  const dist = new Map<Confidence, number>()
  for (const m of memories) {
    dist.set(m.confidence, (dist.get(m.confidence) ?? 0) + 1)
  }
  return Array.from(dist.entries()).map(([confidence, count]) => ({
    confidence,
    count,
  }))
}

export function getTimeline(memories: MemoryLite[]) {
  const byDate = new Map<string, number>()
  for (const m of memories) {
    const date = m.created_at.split('T')[0]
    byDate.set(date, (byDate.get(date) ?? 0) + 1)
  }
  return Array.from(byDate.entries())
    .map(([date, count]) => ({ date, count }))
    .sort((a, b) => a.date.localeCompare(b.date))
}
