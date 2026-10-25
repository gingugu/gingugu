// Camera behaviour for the brain: idle auto-rotate, fly-to-node on selection.
import { useCallback, useEffect, useRef, type MutableRefObject } from 'react'
import type { ForceGraphMethods } from 'react-force-graph-3d'
import type { GNode } from './useStableGraph'

type Fg = MutableRefObject<ForceGraphMethods | undefined>

interface OrbitLike {
  autoRotate: boolean
  autoRotateSpeed: number
  addEventListener: (type: string, fn: () => void) => void
  removeEventListener: (type: string, fn: () => void) => void
}

const RESUME_MS = 4000

export function useBrainCamera(
  fgRef: Fg,
  ready: boolean,
  nodes: GNode[],
  selectedId: string | null,
) {
  const selectedRef = useRef(selectedId)
  selectedRef.current = selectedId
  const flown = useRef<string | null>(null)

  // Slow idle spin; the user's hands, or a selection, stop it.
  useEffect(() => {
    const controls = fgRef.current?.controls() as OrbitLike | undefined
    if (!controls || !ready || typeof controls.addEventListener !== 'function') return
    let timer: number | undefined
    controls.autoRotateSpeed = 0.3
    controls.autoRotate = selectedRef.current === null
    const stop = () => {
      controls.autoRotate = false
      window.clearTimeout(timer)
    }
    const resume = () => {
      timer = window.setTimeout(() => {
        controls.autoRotate = selectedRef.current === null
      }, RESUME_MS)
    }
    controls.addEventListener('start', stop)
    controls.addEventListener('end', resume)
    return () => {
      window.clearTimeout(timer)
      controls.removeEventListener('start', stop)
      controls.removeEventListener('end', resume)
    }
  }, [fgRef, ready])

  useEffect(() => {
    const controls = fgRef.current?.controls() as OrbitLike | undefined
    if (controls && ready) controls.autoRotate = selectedId === null
  }, [fgRef, ready, selectedId])

  const flyTo = useCallback(
    (n: GNode) => {
      if (n.x === undefined || n.y === undefined || n.z === undefined) return false
      const dist = Math.hypot(n.x, n.y, n.z)
      const k = 1 + 90 / Math.max(1, dist)
      fgRef.current?.cameraPosition(
        { x: n.x * k, y: n.y * k, z: n.z * k },
        { x: n.x, y: n.y, z: n.z },
        900,
      )
      flown.current = n.id
      return true
    },
    [fgRef],
  )

  // Selection made elsewhere (List view, a link in the drawer): fly there once
  // the node has a position, retrying briefly while the layout settles.
  useEffect(() => {
    if (!selectedId) flown.current = null
    if (!ready || !selectedId || flown.current === selectedId) return
    const node = nodes.find((n) => n.id === selectedId)
    if (!node) return
    let tries = 0
    const timer = window.setInterval(() => {
      if (flyTo(node) || ++tries > 15) window.clearInterval(timer)
    }, 200)
    return () => window.clearInterval(timer)
  }, [ready, selectedId, nodes, flyTo])

  return flyTo
}
