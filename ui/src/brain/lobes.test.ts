import { describe, expect, it } from 'vitest'
import { lobeAnchors, lobeForce, type Vec3 } from './lobes'

const dist = (a: Vec3, b: Vec3 = { x: 0, y: 0, z: 0 }) =>
  Math.hypot(a.x - b.x, a.y - b.y, a.z - b.z)

const NS = [
  { id: 'c', name: 'crow', count: 800 },
  { id: 'b', name: 'beepboop', count: 400 },
  { id: 'g', name: 'gingugu', count: 600 },
  { id: 'd', name: 'devex', count: 1400 },
  { id: 't', name: 'token-tab', count: 80 },
  { id: 'p', name: 'playtronics', count: 26 },
]

describe('lobeAnchors', () => {
  const anchors = lobeAnchors(NS, { persona: 'beepboop', inner: 120, outer: 320 })

  it('puts the core namespace at the centre', () => {
    expect(dist(anchors.get('c')!)).toBeCloseTo(0)
  })

  it('puts the persona on the inner shell', () => {
    expect(dist(anchors.get('b')!)).toBeCloseTo(120)
  })

  it('puts every project on the outer shell, well apart', () => {
    const outer = ['g', 'd', 't', 'p'].map((id) => anchors.get(id)!)
    for (const a of outer) expect(dist(a)).toBeCloseTo(320)
    for (let i = 0; i < outer.length; i++)
      for (let j = i + 1; j < outer.length; j++)
        expect(dist(outer[i], outer[j])).toBeGreaterThan(320 * 0.5)
  })

  it('is deterministic whatever order the namespaces arrive in', () => {
    const again = lobeAnchors([...NS].reverse(), { persona: 'beepboop', inner: 120, outer: 320 })
    for (const ns of NS) expect(again.get(ns.id)).toEqual(anchors.get(ns.id))
  })

  it('treats the persona as a project when none is set', () => {
    const none = lobeAnchors(NS, { inner: 120, outer: 320 })
    expect(dist(none.get('b')!)).toBeCloseTo(320)
  })

  it('copes with no core namespace at all', () => {
    const solo = lobeAnchors([{ id: 'x', name: 'solo', count: 3 }], { inner: 120, outer: 320 })
    expect(dist(solo.get('x')!)).toBeCloseTo(320)
  })

  it('defaults the core to crow', () => {
    const def = lobeAnchors(NS)
    expect(dist(def.get('c')!)).toBeCloseTo(0)
  })
})

describe('lobeForce', () => {
  it('nudges velocity toward the namespace anchor, scaled by strength and alpha', () => {
    const anchors = new Map<string, Vec3>([['g', { x: 100, y: 0, z: -50 }]])
    const node = { nsId: 'g', x: 0, y: 10, z: 0, vx: 1, vy: 0, vz: 0 }
    const force = lobeForce(anchors, 0.1)
    force.initialize([node])
    force(0.5)
    expect(node.vx).toBeCloseTo(1 + 100 * 0.1 * 0.5)
    expect(node.vy).toBeCloseTo(-10 * 0.1 * 0.5)
    expect(node.vz).toBeCloseTo(-50 * 0.1 * 0.5)
  })

  it('leaves nodes of unknown namespaces alone', () => {
    const node = { nsId: 'zzz', x: 5, y: 5, z: 5, vx: 0, vy: 0, vz: 0 }
    const force = lobeForce(new Map(), 0.1)
    force.initialize([node])
    force(1)
    expect([node.vx, node.vy, node.vz]).toEqual([0, 0, 0])
  })

  it('strength can be changed live', () => {
    const anchors = new Map<string, Vec3>([['g', { x: 10, y: 0, z: 0 }]])
    const node = { nsId: 'g', x: 0, y: 0, z: 0, vx: 0, vy: 0, vz: 0 }
    const force = lobeForce(anchors, 0.1)
    force.initialize([node])
    force.strength(0)
    force(1)
    expect(node.vx).toBe(0)
  })
})
