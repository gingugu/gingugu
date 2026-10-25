import { describe, expect, it } from 'vitest'
import { GRAPH, NOW } from '../test/fixtures'
import { toBrain, toLiteMemories } from './model'

describe('toBrain', () => {
  const brain = toBrain(GRAPH, NOW)

  it('resolves namespace names and tag names', () => {
    const rule = brain.byId.get('m-rule')!
    expect(rule.nsName).toBe('crow')
    expect(rule.tags).toEqual(['git', 'stacking'])
  })

  it('scores health in [0, 1] and flags dormancy at 90 days untouched', () => {
    for (const n of brain.nodes) {
      expect(n.health).toBeGreaterThanOrEqual(0)
      expect(n.health).toBeLessThanOrEqual(1)
    }
    expect(brain.byId.get('m-schema')!.dormant).toBe(true) // last accessed June
    expect(brain.byId.get('m-rule')!.dormant).toBe(false)
    // Deprecated trusts least.
    expect(brain.byId.get('m-old')!.health).toBeLessThan(brain.byId.get('m-rule')!.health)
  })

  it('builds relation and tag links, dropping edges to unknown nodes', () => {
    const rel = brain.links.filter((l) => l.kind === 'relation')
    const tag = brain.links.filter((l) => l.kind === 'tag')
    expect(rel).toHaveLength(2) // the m-gone edge is dropped
    expect(rel.find((l) => l.source === 'm-lesson')!.relType).toBe('caused_by')
    expect(tag).toHaveLength(2)
    expect(tag.find((l) => l.source === 'm-lesson')!.weight).toBe(3.2)
  })

  it('indexes titles case-insensitively for [[links]]', () => {
    expect(brain.titleIndex.get('never delete a stack parent branch')).toBe('m-rule')
  })

  it('keeps the newest memory when two share a title', () => {
    const dup = structuredClone(GRAPH)
    dup.nodes.push({ ...dup.nodes[1], id: 'm-lesson-2', updated: '2026-10-24T00:00:00+00:00' })
    expect(toBrain(dup, NOW).titleIndex.get('retarget child prs before merging')).toBe('m-lesson-2')
  })

  it('carries the namespaces through', () => {
    expect(brain.namespaces.map((n) => n.name)).toEqual(['crow', 'gingugu'])
  })
})

describe('toLiteMemories', () => {
  it('maps nodes to the body-less memory shape the charts use', () => {
    const lite = toLiteMemories(GRAPH)
    expect(lite).toHaveLength(5)
    const rule = lite.find((m) => m.id === 'm-rule')!
    expect(rule).toMatchObject({
      namespace_id: 'ns-crow',
      type: 'preference',
      confidence: 'verified',
      title: 'Never delete a stack parent branch',
      access_count: 40,
      created_at: '2026-08-01T00:00:00+00:00',
      updated_at: '2026-10-20T00:00:00+00:00',
      last_accessed: '2026-10-24T00:00:00+00:00',
      last_confirmed: '2026-10-20T00:00:00+00:00',
      tags: ['git', 'stacking'],
      pinned: true,
    })
    expect('content' in rule).toBe(false)
  })
})
