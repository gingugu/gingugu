import { useMemo, useRef, useState } from 'react'
import { useVirtualizer } from '@tanstack/react-virtual'
import { ArrowDown, ArrowUp, Pin, Search } from 'lucide-react'
import type { Brain } from '../brain/model'
import { CONFIDENCE_COLORS, TYPE_COLORS, getNamespaceColor } from '../lib/colors'
import { healthColor } from '../lib/decay'
import { filterSortNodes, type SortKey } from './table'

interface Props {
  brain: Brain
  selectedId: string | null
  onSelect: (id: string) => void
}

const COLS = 'grid-cols-[minmax(14rem,1fr)_9rem_8rem_7rem_4rem_6rem_5rem_1.5rem]'
const HEADERS: { key: SortKey; label: string }[] = [
  { key: 'title', label: 'Title' },
  { key: 'namespace', label: 'Namespace' },
  { key: 'type', label: 'Type' },
  { key: 'confidence', label: 'Confidence' },
  { key: 'access', label: 'Recalls' },
  { key: 'updated', label: 'Updated' },
  { key: 'health', label: 'Trust' },
]

export default function ListView({ brain, selectedId, onSelect }: Props) {
  const [text, setText] = useState('')
  const [sort, setSort] = useState<SortKey>('updated')
  const [dir, setDir] = useState<'asc' | 'desc'>('desc')
  const rows = useMemo(
    () => filterSortNodes(brain.nodes, { text, sort, dir }),
    [brain.nodes, text, sort, dir],
  )
  const scroller = useRef<HTMLDivElement>(null)
  const virtual = useVirtualizer({
    count: rows.length,
    getScrollElement: () => scroller.current,
    estimateSize: () => 32,
    overscan: 12,
  })

  const clickHeader = (key: SortKey) => {
    if (key === sort) setDir(dir === 'asc' ? 'desc' : 'asc')
    else {
      setSort(key)
      setDir(key === 'title' || key === 'namespace' || key === 'type' ? 'asc' : 'desc')
    }
  }

  return (
    <div className="h-full flex flex-col p-4 gap-3">
      <div className="flex items-center gap-3">
        <div className="relative w-80">
          <Search size={13} className="absolute left-2.5 top-2.5 text-gray-500" />
          <input
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="Filter by title, tag or namespace"
            className="w-full bg-gray-900 border border-gray-800 rounded-lg pl-8 pr-3 py-1.5 text-xs text-gray-200 placeholder:text-gray-600 focus:outline-hidden focus:border-gray-600"
          />
        </div>
        <span className="text-xs text-gray-600">{rows.length} of {brain.nodes.length}</span>
      </div>

      <div className="flex-1 min-h-0 flex flex-col border border-gray-800 rounded-xl overflow-x-auto overflow-y-hidden">
        <div className={`grid ${COLS} min-w-[60rem] gap-3 px-3 py-2 bg-gray-900 border-b border-gray-800 text-[10px] uppercase tracking-wider text-gray-500`}>
          {HEADERS.map((h) => (
            <button key={h.key} onClick={() => clickHeader(h.key)}
              className="flex items-center gap-1 text-left hover:text-gray-300">
              {h.label}
              {sort === h.key && (dir === 'asc' ? <ArrowUp size={10} /> : <ArrowDown size={10} />)}
            </button>
          ))}
          <span />
        </div>
        <div ref={scroller} className="flex-1 min-w-[60rem] overflow-y-auto">
          <div style={{ height: virtual.getTotalSize(), position: 'relative' }}>
            {virtual.getVirtualItems().map((v) => {
              const n = rows[v.index]
              return (
                <button
                  key={n.id}
                  onClick={() => onSelect(n.id)}
                  style={{ position: 'absolute', top: 0, left: 0, width: '100%', height: v.size, transform: `translateY(${v.start}px)` }}
                  className={`grid ${COLS} gap-3 px-3 items-center text-left text-xs border-b border-gray-900 hover:bg-gray-900 ${
                    n.id === selectedId ? 'bg-blue-950/50' : ''
                  } ${n.dormant || n.conf === 'deprecated' ? 'text-gray-500' : 'text-gray-300'}`}
                >
                  <span className="truncate">{n.title}</span>
                  <span className="flex items-center gap-1.5 truncate">
                    <span className="w-2 h-2 rounded-full shrink-0" style={{ backgroundColor: getNamespaceColor(n.nsName) }} />
                    {n.nsName}
                  </span>
                  <span className="flex items-center gap-1.5">
                    <span className="w-2 h-2 rounded-full shrink-0" style={{ backgroundColor: TYPE_COLORS[n.type] }} />
                    {n.type}
                  </span>
                  <span style={{ color: CONFIDENCE_COLORS[n.conf] }}>{n.conf}</span>
                  <span className="tabular-nums">{n.access}</span>
                  <span className="tabular-nums">{n.updated.slice(0, 10)}</span>
                  <span className="h-1.5 w-full rounded-full bg-gray-800 overflow-hidden" title={`${(n.health * 100).toFixed(0)}%`}>
                    <span className="block h-full rounded-full"
                      style={{ width: `${n.health * 100}%`, backgroundColor: healthColor(n.health) }} />
                  </span>
                  {n.pinned ? <Pin size={11} className="text-amber-400" /> : <span />}
                </button>
              )
            })}
          </div>
        </div>
      </div>
    </div>
  )
}
