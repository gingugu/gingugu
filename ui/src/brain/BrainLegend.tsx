import { TYPE_COLORS, getNamespaceColor } from '../lib/colors'
import type { ColorMode } from './visuals'

interface Props {
  mode: ColorMode
  namespaces: { id: string; name: string }[]
}

function Dot({ color, label }: { color: string; label: string }) {
  return (
    <span className="flex items-center gap-1.5">
      <span className="w-2 h-2 rounded-full" style={{ backgroundColor: color }} />
      {label}
    </span>
  )
}

export default function BrainLegend({ mode, namespaces }: Props) {
  return (
    <div className="absolute bottom-3 left-3 z-10 max-w-md bg-gray-900/80 backdrop-blur-sm border border-gray-800 rounded-xl px-3 py-2 text-[11px] text-gray-400">
      <div className="flex flex-wrap gap-x-3 gap-y-1">
        {mode === 'type' &&
          Object.entries(TYPE_COLORS).map(([t, c]) => <Dot key={t} color={c} label={t} />)}
        {mode === 'namespace' &&
          namespaces.map((n) => <Dot key={n.id} color={getNamespaceColor(n.name)} label={n.name} />)}
        {mode === 'trust' && (
          <span className="flex items-center gap-2">
            Low
            <span className="h-2 w-32 rounded-full" style={{
              background: 'linear-gradient(to right, rgb(239,68,68), rgb(234,179,8), rgb(34,197,94))',
            }} />
            High trust
          </span>
        )}
      </div>
      <div className="mt-1 text-gray-600">
        size = recall count · glow = trust · faint = dormant or deprecated · halo = pinned
      </div>
    </div>
  )
}
