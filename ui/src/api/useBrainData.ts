// One hook owns the brain's data: which source answered, the graph, the info,
// and (live only) a 10s poll that refetches the graph when its version moves.
import { useCallback, useEffect, useRef, useState } from 'react'
import { api, memoryPath, sourceOf, type Source } from './client'
import { loadStatic } from './static'
import type { GraphPayload, InfoPayload, MemoryDetailPayload } from './types'

const POLL_MS = 10_000

export interface BrainData {
  source: Source | null
  info: InfoPayload | null
  graph: GraphPayload | null
  error: string | null
  loading: boolean
  refresh: () => void
  fetchDetail: (id: string) => Promise<MemoryDetailPayload>
}

export function useBrainData(includeDates: boolean): BrainData {
  const [source, setSource] = useState<Source | null>(null)
  const [info, setInfo] = useState<InfoPayload | null>(null)
  const [graph, setGraph] = useState<GraphPayload | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const seenVersion = useRef<string | null>(null)
  const sourceRef = useRef<Source | null>(null)
  const datesRef = useRef(includeDates)
  datesRef.current = includeDates

  const loadGraph = useCallback(async () => {
    const res = await api<GraphPayload>('/graph', { dates: datesRef.current })
    if (res.ok) {
      setGraph(res.body)
      setError(null)
    } else {
      setError(res.error)
    }
  }, [])

  const load = useCallback(
    async (force: boolean) => {
      const res = await api<InfoPayload>('/info')
      const src = sourceOf(res)
      sourceRef.current = src
      setSource(src)
      if (res.ok) {
        setInfo(res.body)
        setError(null)
        if (force || res.body.graph_version !== seenVersion.current) {
          seenVersion.current = res.body.graph_version
          await loadGraph()
        }
      } else if (src === 'static') {
        const data = await loadStatic()
        setInfo(data.info)
        setGraph(data.graph)
        setError(null)
      } else {
        setError(res.error)
      }
      setLoading(false)
    },
    [loadGraph],
  )

  const refresh = useCallback(() => {
    setLoading(true)
    void load(true)
  }, [load])

  useEffect(() => {
    void load(true)
    const timer = window.setInterval(() => {
      if (sourceRef.current === 'live') void load(false)
    }, POLL_MS)
    return () => window.clearInterval(timer)
  }, [load])

  // The date-tags toggle changes the graph itself, not just its version.
  const first = useRef(true)
  useEffect(() => {
    if (first.current) {
      first.current = false
      return
    }
    if (sourceRef.current === 'live') void loadGraph()
  }, [includeDates, loadGraph])

  const fetchDetail = useCallback(async (id: string): Promise<MemoryDetailPayload> => {
    if (sourceRef.current === 'static') {
      const hit = (await loadStatic()).details[id]
      if (!hit) throw new Error('memory not in the demo data')
      return hit
    }
    const res = await api<MemoryDetailPayload>(memoryPath(id))
    if (!res.ok) throw new Error(res.error)
    return res.body
  }, [])

  return { source, info, graph, error, loading, refresh, fetchDetail }
}
