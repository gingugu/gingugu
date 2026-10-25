// Namespace "lobes": where each namespace's memories gather in 3D space.
// The shared core namespace sits in the middle, the viewer's persona on an
// inner shell, every project on an outer Fibonacci sphere.

export interface Vec3 {
  x: number
  y: number
  z: number
}

export interface LobeNamespace {
  id: string
  name: string
  count: number
}

export interface LobeOptions {
  core?: string
  persona?: string | null
  inner?: number
  outer?: number
}

export function lobeAnchors(
  namespaces: LobeNamespace[],
  { core = 'crow', persona = null, inner = 120, outer = 320 }: LobeOptions = {},
): Map<string, Vec3> {
  const anchors = new Map<string, Vec3>()
  const rest: LobeNamespace[] = []
  for (const ns of namespaces) {
    if (ns.name === core) anchors.set(ns.id, { x: 0, y: 0, z: 0 })
    else if (persona && ns.name === persona) anchors.set(ns.id, { x: 0, y: inner, z: 0 })
    else rest.push(ns)
  }
  // Biggest first, name as tiebreak: the layout never depends on arrival order.
  rest.sort((a, b) => b.count - a.count || a.name.localeCompare(b.name))
  const golden = Math.PI * (3 - Math.sqrt(5))
  rest.forEach((ns, i) => {
    const y = rest.length === 1 ? 0 : 1 - (2 * i) / (rest.length - 1)
    const ring = Math.sqrt(Math.max(0, 1 - y * y))
    const theta = golden * i
    anchors.set(ns.id, {
      x: Math.cos(theta) * ring * outer,
      y: y * outer,
      z: Math.sin(theta) * ring * outer,
    })
  })
  return anchors
}

interface LobeBody extends Partial<Vec3> {
  nsId?: string
  vx?: number
  vy?: number
  vz?: number
}

export interface LobeForce {
  (alpha: number): void
  initialize: (nodes: LobeBody[]) => void
  strength: (s: number) => LobeForce
}

export function lobeForce(anchors: Map<string, Vec3>, strength: number): LobeForce {
  let nodes: LobeBody[] = []
  let k = strength
  const force = ((alpha: number) => {
    for (const n of nodes) {
      const a = n.nsId === undefined ? undefined : anchors.get(n.nsId)
      if (!a) continue
      n.vx = (n.vx ?? 0) + (a.x - (n.x ?? 0)) * k * alpha
      n.vy = (n.vy ?? 0) + (a.y - (n.y ?? 0)) * k * alpha
      n.vz = (n.vz ?? 0) + (a.z - (n.z ?? 0)) * k * alpha
    }
  }) as LobeForce
  force.initialize = (list) => {
    nodes = list
  }
  force.strength = (s) => {
    k = s
    return force
  }
  return force
}
