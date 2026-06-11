import { MemoryType, RelationType } from '../types'

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

export const CONFIDENCE_COLORS: Record<string, string> = {
  verified: '#22c55e',
  inferred: '#f59e0b',
  stale: '#ef4444',
}

export const NAMESPACE_COLORS: Record<string, string> = {
  gingugu: '#a855f7',
  'orbit-tracker': '#3b82f6',
  'recipe-bot': '#22c55e',
  default: '#6b7280',
}

export const RELATION_COLORS: Record<RelationType, string> = {
  supersedes: '#f59e0b',
  related_to: '#6b7280',
  caused_by: '#ef4444',
  contradicts: '#f43f5e',
  parent_of: '#14b8a6',
  child_of: '#14b8a6',
}

export function getNamespaceColor(name: string): string {
  return NAMESPACE_COLORS[name] ?? NAMESPACE_COLORS.default
}
