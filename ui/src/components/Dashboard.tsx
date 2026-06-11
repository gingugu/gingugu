import { Memory, MemoryNamespace, Relation } from '../types'
import StatsCards from './StatsCards'
import TypeChart from './TypeChart'
import NamespaceChart from './NamespaceChart'
import ConfidenceChart from './ConfidenceChart'
import TagCloud from './TagCloud'
import Timeline from './Timeline'

interface Props {
  memories: Memory[]
  namespaces: MemoryNamespace[]
  relations: Relation[]
}

export default function Dashboard({ memories, namespaces, relations }: Props) {
  return (
    <div className="p-6 space-y-6 overflow-y-auto h-full">
      <StatsCards memories={memories} namespaces={namespaces} relations={relations} />

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <TypeChart memories={memories} />
        <NamespaceChart memories={memories} namespaces={namespaces} />
        <ConfidenceChart memories={memories} />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <Timeline memories={memories} />
        <TagCloud memories={memories} />
      </div>

      <div className="bg-gray-900 border border-gray-800 rounded-xl p-4">
        <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-3">
          Recent Memories
        </h3>
        <div className="space-y-2 max-h-[300px] overflow-y-auto">
          {[...memories]
            .sort((a, b) => b.created_at.localeCompare(a.created_at))
            .slice(0, 15)
            .map((m) => (
              <div
                key={m.id}
                className="flex items-center gap-3 px-3 py-2 rounded-lg hover:bg-gray-800/60 transition-colors"
              >
                <span
                  className="w-2 h-2 rounded-full flex-shrink-0"
                  style={{
                    backgroundColor:
                      {
                        fact: '#3b82f6', decision: '#a855f7', bug: '#ef4444',
                        pattern: '#f97316', architecture: '#14b8a6', context: '#6b7280',
                        preference: '#ec4899', workflow: '#22c55e',
                      }[m.type] ?? '#6b7280',
                  }}
                />
                <span className="text-sm text-gray-300 truncate flex-1">{m.title}</span>
                <span className="text-[10px] text-gray-600 flex-shrink-0">
                  {new Date(m.created_at).toLocaleDateString()}
                </span>
              </div>
            ))}
        </div>
      </div>
    </div>
  )
}
