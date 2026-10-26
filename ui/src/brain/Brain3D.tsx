import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import ForceGraph3D, { type ForceGraphMethods } from 'react-force-graph-3d'
import type { Brain, BrainLink } from './model'
import BrainControls, { type BrainSettings } from './BrainControls'
import BrainLegend from './BrainLegend'
import { lobeAnchors, lobeForce, type LobeForce } from './lobes'
import { buildNodeObject, escapeHtml, withAlpha } from './nodeObjects'
import { useBloom } from './useBloom'
import { useBrainCamera } from './useBrainCamera'
import { endId, useStableGraph, type GLink, type GNode } from './useStableGraph'
import { linkStyle, nodeColor, nodeSize } from './visuals'

interface Props {
  brain: Brain
  persona: string | null
  selectedId: string | null
  onSelect: (id: string | null) => void
  includeDates: boolean
  onIncludeDates: (on: boolean) => void
  drawerOpen: boolean
}

function useElementSize<T extends HTMLElement>() {
  const ref = useRef<T>(null)
  const [size, setSize] = useState({ width: 800, height: 600 })
  useEffect(() => {
    const el = ref.current
    if (!el) return
    const ro = new ResizeObserver(([entry]) => {
      const { width, height } = entry.contentRect
      if (width > 0 && height > 0) setSize({ width, height })
    })
    ro.observe(el)
    return () => ro.disconnect()
  }, [])
  return [ref, size] as const
}

export default function Brain3D({
  brain, persona, selectedId, onSelect, includeDates, onIncludeDates, drawerOpen,
}: Props) {
  const fgRef = useRef<ForceGraphMethods>()
  const [wrap, size] = useElementSize<HTMLDivElement>()
  const [settings, setSettings] = useState<BrainSettings>({
    mode: 'type', showTags: true, hiddenNs: new Set(), lobeStrength: 0.04,
  })
  const { data, maxTagWeight } = useStableGraph(brain)
  const empty = brain.nodes.length === 0
  const ready = !empty

  useBloom(fgRef, ready)
  const flyTo = useBrainCamera(fgRef, ready, data.nodes, selectedId)

  // Lobes: swap the force when the namespaces change, retune it live otherwise.
  const forceRef = useRef<LobeForce | null>(null)
  const anchors = useMemo(
    () => lobeAnchors(brain.namespaces, { persona }),
    [brain.namespaces, persona],
  )
  useEffect(() => {
    const fg = fgRef.current
    if (!fg || !ready) return
    forceRef.current = lobeForce(anchors, settings.lobeStrength)
    fg.d3Force('lobe', forceRef.current)
    // The anchors place the brain; d3's centroid force would fight them and
    // drag the core off-centre whenever the lobes are unevenly sized.
    fg.d3Force('center', null)
    fg.d3Force('charge')?.strength?.(-30)
    // Relations shape the brain; tag links mostly draw it. At full strength
    // thousands of tag links would melt the small lobes into the big ones.
    fg.d3Force('link')?.strength?.((l: GLink) =>
      l.kind === 'tag' ? 0.01 + 0.06 * Math.min(1, l.weight / (maxTagWeight || 1)) : 0.35,
    )
    fg.d3ReheatSimulation()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [anchors, ready, maxTagWeight])
  useEffect(() => {
    forceRef.current?.strength(settings.lobeStrength)
    fgRef.current?.d3ReheatSimulation()
  }, [settings.lobeStrength])

  const { mode, showTags, hiddenNs } = settings
  const nodeThree = useCallback(
    (n: object) => buildNodeObject(n as GNode, nodeColor(n as GNode, mode)),
    // Health and dormancy move with each refetch, so rebuild on a new brain too.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [mode, brain],
  )

  const nodeVisible = useCallback((n: object) => !hiddenNs.has((n as GNode).nsId), [hiddenNs])
  const linkVisible = useCallback(
    (l: object) => {
      const link = l as GLink
      if (link.kind === 'tag' && !showTags) return false
      const a = brain.byId.get(endId(link.source))
      const b = brain.byId.get(endId(link.target))
      return !!a && !!b && !hiddenNs.has(a.nsId) && !hiddenNs.has(b.nsId)
    },
    [showTags, hiddenNs, brain],
  )
  const style = useCallback((l: GLink) => linkStyle(l as unknown as BrainLink, maxTagWeight), [maxTagWeight])
  const linkColor = useCallback(
    (l: object) => {
      const s = style(l as GLink)
      return withAlpha(s.color, s.opacity)
    },
    [style],
  )
  const linkWidth = useCallback((l: object) => style(l as GLink).width, [style])
  const particles = useCallback(
    (l: object) => {
      const link = l as GLink
      if (!selectedId || link.kind !== 'relation') return 0
      return endId(link.source) === selectedId || endId(link.target) === selectedId ? 2 : 0
    },
    [selectedId],
  )

  const label = useCallback(
    (n: object) => {
      const g = n as GNode
      return `${escapeHtml(g.title)} · ${escapeHtml(g.nsName)} · ${escapeHtml(g.type)}`
    },
    [],
  )

  // Frame the brain once, when the first layout settles; never yank the
  // camera away from someone who is already exploring.
  const framed = useRef(false)
  const onEngineStop = useCallback(() => {
    if (framed.current || selectedId) return
    framed.current = true
    fgRef.current?.zoomToFit(800, 10)
  }, [selectedId])

  const onClick = useCallback(
    (n: object) => {
      const g = n as GNode
      onSelect(g.id)
      flyTo(g)
    },
    [onSelect, flyTo],
  )

  if (empty) {
    return (
      <div className="h-full flex items-center justify-center text-sm text-gray-500">
        This brain is empty. Store a memory and it will light up here.
      </div>
    )
  }

  return (
    <div ref={wrap} className="relative h-full w-full bg-[#05060a]">
      <ForceGraph3D
        ref={fgRef as never}
        width={size.width}
        height={size.height}
        graphData={data}
        backgroundColor="#05060a"
        controlType="orbit"
        showNavInfo={false}
        warmupTicks={60}
        cooldownTicks={250}
        nodeVal={(n) => nodeSize(n as GNode)}
        nodeLabel={label}
        nodeVisibility={nodeVisible}
        nodeThreeObject={nodeThree}
        linkVisibility={linkVisible}
        linkColor={linkColor}
        linkWidth={linkWidth}
        linkOpacity={1}
        linkDirectionalParticles={particles}
        linkDirectionalParticleSpeed={0.006}
        linkDirectionalParticleWidth={1.5}
        onEngineStop={onEngineStop}
        onNodeClick={onClick}
        onBackgroundClick={() => onSelect(null)}
      />
      <BrainControls
        settings={settings}
        onChange={setSettings}
        namespaces={brain.namespaces}
        includeDates={includeDates}
        onIncludeDates={onIncludeDates}
        shifted={drawerOpen}
        onFit={() => fgRef.current?.zoomToFit(600)}
      />
      <BrainLegend mode={mode} namespaces={brain.namespaces} />
    </div>
  )
}
