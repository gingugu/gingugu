import type { ReactNode } from 'react'
import type { ClaimRow, LinkedMemory, TripwireRow } from '../api/types'
import { RELATION_COLORS } from '../lib/colors'

export function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="mt-5">
      <h3 className="text-[10px] font-semibold text-gray-500 uppercase tracking-wider mb-2">{title}</h3>
      {children}
    </section>
  )
}

function LinkList({ items, arrow, onSelect }: {
  items: LinkedMemory[]
  arrow: string
  onSelect: (id: string) => void
}) {
  return (
    <>
      {items.map((r) => (
        <li key={`${r.id}-${r.type}`} className="flex items-baseline gap-2">
          <span className="text-[10px] font-medium shrink-0" style={{ color: RELATION_COLORS[r.type] ?? '#6b7280' }}>
            {arrow} {r.type.replace('_', ' ')}
          </span>
          <button onClick={() => onSelect(r.id)}
            className="text-left text-sm text-gray-300 hover:text-white truncate">
            {r.title}
          </button>
        </li>
      ))}
    </>
  )
}

export function Relations({ out, incoming, onSelect }: {
  out: LinkedMemory[]
  incoming: LinkedMemory[]
  onSelect: (id: string) => void
}) {
  if (out.length + incoming.length === 0) return null
  return (
    <Section title="Relations">
      <ul className="space-y-1">
        <LinkList items={out} arrow="→" onSelect={onSelect} />
        <LinkList items={incoming} arrow="←" onSelect={onSelect} />
      </ul>
    </Section>
  )
}

export function Claims({ claims }: { claims: ClaimRow[] }) {
  if (claims.length === 0) return null
  return (
    <Section title="Claims">
      <ul className="space-y-1">
        {claims.map((c, i) => (
          <li key={i} className="text-xs flex gap-2 items-baseline">
            <span className="text-gray-500 shrink-0">{c.kind}</span>
            <code className="text-gray-300 truncate">{c.ref}</code>
            <span className="ml-auto shrink-0 text-gray-500">{c.resolved_state ?? c.state}</span>
          </li>
        ))}
      </ul>
    </Section>
  )
}

export function Tripwires({ wires }: { wires: TripwireRow[] }) {
  if (wires.length === 0) return null
  return (
    <Section title="Tripwires">
      <ul className="space-y-1">
        {wires.map((w) => (
          <li key={w.id} className="text-xs text-gray-300">
            <code>{w.tool_pattern}</code>
            {w.input_pattern && <span className="text-gray-500"> when </span>}
            {w.input_pattern && <code>{w.input_pattern}</code>}
          </li>
        ))}
      </ul>
    </Section>
  )
}
