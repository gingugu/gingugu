import { Activity, AlertTriangle } from 'lucide-react'
import type { Source } from '../api/client'
import type { InfoPayload } from '../api/types'

interface Props {
  source: Source | null
  info: InfoPayload | null
  error: string | null
}

const BASE = 'text-[10px] px-2 py-0.5 rounded-full border flex items-center gap-1 max-w-md'

export default function SourceBadge({ source, info, error }: Props) {
  if (source === 'error' || (source === null && error)) {
    return (
      <span className={`${BASE} text-red-400 bg-red-950 border-red-800`} title={error ?? ''}>
        <AlertTriangle size={10} className="shrink-0" />
        <span className="truncate">ERROR {error}</span>
      </span>
    )
  }
  if (source === 'static') {
    return <span className={`${BASE} text-amber-400 bg-amber-950 border-amber-800`}>STATIC demo</span>
  }
  if (source === 'live' && info?.mode === 'remote') {
    return (
      <span className={`${BASE} text-cyan-400 bg-cyan-950 border-cyan-800`}>
        <Activity size={10} /> REMOTE {info.brain}
      </span>
    )
  }
  if (source === 'live') {
    return (
      <span className={`${BASE} text-emerald-400 bg-emerald-950 border-emerald-800`}>
        <Activity size={10} /> LOCAL
      </span>
    )
  }
  return null
}
