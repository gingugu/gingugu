import { useMemo, useState } from 'react'
import {
  ResponsiveContainer, AreaChart, Area, BarChart, Bar, XAxis, YAxis, Tooltip, Legend, CartesianGrid,
} from 'recharts'
import { Memory, MemoryNamespace, MemoryType } from '../types'
import { TYPE_COLORS } from '../lib/colors'

type Granularity = 'day' | 'week' | 'month'

interface Props {
  memories: Memory[]
  namespaces: MemoryNamespace[]
}

const ALL_TYPES: MemoryType[] = [
  'fact', 'decision', 'pattern', 'bug', 'architecture', 'preference', 'workflow', 'context',
]

function bucketKey(iso: string, gran: Granularity): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return 'unknown'
  if (gran === 'day') return d.toISOString().slice(0, 10)
  if (gran === 'month') return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, '0')}`
  // ISO week
  const tmp = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate()))
  const dayNum = (tmp.getUTCDay() + 6) % 7
  tmp.setUTCDate(tmp.getUTCDate() - dayNum + 3)
  const firstThursday = new Date(Date.UTC(tmp.getUTCFullYear(), 0, 4))
  const week = 1 + Math.round(((tmp.getTime() - firstThursday.getTime()) / 86_400_000 - 3 + ((firstThursday.getUTCDay() + 6) % 7)) / 7)
  return `${tmp.getUTCFullYear()}-W${String(week).padStart(2, '0')}`
}

export default function TimelineView({ memories, namespaces }: Props) {
  const [granularity, setGranularity] = useState<Granularity>('day')

  const totalSeries = useMemo(() => {
    const byBucket = new Map<string, number>()
    for (const m of memories) {
      const k = bucketKey(m.created_at, granularity)
      byBucket.set(k, (byBucket.get(k) ?? 0) + 1)
    }
    return Array.from(byBucket.entries())
      .map(([bucket, count]) => ({ bucket, count }))
      .sort((a, b) => a.bucket.localeCompare(b.bucket))
  }, [memories, granularity])

  const stackedSeries = useMemo(() => {
    const byBucket = new Map<string, Record<string, number | string>>()
    for (const m of memories) {
      const k = bucketKey(m.created_at, granularity)
      const row = byBucket.get(k) ?? { bucket: k }
      row[m.type] = ((row[m.type] as number | undefined) ?? 0) + 1
      byBucket.set(k, row)
    }
    return Array.from(byBucket.values()).sort((a, b) =>
      (a.bucket as string).localeCompare(b.bucket as string),
    )
  }, [memories, granularity])

  const accessSeries = useMemo(() => {
    const byBucket = new Map<string, number>()
    for (const m of memories) {
      const k = bucketKey(m.last_accessed || m.updated_at || m.created_at, granularity)
      byBucket.set(k, (byBucket.get(k) ?? 0) + (m.access_count || 0))
    }
    return Array.from(byBucket.entries())
      .map(([bucket, accesses]) => ({ bucket, accesses }))
      .sort((a, b) => a.bucket.localeCompare(b.bucket))
  }, [memories, granularity])

  const stats = useMemo(() => {
    const dates = memories.map((m) => new Date(m.created_at).getTime()).filter((t) => !Number.isNaN(t))
    if (dates.length === 0) return null
    const min = new Date(Math.min(...dates))
    const max = new Date(Math.max(...dates))
    const totalDays = Math.max(1, Math.round((max.getTime() - min.getTime()) / 86_400_000))
    return {
      first: min.toLocaleDateString(),
      last: max.toLocaleDateString(),
      totalDays,
      avgPerDay: (memories.length / totalDays).toFixed(2),
    }
  }, [memories])

  const labelFormatter = (v: string) => {
    if (granularity === 'day') return new Date(v).toLocaleDateString()
    return v
  }

  return (
    <div className="p-6 space-y-6 overflow-y-auto h-full">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h2 className="text-lg font-bold text-white">Memory Timeline</h2>
          <p className="text-xs text-gray-500 mt-0.5">
            {memories.length} memories across {namespaces.length} namespaces
            {stats && ` · ${stats.first} → ${stats.last} (${stats.totalDays}d, ${stats.avgPerDay}/day avg)`}
          </p>
        </div>
        <div className="flex bg-gray-900 rounded-lg p-0.5 border border-gray-800">
          {(['day', 'week', 'month'] as Granularity[]).map((g) => (
            <button
              key={g}
              onClick={() => setGranularity(g)}
              className={`px-3 py-1 rounded-md text-xs font-medium transition-colors ${
                granularity === g ? 'bg-blue-600 text-white' : 'text-gray-400 hover:text-white'
              }`}
            >
              {g}
            </button>
          ))}
        </div>
      </div>

      <ChartCard title="Memories created over time">
        <ResponsiveContainer width="100%" height={240}>
          <AreaChart data={totalSeries} margin={{ left: -10 }}>
            <defs>
              <linearGradient id="totalGrad" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor="#3b82f6" stopOpacity={0.5} />
                <stop offset="95%" stopColor="#3b82f6" stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
            <XAxis dataKey="bucket" tick={{ fontSize: 10, fill: '#6b7280' }} axisLine={false} tickLine={false} tickFormatter={labelFormatter} />
            <YAxis tick={{ fontSize: 10, fill: '#6b7280' }} axisLine={false} tickLine={false} allowDecimals={false} />
            <Tooltip
              contentStyle={{ backgroundColor: '#1f2937', border: '1px solid #374151', borderRadius: 8, fontSize: 12 }}
              labelFormatter={labelFormatter}
            />
            <Area type="monotone" dataKey="count" stroke="#3b82f6" fill="url(#totalGrad)" strokeWidth={2} />
          </AreaChart>
        </ResponsiveContainer>
      </ChartCard>

      <ChartCard title="By type (stacked)">
        <ResponsiveContainer width="100%" height={260}>
          <BarChart data={stackedSeries} margin={{ left: -10 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
            <XAxis dataKey="bucket" tick={{ fontSize: 10, fill: '#6b7280' }} axisLine={false} tickLine={false} tickFormatter={labelFormatter} />
            <YAxis tick={{ fontSize: 10, fill: '#6b7280' }} axisLine={false} tickLine={false} allowDecimals={false} />
            <Tooltip
              contentStyle={{ backgroundColor: '#1f2937', border: '1px solid #374151', borderRadius: 8, fontSize: 12 }}
              labelFormatter={labelFormatter}
            />
            <Legend wrapperStyle={{ fontSize: 11 }} iconType="circle" />
            {ALL_TYPES.map((t) => (
              <Bar key={t} dataKey={t} stackId="a" fill={TYPE_COLORS[t]} />
            ))}
          </BarChart>
        </ResponsiveContainer>
      </ChartCard>

      <ChartCard title="Access activity (last accessed timestamps × access count)">
        <ResponsiveContainer width="100%" height={200}>
          <AreaChart data={accessSeries} margin={{ left: -10 }}>
            <defs>
              <linearGradient id="accGrad" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor="#a855f7" stopOpacity={0.5} />
                <stop offset="95%" stopColor="#a855f7" stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
            <XAxis dataKey="bucket" tick={{ fontSize: 10, fill: '#6b7280' }} axisLine={false} tickLine={false} tickFormatter={labelFormatter} />
            <YAxis tick={{ fontSize: 10, fill: '#6b7280' }} axisLine={false} tickLine={false} allowDecimals={false} />
            <Tooltip
              contentStyle={{ backgroundColor: '#1f2937', border: '1px solid #374151', borderRadius: 8, fontSize: 12 }}
              labelFormatter={labelFormatter}
            />
            <Area type="monotone" dataKey="accesses" stroke="#a855f7" fill="url(#accGrad)" strokeWidth={2} />
          </AreaChart>
        </ResponsiveContainer>
      </ChartCard>
    </div>
  )
}

function ChartCard({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="bg-gray-900 border border-gray-800 rounded-xl p-4">
      <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-3">{title}</h3>
      {children}
    </div>
  )
}
