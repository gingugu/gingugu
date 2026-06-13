import { Confidence, MemoryType, RelationType } from '../types'

export const TYPE_COLORS: Record<MemoryType, string> = {
  fact: '#3b82f6',
  decision: '#a855f7',
  bug: '#ef4444',
  pattern: '#f97316',
  architecture: '#14b8a6',
  context: '#6b7280',
  preference: '#ec4899',
  workflow: '#22c55e',
}

export const CONFIDENCE_COLORS: Record<Confidence, string> = {
  verified: '#22c55e', // green — trusted
  inferred: '#f59e0b', // amber — drawn conclusion
  stale: '#ef4444', // red — legacy value, no longer auto-assigned
  deprecated: '#52525b', // slate — retired via memory_forget
}

// Namespaces are user-defined and unbounded, so colors are derived
// deterministically from the name instead of being hardcoded. Same name →
// same color across sessions; distinct names spread across a vibrant palette.
const NAMESPACE_PALETTE = [
  '#a855f7', // violet
  '#3b82f6', // blue
  '#22c55e', // green
  '#f59e0b', // amber
  '#f43f5e', // rose
  '#06b6d4', // cyan
  '#f97316', // orange
  '#d946ef', // fuchsia
  '#84cc16', // lime
  '#14b8a6', // teal
  '#6366f1', // indigo
  '#ec4899', // pink
  '#0ea5e9', // sky
  '#eab308', // yellow
]

const NAMESPACE_FALLBACK = '#6b7280' // gray — only for empty/unknown names

export const RELATION_COLORS: Record<RelationType, string> = {
  supersedes: '#f59e0b',
  related_to: '#6b7280',
  caused_by: '#ef4444',
  contradicts: '#f43f5e',
  parent_of: '#14b8a6',
  child_of: '#14b8a6',
}

export function getNamespaceColor(name: string): string {
  if (!name) return NAMESPACE_FALLBACK
  // djb2 string hash → stable index into the palette.
  let hash = 5381
  for (let i = 0; i < name.length; i++) {
    hash = ((hash << 5) + hash + name.charCodeAt(i)) | 0
  }
  return NAMESPACE_PALETTE[Math.abs(hash) % NAMESPACE_PALETTE.length]
}
