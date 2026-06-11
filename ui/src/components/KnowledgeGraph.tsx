import { useCallback, useRef, useEffect, useState } from 'react'
import ForceGraph2D from 'react-force-graph-2d'
import { Memory, MemoryNamespace, Relation, GraphNode } from '../types'
import { buildGraphData } from '../lib/graph'
import { TYPE_COLORS } from '../lib/colors'

interface Props {
  memories: Memory[]
  namespaces: MemoryNamespace[]
  relations: Relation[]
  onSelectMemory: (m: Memory | null) => void
}

export default function KnowledgeGraph({ memories, namespaces, relations, onSelectMemory }: Props) {
  const fgRef = useRef<any>(null)
  const containerRef = useRef<HTMLDivElement>(null)
  const [showTagLinks, setShowTagLinks] = useState(true)
  const [dimensions, setDimensions] = useState({ width: 800, height: 600 })

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

  const graphData = buildGraphData(memories, namespaces, relations, showTagLinks)

  const handleNodeClick = useCallback(
    (node: any) => {
      const mem = memories.find((m) => m.id === node.id)
      onSelectMemory(mem ?? null)
    },
    [memories, onSelectMemory],
  )

  const paintNode = useCallback((node: any, ctx: CanvasRenderingContext2D) => {
    const n = node as GraphNode
    const size = n.val ?? 5
    ctx.beginPath()
    ctx.arc(node.x!, node.y!, size, 0, 2 * Math.PI)
    ctx.fillStyle = n.color
    ctx.fill()
    ctx.strokeStyle = n.confidence === 'verified' ? 'rgba(255,255,255,0.4)' : 'rgba(255,255,255,0.1)'
    ctx.lineWidth = 1
    ctx.stroke()
    const label = n.name.length > 30 ? n.name.slice(0, 28) + '...' : n.name
    ctx.font = '3px Inter, sans-serif'
    ctx.textAlign = 'center'
    ctx.textBaseline = 'top'
    ctx.fillStyle = 'rgba(255,255,255,0.7)'
    ctx.fillText(label, node.x!, node.y! + size + 2)
  }, [])

  return (
    <div className="relative h-full w-full" ref={containerRef}>
      <div className="absolute top-4 left-4 z-10 flex flex-col gap-3">
        <label className="flex items-center gap-2 text-xs text-gray-400 bg-gray-900/80 backdrop-blur px-3 py-2 rounded-lg border border-gray-800">
          <input
            type="checkbox"
            checked={showTagLinks}
            onChange={(e) => setShowTagLinks(e.target.checked)}
            className="rounded"
          />
          Show tag connections
        </label>
        <div className="bg-gray-900/80 backdrop-blur px-3 py-2 rounded-lg border border-gray-800">
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
      </div>
      <ForceGraph2D
        ref={fgRef}
        graphData={graphData}
        width={dimensions.width}
        height={dimensions.height}
        backgroundColor="transparent"
        nodeCanvasObject={paintNode}
        nodePointerAreaPaint={(node: any, color: string, ctx: CanvasRenderingContext2D) => {
          const size = (node as GraphNode).val ?? 5
          ctx.beginPath()
          ctx.arc(node.x!, node.y!, size + 2, 0, 2 * Math.PI)
          ctx.fillStyle = color
          ctx.fill()
        }}
        linkColor={(link: any) => link.color}
        linkWidth={(link: any) => (link.type === 'relation' ? 2 : 0.5)}
        linkDirectionalParticles={(link: any) => (link.type === 'relation' ? 2 : 0)}
        linkDirectionalParticleWidth={2}
        onNodeClick={handleNodeClick}
        onBackgroundClick={() => onSelectMemory(null)}
        cooldownTicks={100}
        d3AlphaDecay={0.02}
        d3VelocityDecay={0.3}
      />
    </div>
  )
}
