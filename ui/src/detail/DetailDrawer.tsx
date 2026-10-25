import { useEffect, useState } from 'react'
import { Loader2, Pin, X } from 'lucide-react'
import type { MemoryDetailPayload } from '../api/types'
import { CONFIDENCE_COLORS, TYPE_COLORS, getNamespaceColor } from '../lib/colors'
import { Claims, Relations, Section, Tripwires } from './DetailSections'
import MemoryBody from './MemoryBody'
import RecallSparkline from './RecallSparkline'

interface Props {
  id: string
  titleIndex: Map<string, string>
  fetchDetail: (id: string) => Promise<MemoryDetailPayload>
  onSelect: (id: string) => void
  onClose: () => void
}

type State =
  | { id: string; status: 'loading' }
  | { id: string; status: 'error'; error: string }
  | { id: string; status: 'ready'; detail: MemoryDetailPayload }

const fmt = (iso: string) => (iso ? new Date(iso).toLocaleString() : '-')

function Badge({ children, color }: { children: React.ReactNode; color?: string }) {
  return (
    <span className="text-[10px] px-2 py-0.5 rounded-full border border-gray-700 text-gray-300 flex items-center gap-1">
      {color && <span className="w-1.5 h-1.5 rounded-full" style={{ backgroundColor: color }} />}
      {children}
    </span>
  )
}

export default function DetailDrawer({ id, titleIndex, fetchDetail, onSelect, onClose }: Props) {
  const [state, setState] = useState<State>({ id, status: 'loading' })

  useEffect(() => {
    let live = true
    setState({ id, status: 'loading' })
    fetchDetail(id).then(
      (detail) => live && setState({ id, status: 'ready', detail }),
      (err: unknown) =>
        live && setState({ id, status: 'error', error: err instanceof Error ? err.message : 'failed' }),
    )
    return () => {
      live = false
    }
  }, [id, fetchDetail])

  return (
    <aside className="absolute top-0 right-0 bottom-0 z-20 w-[28rem] max-w-full bg-gray-950/95 backdrop-blur-sm border-l border-gray-800 overflow-y-auto">
      <button onClick={onClose} title="Close"
        className="absolute top-3 right-3 p-1 rounded-md text-gray-500 hover:text-white hover:bg-gray-800">
        <X size={16} />
      </button>
      <div className="p-5 pt-4">
        {state.status === 'loading' && (
          <div className="flex items-center gap-2 text-sm text-gray-500 mt-6">
            <Loader2 size={14} className="animate-spin" /> Loading memory...
          </div>
        )}
        {state.status === 'error' && (
          <p className="text-sm text-red-400 mt-6">Could not load this memory: {state.error}</p>
        )}
        {state.status === 'ready' && (
          <Loaded detail={state.detail} titleIndex={titleIndex} onSelect={onSelect} />
        )}
      </div>
    </aside>
  )
}

function Loaded({ detail, titleIndex, onSelect }: {
  detail: MemoryDetailPayload
  titleIndex: Map<string, string>
  onSelect: (id: string) => void
}) {
  const m = detail.memory
  return (
    <>
      <div className="flex flex-wrap gap-1.5 pr-8">
        <Badge color={TYPE_COLORS[m.type]}>{m.type}</Badge>
        <Badge color={CONFIDENCE_COLORS[m.confidence]}>{m.confidence}</Badge>
        {m.pinned && <Badge><Pin size={9} /> pinned</Badge>}
        {m.provenance && <Badge>provenance: {m.provenance}</Badge>}
        {m.about && <Badge>about: {m.about}</Badge>}
      </div>
      <h2 className="text-lg font-bold text-white mt-3 leading-snug">{m.title}</h2>
      <p className="text-xs mt-1 flex items-center gap-1.5 text-gray-400">
        <span className="w-2 h-2 rounded-full" style={{ backgroundColor: getNamespaceColor(m.namespace) }} />
        {m.namespace}
      </p>
      <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-[11px] text-gray-500 mt-3">
        <dt>Created</dt><dd className="text-gray-400">{fmt(m.created_at)}</dd>
        <dt>Updated</dt><dd className="text-gray-400">{fmt(m.updated_at)}</dd>
        <dt>Last accessed</dt><dd className="text-gray-400">{fmt(m.last_accessed)}</dd>
        <dt>Accesses</dt><dd className="text-gray-400">{m.access_count}</dd>
      </dl>

      <Section title="Content">
        <MemoryBody body={m.content} titleIndex={titleIndex} onSelect={onSelect} />
      </Section>
      <Relations out={detail.relations.out} incoming={detail.relations.in} onSelect={onSelect} />
      <Claims claims={detail.claims} />
      <Tripwires wires={detail.tripwires} />
      <Section title="Recall history">
        <RecallSparkline accesses={detail.accesses} />
      </Section>
      {m.tags.length > 0 && (
        <Section title="Tags">
          <div className="flex flex-wrap gap-1">
            {m.tags.map((t) => (
              <span key={t} className="text-[10px] px-2 py-0.5 rounded-full bg-gray-800 text-gray-400">{t}</span>
            ))}
          </div>
        </Section>
      )}
      <p className="mt-5 text-[10px] text-gray-600 font-mono break-all">{m.id}</p>
    </>
  )
}
