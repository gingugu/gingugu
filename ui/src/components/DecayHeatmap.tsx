import { useMemo, useState } from 'react'
import { Clock } from 'lucide-react'
import { Memory, MemoryNamespace, DecayScored } from '../types'
import { scoreAll, healthColor } from '../lib/decay'
import { TYPE_COLORS } from '../lib/colors'
import MemoryDetail from './MemoryDetail'

type SortKey = 'health' | 'days' | 'access' | 'created'
type GroupKey = 'none' | 'namespace' | 'type' | 'confidence'

interface Props {
  memories: Memory[]
  namespaces: MemoryNamespace[]
}

export default function DecayHeatmap({ memories, namespaces }: Props) {
  const [sortKey, setSortKey] = useState<SortKey>('health')
  const [groupKey, setGroupKey] = useState<GroupKey>('namespace')
  const [selected, setSelected] = useState<Memory | null>(null)

  const scored = useMemo(() => scoreAll(memories), [memories])

  const nsMap = useMemo(() => {
    const m = new Map<string, string>()
    for (const ns of namespaces) m.set(ns.id, ns.name)
    return m
  }, [namespaces])

  const groups = useMemo(() => {
    const grouped = new Map<string, DecayScored[]>()
    for (const s of scored) {
      let key = 'all'
      if (groupKey === 'namespace') key = nsMap.get(s.memory.namespace_id) ?? 'unknown'
      else if (groupKey === 'type') key = s.memory.type
      else if (groupKey === 'confidence') key = s.memory.confidence
      if (!grouped.has(key)) grouped.set(key, [])
      grouped.get(key)!.push(s)
    }
    for (const arr of grouped.values()) {
      arr.sort((a, b) => {
        if (sortKey === 'health') return a.health - b.health
        if (sortKey === 'days') return b.daysSinceConfirmed - a.daysSinceConfirmed
        if (sortKey === 'access') return a.memory.access_count - b.memory.access_count
        return b.memory.created_at.localeCompare(a.memory.created_at)
      })
    }
    return Array.from(grouped.entries()).sort(([a], [b]) => a.localeCompare(b))
  }, [scored, groupKey, sortKey, nsMap])

  const summary = useMemo(() => {
    const buckets = { healthy: 0, fading: 0, stale: 0, dormant: 0 }
    for (const s of scored) {
      if (s.health >= 0.7) buckets.healthy++
      else if (s.health >= 0.4) buckets.fading++
      else buckets.stale++
      if (s.dormant) buckets.dormant++
    }
    return buckets
  }, [scored])

  return (
    <div className="p-6 overflow-y-auto h-full">
      <div className="flex items-center justify-between mb-4 flex-wrap gap-3">
        <div>
          <h2 className="text-lg font-bold text-white">Trust Map</h2>
          <p className="text-xs text-gray-500 mt-0.5">
            Color = trust (confidence-led, with a freshness floor). Nothing is ever
            forgotten — the clock badge marks <span className="text-gray-400">dormant</span>{' '}
            memories (90+ days untouched), resting until recall wakes them.
          </p>
        </div>

        <div className="flex items-center gap-2">
          <select
            value={groupKey}
            onChange={(e) => setGroupKey(e.target.value as GroupKey)}
            className="bg-gray-900 border border-gray-800 rounded-lg px-2 py-1.5 text-xs text-gray-300"
          >
            <option value="none">No grouping</option>
            <option value="namespace">Group: Namespace</option>
            <option value="type">Group: Type</option>
            <option value="confidence">Group: Confidence</option>
          </select>
          <select
            value={sortKey}
            onChange={(e) => setSortKey(e.target.value as SortKey)}
            className="bg-gray-900 border border-gray-800 rounded-lg px-2 py-1.5 text-xs text-gray-300"
          >
            <option value="health">Sort: Health (worst first)</option>
            <option value="days">Sort: Days since confirmed</option>
            <option value="access">Sort: Access count</option>
            <option value="created">Sort: Newest first</option>
          </select>
        </div>
      </div>

      <div className="grid grid-cols-4 gap-3 mb-6">
        <SummaryCard label="Trusted" count={summary.healthy} colorClass="text-emerald-400 border-emerald-900 bg-emerald-950/40" hint="trust ≥ 0.7" />
        <SummaryCard label="Tentative" count={summary.fading} colorClass="text-amber-400 border-amber-900 bg-amber-950/40" hint="0.4 ≤ trust < 0.7" />
        <SummaryCard label="Low trust" count={summary.stale} colorClass="text-red-400 border-red-900 bg-red-950/40" hint="trust < 0.4" />
        <SummaryCard label="Dormant" count={summary.dormant} colorClass="text-sky-400 border-sky-900 bg-sky-950/40" hint="90+ days untouched" />
      </div>

      <div className="flex items-center gap-4 mb-4 text-[10px] text-gray-500">
        <div className="flex items-center gap-2">
          <span>Low trust</span>
          <div className="h-2 w-48 rounded-full" style={{
            background: 'linear-gradient(to right, rgb(239,68,68), rgb(234,179,8), rgb(34,197,94))',
          }} />
          <span>Trusted</span>
        </div>
        <div className="flex items-center gap-1.5">
          <Clock size={11} className="text-sky-400" />
          <span>= dormant (resting)</span>
        </div>
      </div>

      <div className="space-y-6">
        {groups.map(([groupName, items]) => (
          <div key={groupName}>
            {groupKey !== 'none' && (
              <div className="flex items-center justify-between mb-2">
                <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider">
                  {groupName}
                </h3>
                <span className="text-[10px] text-gray-600">{items.length}</span>
              </div>
            )}
            <div className="grid grid-cols-[repeat(auto-fill,minmax(28px,1fr))] gap-1">
              {items.map((s) => (
                <Cell key={s.memory.id} scored={s} onClick={() => setSelected(s.memory)} />
              ))}
            </div>
          </div>
        ))}
      </div>

      {selected && (
        <MemoryDetail
          memory={selected}
          namespaces={namespaces}
          onClose={() => setSelected(null)}
        />
      )}
    </div>
  )
}

function SummaryCard({ label, count, colorClass, hint }: { label: string; count: number; colorClass: string; hint: string }) {
  return (
    <div className={`rounded-xl border p-4 ${colorClass}`}>
      <div className="text-[10px] uppercase tracking-wider opacity-70">{label}</div>
      <div className="text-2xl font-bold mt-1">{count}</div>
      <div className="text-[10px] opacity-60 mt-1">{hint}</div>
    </div>
  )
}

function Cell({ scored, onClick }: { scored: DecayScored; onClick: () => void }) {
  const color = healthColor(scored.health)
  const accent = TYPE_COLORS[scored.memory.type] ?? '#6b7280'
  const dormantNote = scored.dormant
    ? `\ndormant — ${scored.daysSinceAccessed.toFixed(0)}d since last touched`
    : ''
  return (
    <button
      onClick={onClick}
      title={`${scored.memory.title}\ntrust ${(scored.health * 100).toFixed(0)}% · ${scored.memory.access_count} accesses · ${scored.memory.confidence}${dormantNote}`}
      className="aspect-square rounded relative group hover:scale-110 hover:z-10 transition-transform"
      style={{ backgroundColor: color }}
    >
      {scored.dormant && (
        <Clock
          size={9}
          className="absolute top-0.5 right-0.5 text-black/60"
          strokeWidth={3}
        />
      )}
      <span
        className="absolute bottom-0 left-0 right-0 h-1 rounded-b"
        style={{ backgroundColor: accent }}
      />
    </button>
  )
}
