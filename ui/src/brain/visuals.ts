// Pure styling rules for nodes and links, kept apart from the three.js code.
import { RELATION_COLORS, TYPE_COLORS, getNamespaceColor } from '../lib/colors'
import { healthColor } from '../lib/decay'
import type { BrainLink, BrainNode } from './model'

export type ColorMode = 'type' | 'namespace' | 'trust'

const MIN_SIZE = 2
const MAX_SIZE = 12

export function nodeSize(n: Pick<BrainNode, 'access' | 'pinned'>): number {
  // log2(1 + 10_000) ~ 13.3, so 10k accesses still sits under the cap.
  const base = MIN_SIZE + Math.log2(1 + Math.max(0, n.access)) * 0.4
  return Math.max(MIN_SIZE, Math.min(MAX_SIZE, n.pinned ? base * 1.5 : base))
}

export function nodeOpacity(n: Pick<BrainNode, 'conf' | 'dormant'>): number {
  if (n.conf === 'deprecated') return 0.2
  if (n.dormant) return 0.35
  return 0.92
}

export function nodeColor(n: BrainNode, mode: ColorMode): string {
  if (mode === 'namespace') return getNamespaceColor(n.nsName)
  if (mode === 'trust') return healthColor(n.health)
  return TYPE_COLORS[n.type] ?? '#6b7280'
}

export interface LinkStyle {
  color: string
  width: number
  opacity: number
}

export function linkStyle(l: BrainLink, maxTagWeight: number): LinkStyle {
  if (l.kind === 'relation' && l.relType) {
    return { color: RELATION_COLORS[l.relType] ?? '#6b7280', width: 1.5, opacity: 0.8 }
  }
  const share = maxTagWeight > 0 ? Math.min(1, l.weight / maxTagWeight) : 0
  return { color: '#94a3b8', width: 0.4, opacity: 0.06 + 0.29 * share }
}
