// Recall history: accesses per day over the last 60 days, as a tiny bar chart.
import { useMemo } from 'react'
import { Bar, BarChart, ResponsiveContainer, Tooltip, XAxis } from 'recharts'

const DAYS = 60

export function recallByDay(accesses: string[], now: Date = new Date()) {
  const counts = new Map<string, number>()
  for (const at of accesses) {
    const day = at.slice(0, 10)
    counts.set(day, (counts.get(day) ?? 0) + 1)
  }
  const out: { day: string; n: number }[] = []
  for (let i = DAYS - 1; i >= 0; i--) {
    const day = new Date(now.getTime() - i * 86_400_000).toISOString().slice(0, 10)
    out.push({ day, n: counts.get(day) ?? 0 })
  }
  return out
}

export default function RecallSparkline({ accesses }: { accesses: string[] }) {
  const data = useMemo(() => recallByDay(accesses), [accesses])
  const total = data.reduce((s, d) => s + d.n, 0)
  if (accesses.length === 0) return <p className="text-xs text-gray-600">Never recalled.</p>
  return (
    <div>
      <ResponsiveContainer width="100%" height={48}>
        <BarChart data={data} margin={{ top: 2, right: 0, bottom: 0, left: 0 }}>
          <XAxis dataKey="day" hide />
          <Tooltip
            cursor={{ fill: 'rgba(148,163,184,0.1)' }}
            contentStyle={{ backgroundColor: '#1f2937', border: '1px solid #374151', borderRadius: 8, fontSize: 11 }}
            formatter={(v: number) => [v, 'recalls']}
          />
          <Bar dataKey="n" fill="#3b82f6" radius={[1, 1, 0, 0]} isAnimationActive={false} />
        </BarChart>
      </ResponsiveContainer>
      <p className="text-[10px] text-gray-600 mt-1">
        {total} in the last {DAYS} days · {accesses.length} logged
      </p>
    </div>
  )
}
