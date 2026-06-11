import { Brain, Layers, Tags, Link } from 'lucide-react'
import { Memory, MemoryNamespace, Relation } from '../types'

interface Props {
  memories: Memory[]
  namespaces: MemoryNamespace[]
  relations: Relation[]
}

interface CardProps {
  icon: React.ReactNode
  label: string
  value: string | number
  sub?: string
  color: string
}

function Card({ icon, label, value, sub, color }: CardProps) {
  return (
    <div className="bg-gray-900 border border-gray-800 rounded-xl p-4 flex items-center gap-4">
      <div className="p-3 rounded-lg" style={{ backgroundColor: color + '20' }}>
        <div style={{ color }}>{icon}</div>
      </div>
      <div>
        <p className="text-2xl font-bold text-white">{value}</p>
        <p className="text-xs text-gray-500">{label}</p>
        {sub && <p className="text-[10px] text-gray-600 mt-0.5">{sub}</p>}
      </div>
    </div>
  )
}

export default function StatsCards({ memories, namespaces, relations }: Props) {
  const uniqueTags = new Set(memories.flatMap((m) => m.tags))
  const verified = memories.filter((m) => m.confidence === 'verified').length

  return (
    <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
      <Card
        icon={<Brain size={22} />}
        label="Total Memories"
        value={memories.length}
        sub={`${verified} verified`}
        color="#3b82f6"
      />
      <Card
        icon={<Layers size={22} />}
        label="Namespaces"
        value={namespaces.length}
        sub={namespaces.map((n) => n.name).join(', ')}
        color="#a855f7"
      />
      <Card
        icon={<Tags size={22} />}
        label="Unique Tags"
        value={uniqueTags.size}
        color="#f97316"
      />
      <Card
        icon={<Link size={22} />}
        label="Relations"
        value={relations.length}
        sub={relations.length === 0 ? 'None yet' : undefined}
        color="#14b8a6"
      />
    </div>
  )
}
