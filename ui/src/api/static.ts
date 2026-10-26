// The bundled public demo (ui/dump_static.py). Dynamic imports keep the JSON out
// of the live bundle until a static deployment actually needs it.
import type { GraphPayload, InfoPayload, MemoryDetailPayload } from './types'

export interface StaticData {
  graph: GraphPayload
  info: InfoPayload
  details: Record<string, MemoryDetailPayload>
}

let cached: Promise<StaticData> | null = null

export function loadStatic(): Promise<StaticData> {
  cached ??= Promise.all([
    import('../data/static/graph.json'),
    import('../data/static/info.json'),
    import('../data/static/details.json'),
  ]).then(([graph, info, details]) => ({
    graph: graph.default as unknown as GraphPayload,
    info: info.default as unknown as InfoPayload,
    details: details.default as unknown as Record<string, MemoryDetailPayload>,
  }))
  return cached
}
