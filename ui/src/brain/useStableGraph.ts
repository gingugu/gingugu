// Feeds the force graph without resetting it: nodes that survive a refetch keep
// their object (and so their x/y/z and velocity), only their fields update.
import { useMemo, useRef } from 'react'
import type { Brain, BrainLink, BrainNode } from './model'

export type GNode = BrainNode & {
  x?: number
  y?: number
  z?: number
  vx?: number
  vy?: number
  vz?: number
}

export type GLink = Omit<BrainLink, 'source' | 'target'> & {
  source: string | GNode
  target: string | GNode
}

export function endId(end: string | GNode): string {
  return typeof end === 'string' ? end : end.id
}

export function useStableGraph(brain: Brain) {
  const previous = useRef(new Map<string, GNode>())
  return useMemo(() => {
    const nodes = brain.nodes.map((n) => {
      const old = previous.current.get(n.id)
      return old ? Object.assign(old, n) : ({ ...n } as GNode)
    })
    previous.current = new Map(nodes.map((n) => [n.id, n]))
    const links: GLink[] = brain.links.map((l) => ({ ...l }))
    const maxTagWeight = brain.links.reduce(
      (m, l) => (l.kind === 'tag' ? Math.max(m, l.weight) : m),
      0,
    )
    return { data: { nodes, links }, maxTagWeight }
  }, [brain])
}
