import { PieChart, Pie, Cell, Tooltip, ResponsiveContainer, Legend } from 'recharts'
import { Memory } from '../types'
import { CONFIDENCE_COLORS } from '../lib/colors'
import { getConfidenceDistribution } from '../lib/graph'

interface Props {
  memories: Memory[]
}

export default function ConfidenceChart({ memories }: Props) {
  const data = getConfidenceDistribution(memories)

  return (
    <div className="bg-gray-900 border border-gray-800 rounded-xl p-4">
      <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-3">
        By Confidence
      </h3>
      <ResponsiveContainer width="100%" height={220}>
        <PieChart>
          <Pie
            data={data}
            dataKey="count"
            nameKey="confidence"
            cx="50%"
            cy="50%"
            innerRadius={50}
            outerRadius={80}
            paddingAngle={4}
            strokeWidth={0}
          >
            {data.map((entry) => (
              <Cell
                key={entry.confidence}
                fill={CONFIDENCE_COLORS[entry.confidence] ?? '#6b7280'}
              />
            ))}
          </Pie>
          <Tooltip
            contentStyle={{
              backgroundColor: '#1f2937',
              border: '1px solid #374151',
              borderRadius: 8,
              fontSize: 12,
            }}
          />
          <Legend
            formatter={(value: string) => (
              <span className="text-xs text-gray-400">{value}</span>
            )}
          />
        </PieChart>
      </ResponsiveContainer>
    </div>
  )
}
