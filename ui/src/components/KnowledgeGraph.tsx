import { useCallback, useRef, useEffect, useState, useMemo } from 'react'
import ForceGraph2D from 'react-force-graph-2d'
import {
  Memory, MemoryNamespace, Relation, GraphNode, GraphFilters, GraphLayoutSettings,
} from '../types'
import { buildGraphData } from '../lib/graph'
import { TYPE_COLORS } from '../lib/colors'
import GraphToolbar from './GraphToolbar'

interface Props {
  memories: Memory[]
  namespaces: MemoryNamespace[]
  relations: Relation[]
  onSelectMemory: (m: Memory | null) => void
}

const DEFAULT_LAYOUT: GraphLayoutSettings = {
  nodeSizeMultiplier: 1,
  linkDistance: 30,
  chargeStrength: -30,
}

function emptyFilters(): GraphFilters {
  return { text: '', types: new Set(), namespaces: new Set(), confidences: new Set() }
}

export default function KnowledgeGraph({ memories, namespaces, relations, onSelectMemory }: Props) {
  const fgRef = useRef<any>(null)
  const containerRef = useRef<HTMLDivElement>(null)
  const [showTagLinks, setShowTagLinks] = useState(true)
  const [dimensions, setDimensions] = useState({ width: 800, height: 600 })
  const [filters, setFilters] = useState<GraphFilters>(emptyFilters)
  const [layout, setLayout] = useState<GraphLayoutSettings>(DEFAULT_LAYOUT)
  const [hoverId, setHoverId] = useState<string | null>(null)

  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    const ro = new ResizeObserver((entries) => {
      const { width, height } = entries[0].contentRect
      setDimensions({ width, height })
    })
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  // Apply filters at the memory level so the graph shrinks/grows visibly.
  const filteredMemories = useMemo(() => {
    const q = filters.text.trim().toLowerCase()
    return memories.filter((m) => {
      if (filters.types.size > 0 && !filters.types.has(m.type)) return false
      if (filters.confidences.size > 0 && !filters.confidences.has(m.confidence)) return false
      if (filters.namespaces.size > 0 && !filters.namespaces.has(m.namespace_id)) return false
      if (q) {
        const hay = (m.title + ' ' + m.content + ' ' + m.tags.join(' ')).toLowerCase()
        if (!hay.includes(q)) return false
      }
      return true
    })
  }, [memories, filters])

  const filteredIds = useMemo(() => new Set(filteredMemories.map((m) => m.id)), [filteredMemories])

  const filteredRelations = useMemo(
    () => relations.filter((r) => filteredIds.has(r.source_id) && filteredIds.has(r.target_id)),
    [relations, filteredIds],
  )

  const graphData = useMemo(
    () => buildGraphData(filteredMemories, namespaces, filteredRelations, showTagLinks),
    [filteredMemories, namespaces, filteredRelations, showTagLinks],
  )

  // Adjacency for hover highlighting.
  const adjacency = useMemo(() => {
    const map = new Map<string, Set<string>>()
    for (const link of graphData.links) {
      const s = typeof link.source === 'string' ? link.source : (link.source as any).id
      const t = typeof link.target === 'string' ? link.target : (link.target as any).id
      if (!map.has(s)) map.set(s, new Set())
      if (!map.has(t)) map.set(t, new Set())
      map.get(s)!.add(t)
      map.get(t)!.add(s)
    }
    return map
  }, [graphData])

  // Apply force tunings whenever they change.
  useEffect(() => {
    const fg = fgRef.current
    if (!fg) return
    fg.d3Force('charge')?.strength(layout.chargeStrength)
    fg.d3Force('link')?.distance(layout.linkDistance)
    fg.d3ReheatSimulation()
  }, [layout.chargeStrength, layout.linkDistance, graphData])

  const handleNodeClick = useCallback(
    (node: any) => {
      const mem = memories.find((m) => m.id === node.id)
      onSelectMemory(mem ?? null)
    },
    [memories, onSelectMemory],
  )

  const handleZoomToFit = useCallback(() => {
    fgRef.current?.zoomToFit(400, 60)
  }, [])

  const isDimmed = useCallback(
    (id: string): boolean => {
      if (!hoverId) return false
      if (id === hoverId) return false
      const neighbors = adjacency.get(hoverId)
      return !neighbors?.has(id)
    },
    [hoverId, adjacency],
  )

  const paintNode = useCallback(
    (node: any, ctx: CanvasRenderingContext2D) => {
      const n = node as GraphNode
      const baseSize = n.val ?? 5
      const size = baseSize * layout.nodeSizeMultiplier
      const dimmed = isDimmed(n.id)
      const isHover = hoverId === n.id

      ctx.globalAlpha = dimmed ? 0.15 : 1
      ctx.beginPath()
      ctx.arc(node.x!, node.y!, size, 0, 2 * Math.PI)
      ctx.fillStyle = n.color
      ctx.fill()
      if (isHover) {
        ctx.strokeStyle = '#ffffff'
        ctx.lineWidth = 2
      } else {
        ctx.strokeStyle = n.confidence === 'verified' ? 'rgba(255,255,255,0.4)' : 'rgba(255,255,255,0.1)'
        ctx.lineWidth = 1
      }
      ctx.stroke()
      const label = n.name.length > 30 ? n.name.slice(0, 28) + '...' : n.name
      ctx.font = `${isHover ? 'bold ' : ''}3px Inter, sans-serif`
      ctx.textAlign = 'center'
      ctx.textBaseline = 'top'
      ctx.fillStyle = isHover ? '#ffffff' : 'rgba(255,255,255,0.7)'
      ctx.fillText(label, node.x!, node.y! + size + 2)
      ctx.globalAlpha = 1
    },
    [layout.nodeSizeMultiplier, hoverId, isDimmed],
  )

  const linkColor = useCallback(
    (link: any): string => {
      if (!hoverId) return link.color
      const s = typeof link.source === 'string' ? link.source : link.source.id
      const t = typeof link.target === 'string' ? link.target : link.target.id
      const involved = s === hoverId || t === hoverId
      if (involved) return link.type === 'relation' ? '#fbbf24' : 'rgba(251, 191, 36, 0.5)'
      return 'rgba(107, 114, 128, 0.05)'
    },
    [hoverId],
  )

  const linkWidth = useCallback(
    (link: any): number => {
      const base = link.type === 'relation' ? 2 : 0.5
      if (!hoverId) return base
      const s = typeof link.source === 'string' ? link.source : link.source.id
      const t = typeof link.target === 'string' ? link.target : link.target.id
      return s === hoverId || t === hoverId ? base * 2 : base
    },
    [hoverId],
  )

  return (
    <div className="relative h-full w-full" ref={containerRef}>
      <div className="absolute top-4 left-4 z-10 bg-gray-900/80 backdrop-blur px-3 py-2 rounded-lg border border-gray-800">
        <p className="text-[10px] text-gray-500 mb-1 font-semibold uppercase tracking-wider">Types</p>
        <div className="flex flex-col gap-1">
          {Object.entries(TYPE_COLORS).map(([type, color]) => (
            <div key={type} className="flex items-center gap-2 text-xs text-gray-400">
              <span className="w-2.5 h-2.5 rounded-full inline-block" style={{ backgroundColor: color }} />
              {type}
            </div>
          ))}
        </div>
      </div>

      <GraphToolbar
        memories={memories}
        namespaces={namespaces}
        filters={filters}
        onFiltersChange={setFilters}
        layout={layout}
        onLayoutChange={setLayout}
        showTagLinks={showTagLinks}
        onShowTagLinksChange={setShowTagLinks}
        onZoomToFit={handleZoomToFit}
      />

      <ForceGraph2D
        ref={fgRef}
        graphData={graphData}
        width={dimensions.width}
        height={dimensions.height}
        backgroundColor="transparent"
        nodeCanvasObject={paintNode}
        nodePointerAreaPaint={(node: any, color: string, ctx: CanvasRenderingContext2D) => {
          const size = ((node as GraphNode).val ?? 5) * layout.nodeSizeMultiplier
          ctx.beginPath()
          ctx.arc(node.x!, node.y!, size + 2, 0, 2 * Math.PI)
          ctx.fillStyle = color
          ctx.fill()
        }}
        linkColor={linkColor}
        linkWidth={linkWidth}
        linkDirectionalParticles={(link: any) => (link.type === 'relation' ? 2 : 0)}
        linkDirectionalParticleWidth={2}
        onNodeClick={handleNodeClick}
        onNodeHover={(node: any) => setHoverId(node?.id ?? null)}
        onBackgroundClick={() => onSelectMemory(null)}
        cooldownTicks={100}
        d3AlphaDecay={0.02}
        d3VelocityDecay={0.3}
      />
    </div>
  )
}
