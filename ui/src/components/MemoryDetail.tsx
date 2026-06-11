import { X, Tag, Clock, Shield, Hash } from 'lucide-react'
import { Memory, MemoryNamespace } from '../types'
import { TYPE_COLORS, CONFIDENCE_COLORS, getNamespaceColor } from '../lib/colors'

interface Props {
  memory: Memory
  namespaces: MemoryNamespace[]
  onClose: () => void
}

export default function MemoryDetail({ memory, namespaces, onClose }: Props) {
  const ns = namespaces.find((n) => n.id === memory.namespace_id)
  const nsName = ns?.name ?? 'unknown'
  const created = new Date(memory.created_at).toLocaleDateString('en-US', {
    year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  })

  return (
    <div className="absolute top-4 right-4 z-20 w-96 max-h-[80vh] bg-gray-900/95 backdrop-blur-lg border border-gray-700 rounded-xl shadow-2xl overflow-hidden flex flex-col">
      <div className="flex items-start justify-between p-4 border-b border-gray-800">
        <div className="flex-1 min-w-0 pr-2">
          <div className="flex items-center gap-2 mb-1">
            <span
              className="inline-block px-2 py-0.5 rounded text-[10px] font-bold uppercase tracking-wider"
              style={{ backgroundColor: TYPE_COLORS[memory.type] + '30', color: TYPE_COLORS[memory.type] }}
            >
              {memory.type}
            </span>
            <span
              className="inline-block px-2 py-0.5 rounded text-[10px] font-bold uppercase tracking-wider"
              style={{
                backgroundColor: (CONFIDENCE_COLORS[memory.confidence] ?? '#6b7280') + '30',
                color: CONFIDENCE_COLORS[memory.confidence] ?? '#6b7280',
              }}
            >
              {memory.confidence}
            </span>
          </div>
          <h3 className="text-sm font-semibold text-white leading-snug">{memory.title}</h3>
        </div>
        <button onClick={onClose} className="text-gray-500 hover:text-white transition-colors p-1">
          <X size={16} />
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-4 space-y-3">
        <div className="flex items-center gap-4 text-xs text-gray-500">
          <span className="flex items-center gap-1" style={{ color: getNamespaceColor(nsName) }}>
            <Hash size={12} /> {nsName}
          </span>
          <span className="flex items-center gap-1">
            <Clock size={12} /> {created}
          </span>
          <span className="flex items-center gap-1">
            <Shield size={12} /> {memory.access_count} accesses
          </span>
        </div>

        <p className="text-sm text-gray-300 leading-relaxed whitespace-pre-wrap">{memory.content}</p>

        {memory.tags.length > 0 && (
          <div className="flex flex-wrap gap-1.5 pt-1">
            {memory.tags.map((tag) => (
              <span key={tag} className="inline-flex items-center gap-1 px-2 py-0.5 bg-gray-800 text-gray-400 rounded text-[11px]">
                <Tag size={10} /> {tag}
              </span>
            ))}
          </div>
        )}
      </div>

      <div className="p-3 border-t border-gray-800 text-[10px] text-gray-600 font-mono truncate">
        {memory.id}
      </div>
    </div>
  )
}
