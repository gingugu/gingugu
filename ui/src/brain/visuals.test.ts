import { describe, expect, it } from 'vitest'
import { RELATION_COLORS, TYPE_COLORS, getNamespaceColor } from '../lib/colors'
import { healthColor } from '../lib/decay'
import { GRAPH, NOW } from '../test/fixtures'
import { toBrain } from './model'
import { linkStyle, nodeColor, nodeOpacity, nodeSize } from './visuals'

const brain = toBrain(GRAPH, NOW)
const node = (id: string) => brain.byId.get(id)!

describe('nodeSize', () => {
  it('grows with access and stays in [2, 12]', () => {
    const base = { ...node('m-lesson'), pinned: false }
    const sizes = [0, 1, 10, 100, 10_000].map((access) => nodeSize({ ...base, access }))
    for (let i = 1; i < sizes.length; i++) expect(sizes[i]).toBeGreaterThan(sizes[i - 1])
    for (const s of sizes) {
      expect(s).toBeGreaterThanOrEqual(2)
      expect(s).toBeLessThanOrEqual(12)
    }
  })

  it('pins are bigger than the same memory unpinned', () => {
    const n = node('m-lesson')
    expect(nodeSize({ ...n, pinned: true })).toBeGreaterThan(nodeSize({ ...n, pinned: false }))
  })
})

describe('nodeOpacity', () => {
  it('deprecated dimmest, dormant dim, never hidden', () => {
    const live = nodeOpacity(node('m-rule'))
    const dormant = nodeOpacity(node('m-schema'))
    const deprecated = nodeOpacity(node('m-old'))
    expect(deprecated).toBeLessThan(dormant)
    expect(dormant).toBeLessThan(live)
    expect(deprecated).toBeGreaterThan(0)
    expect(dormant).toBeGreaterThanOrEqual(0.25)
  })
})

describe('nodeColor', () => {
  it('colors by type, namespace or trust', () => {
    const n = node('m-rule')
    expect(nodeColor(n, 'type')).toBe(TYPE_COLORS.preference)
    expect(nodeColor(n, 'namespace')).toBe(getNamespaceColor('crow'))
    expect(nodeColor(n, 'trust')).toBe(healthColor(n.health))
  })
})

describe('linkStyle', () => {
  const rel = brain.links.find((l) => l.kind === 'relation' && l.relType === 'caused_by')!
  const strong = brain.links.find((l) => l.kind === 'tag' && l.weight === 3.2)!
  const weak = brain.links.find((l) => l.kind === 'tag' && l.weight === 0.8)!

  it('relations use their type color and are wider than tag edges', () => {
    const r = linkStyle(rel, 3.2)
    expect(r.color).toBe(RELATION_COLORS.caused_by)
    expect(r.width).toBeGreaterThan(linkStyle(strong, 3.2).width)
  })

  it('tag edge opacity scales with weight and stays faint', () => {
    const s = linkStyle(strong, 3.2).opacity
    const w = linkStyle(weak, 3.2).opacity
    expect(w).toBeLessThan(s)
    expect(w).toBeGreaterThan(0)
    expect(s).toBeLessThanOrEqual(0.35)
  })
})
