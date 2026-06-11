import { Memory } from '../types'
import { getTagFrequencies } from '../lib/graph'

interface Props {
  memories: Memory[]
}

function sizeForCount(count: number, max: number): string {
  const ratio = count / Math.max(max, 1)
  if (ratio > 0.7) return 'text-lg font-bold'
  if (ratio > 0.4) return 'text-sm font-semibold'
  if (ratio > 0.2) return 'text-xs font-medium'
  return 'text-[11px] font-normal'
}

function opacityForCount(count: number, max: number): number {
  return 0.4 + 0.6 * (count / Math.max(max, 1))
}

export default function TagCloud({ memories }: Props) {
  const tags = getTagFrequencies(memories)
  const max = tags[0]?.count ?? 1

  return (
    <div className="bg-gray-900 border border-gray-800 rounded-xl p-4">
      <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-3">
        Tag Cloud
      </h3>
      <div className="flex flex-wrap gap-2 max-h-[200px] overflow-y-auto">
        {tags.map(({ tag, count }) => (
          <span
            key={tag}
            className={`inline-block px-2 py-0.5 rounded-md bg-gray-800 text-blue-400 transition-all hover:bg-gray-700 cursor-default ${sizeForCount(count, max)}`}
            style={{ opacity: opacityForCount(count, max) }}
            title={`${tag}: ${count}`}
          >
            {tag}
            <span className="ml-1 text-gray-600 text-[10px]">{count}</span>
          </span>
        ))}
      </div>
    </div>
  )
}
