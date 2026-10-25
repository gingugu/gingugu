// Filter + sort for the List view. Pure: never mutates its input.
import type { BrainNode } from '../brain/model'

export type SortKey =
  | 'title'
  | 'namespace'
  | 'type'
  | 'confidence'
  | 'access'
  | 'updated'
  | 'health'

export interface TableQuery {
  text: string
  sort: SortKey
  dir: 'asc' | 'desc'
}

function matches(n: BrainNode, words: string[]): boolean {
  // Title and namespace match by substring; a tag must match whole, or "stack"
  // would drag in everything tagged "stacking".
  const hay = [n.title, n.nsName].map((s) => s.toLowerCase())
  const tags = n.tags.map((t) => t.toLowerCase())
  return words.every((w) => hay.some((h) => h.includes(w)) || tags.includes(w))
}

const CONF_RANK: Record<string, number> = { deprecated: 0, stale: 1, inferred: 2, verified: 3 }

function compare(a: BrainNode, b: BrainNode, sort: SortKey): number {
  switch (sort) {
    case 'namespace':
      return a.nsName.localeCompare(b.nsName) || a.title.localeCompare(b.title)
    case 'type':
      return a.type.localeCompare(b.type)
    case 'confidence':
      return (CONF_RANK[a.conf] ?? 0) - (CONF_RANK[b.conf] ?? 0)
    case 'access':
      return a.access - b.access
    case 'updated':
      return a.updated.localeCompare(b.updated)
    case 'health':
      return a.health - b.health
    default:
      return a.title.localeCompare(b.title)
  }
}

export function filterSortNodes(nodes: BrainNode[], q: TableQuery): BrainNode[] {
  const words = q.text.toLowerCase().split(/\s+/).filter(Boolean)
  const kept = words.length ? nodes.filter((n) => matches(n, words)) : nodes.slice()
  const sign = q.dir === 'asc' ? 1 : -1
  // Array.sort is stable; ties keep input order whichever way we flip.
  return kept.sort((a, b) => sign * compare(a, b, q.sort))
}
