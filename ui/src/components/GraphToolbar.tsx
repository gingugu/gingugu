import { useMemo, useState } from 'react'
import { Search, Filter, Maximize2, Sliders, X } from 'lucide-react'
import { Memory, MemoryNamespace, MemoryType, GraphFilters, GraphLayoutSettings } from '../types'
import { TYPE_COLORS } from '../lib/colors'

const ALL_TYPES: MemoryType[] = [
  'fact', 'decision', 'pattern', 'bug', 'architecture', 'preference', 'workflow', 'context',
]
const ALL_CONFIDENCES = ['verified', 'inferred', 'stale'] as const

interface Props {
  memories: Memory[]
  namespaces: MemoryNamespace[]
  filters: GraphFilters
  onFiltersChange: (f: GraphFilters) => void
  layout: GraphLayoutSettings
  onLayoutChange: (l: GraphLayoutSettings) => void
  showTagLinks: boolean
  onShowTagLinksChange: (v: boolean) => void
  onZoomToFit: () => void
}

export default function GraphToolbar({
  memories, namespaces, filters, onFiltersChange,
  layout, onLayoutChange, showTagLinks, onShowTagLinksChange, onZoomToFit,
}: Props) {
  const [openPanel, setOpenPanel] = useState<'filter' | 'layout' | null>(null)

  const matchingCount = useMemo(() => {
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
    }).length
  }, [memories, filters])

  const activeFilterCount =
    (filters.text ? 1 : 0) + filters.types.size + filters.namespaces.size + filters.confidences.size

  function toggleSet<T>(set: Set<T>, value: T): Set<T> {
    const next = new Set(set)
    if (next.has(value)) next.delete(value)
    else next.add(value)
    return next
  }

  return (
    <div className="absolute top-4 right-4 z-10 flex flex-col gap-3 items-end">
      <div className="flex items-center gap-2">
        <div className="relative">
          <Search size={12} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-gray-500" />
          <input
            type="text"
            value={filters.text}
            onChange={(e) => onFiltersChange({ ...filters, text: e.target.value })}
            placeholder="Search title, content, tags..."
            className="w-64 pl-7 pr-2 py-1.5 text-xs bg-gray-900/90 backdrop-blur border border-gray-800 rounded-lg text-gray-200 placeholder-gray-600 focus:outline-none focus:border-blue-700"
          />
        </div>
        <button
          onClick={() => setOpenPanel(openPanel === 'filter' ? null : 'filter')}
          className={`relative flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium border transition-all ${
            openPanel === 'filter'
              ? 'bg-blue-600 text-white border-blue-700'
              : 'text-gray-400 hover:text-white bg-gray-900 border-gray-800 hover:border-gray-700'
          }`}
        >
          <Filter size={12} /> Filter
          {activeFilterCount > 0 && (
            <span className="ml-0.5 bg-blue-500 text-white text-[10px] px-1.5 rounded-full">
              {activeFilterCount}
            </span>
          )}
        </button>
        <button
          onClick={() => setOpenPanel(openPanel === 'layout' ? null : 'layout')}
          className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium border transition-all ${
            openPanel === 'layout'
              ? 'bg-purple-600 text-white border-purple-700'
              : 'text-gray-400 hover:text-white bg-gray-900 border-gray-800 hover:border-gray-700'
          }`}
        >
          <Sliders size={12} /> Layout
        </button>
        <button
          onClick={onZoomToFit}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium text-gray-400 hover:text-white bg-gray-900 border border-gray-800 hover:border-gray-700 transition-all"
          title="Zoom to fit"
        >
          <Maximize2 size={12} /> Fit
        </button>
      </div>

      <div className="text-[10px] text-gray-500 bg-gray-900/70 backdrop-blur px-2 py-0.5 rounded-full border border-gray-800">
        {matchingCount} / {memories.length} match
      </div>

      {openPanel === 'filter' && (
        <div className="w-72 bg-gray-900/95 backdrop-blur border border-gray-800 rounded-lg p-3 space-y-3 text-xs">
          <div className="flex items-center justify-between">
            <span className="text-[10px] uppercase tracking-wider text-gray-500 font-semibold">Filters</span>
            {activeFilterCount > 0 && (
              <button
                onClick={() => onFiltersChange({
                  text: '', types: new Set(), namespaces: new Set(), confidences: new Set(),
                })}
                className="text-[10px] text-gray-500 hover:text-white flex items-center gap-1"
              >
                <X size={10} /> Clear
              </button>
            )}
          </div>

          <div>
            <p className="text-[10px] text-gray-500 mb-1.5 font-semibold uppercase tracking-wider">Type</p>
            <div className="flex flex-wrap gap-1">
              {ALL_TYPES.map((t) => {
                const on = filters.types.has(t)
                return (
                  <button
                    key={t}
                    onClick={() => onFiltersChange({ ...filters, types: toggleSet(filters.types, t) })}
                    className={`flex items-center gap-1 px-2 py-0.5 rounded text-[10px] border transition-colors ${
                      on ? 'border-gray-600 bg-gray-800 text-white' : 'border-gray-800 text-gray-500 hover:text-gray-300'
                    }`}
                  >
                    <span className="w-2 h-2 rounded-full" style={{ backgroundColor: TYPE_COLORS[t] }} />
                    {t}
                  </button>
                )
              })}
            </div>
          </div>

          <div>
            <p className="text-[10px] text-gray-500 mb-1.5 font-semibold uppercase tracking-wider">Namespace</p>
            <div className="flex flex-wrap gap-1">
              {namespaces.map((ns) => {
                const on = filters.namespaces.has(ns.id)
                return (
                  <button
                    key={ns.id}
                    onClick={() => onFiltersChange({ ...filters, namespaces: toggleSet(filters.namespaces, ns.id) })}
                    className={`px-2 py-0.5 rounded text-[10px] border transition-colors ${
                      on ? 'border-gray-600 bg-gray-800 text-white' : 'border-gray-800 text-gray-500 hover:text-gray-300'
                    }`}
                  >
                    {ns.name}
                  </button>
                )
              })}
            </div>
          </div>

          <div>
            <p className="text-[10px] text-gray-500 mb-1.5 font-semibold uppercase tracking-wider">Confidence</p>
            <div className="flex flex-wrap gap-1">
              {ALL_CONFIDENCES.map((c) => {
                const on = filters.confidences.has(c)
                return (
                  <button
                    key={c}
                    onClick={() => onFiltersChange({ ...filters, confidences: toggleSet(filters.confidences, c) })}
                    className={`px-2 py-0.5 rounded text-[10px] border transition-colors ${
                      on ? 'border-gray-600 bg-gray-800 text-white' : 'border-gray-800 text-gray-500 hover:text-gray-300'
                    }`}
                  >
                    {c}
                  </button>
                )
              })}
            </div>
          </div>
        </div>
      )}

      {openPanel === 'layout' && (
        <div className="w-72 bg-gray-900/95 backdrop-blur border border-gray-800 rounded-lg p-3 space-y-3 text-xs">
          <div className="flex items-center justify-between">
            <span className="text-[10px] uppercase tracking-wider text-gray-500 font-semibold">Layout</span>
            <button
              onClick={() => onLayoutChange({ nodeSizeMultiplier: 1, linkDistance: 30, chargeStrength: -30 })}
              className="text-[10px] text-gray-500 hover:text-white"
            >
              Reset
            </button>
          </div>

          <label className="flex flex-col gap-1">
            <span className="flex justify-between text-gray-400">
              Node size <span className="text-gray-600">{layout.nodeSizeMultiplier.toFixed(2)}×</span>
            </span>
            <input
              type="range" min="0.5" max="3" step="0.1"
              value={layout.nodeSizeMultiplier}
              onChange={(e) => onLayoutChange({ ...layout, nodeSizeMultiplier: parseFloat(e.target.value) })}
              className="accent-blue-500"
            />
          </label>

          <label className="flex flex-col gap-1">
            <span className="flex justify-between text-gray-400">
              Link distance <span className="text-gray-600">{layout.linkDistance}</span>
            </span>
            <input
              type="range" min="10" max="120" step="5"
              value={layout.linkDistance}
              onChange={(e) => onLayoutChange({ ...layout, linkDistance: parseInt(e.target.value, 10) })}
              className="accent-blue-500"
            />
          </label>

          <label className="flex flex-col gap-1">
            <span className="flex justify-between text-gray-400">
              Repulsion <span className="text-gray-600">{Math.abs(layout.chargeStrength)}</span>
            </span>
            <input
              type="range" min="-200" max="-10" step="5"
              value={layout.chargeStrength}
              onChange={(e) => onLayoutChange({ ...layout, chargeStrength: parseInt(e.target.value, 10) })}
              className="accent-blue-500"
            />
          </label>

          <label className="flex items-center gap-2 text-gray-400 pt-2 border-t border-gray-800">
            <input
              type="checkbox"
              checked={showTagLinks}
              onChange={(e) => onShowTagLinksChange(e.target.checked)}
              className="rounded"
            />
            Show tag connections
          </label>
        </div>
      )}
    </div>
  )
}
