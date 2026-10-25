import {
  Brain, Clock, Flame, LayoutDashboard, List, Network, RefreshCw, type LucideIcon,
} from 'lucide-react'
import type { Source } from '../api/client'
import type { InfoPayload } from '../api/types'
import type { ViewTab } from '../types'
import SourceBadge from './SourceBadge'

interface Props {
  tab: ViewTab
  onTab: (tab: ViewTab) => void
  source: Source | null
  info: InfoPayload | null
  error: string | null
  loading: boolean
  onRefresh: () => void
}

const TABS: { key: ViewTab; label: string; icon: LucideIcon; active: string }[] = [
  { key: 'brain', label: 'Brain', icon: Network, active: 'bg-blue-600 shadow-blue-600/20' },
  { key: 'list', label: 'List', icon: List, active: 'bg-emerald-600 shadow-emerald-600/20' },
  { key: 'dashboard', label: 'Dashboard', icon: LayoutDashboard, active: 'bg-purple-600 shadow-purple-600/20' },
  { key: 'timeline', label: 'Timeline', icon: Clock, active: 'bg-cyan-600 shadow-cyan-600/20' },
  { key: 'heatmap', label: 'Trust Map', icon: Flame, active: 'bg-orange-600 shadow-orange-600/20' },
]

export default function Header({ tab, onTab, source, info, error, loading, onRefresh }: Props) {
  return (
    <header className="flex items-center justify-between px-6 py-3 border-b border-gray-800 bg-gray-950/90 backdrop-blur-xs">
      <div className="flex items-center gap-3 min-w-0">
        <img src={`${import.meta.env.BASE_URL}logo.svg`} alt="Gingugu" width={28} height={28} className="rounded-md" />
        <Brain className="text-blue-500" size={24} />
        <h1 className="text-lg font-bold text-white tracking-tight">Memory Explorer</h1>
        <SourceBadge source={source} info={info} error={error} />
        {info && (
          <span className="text-[10px] text-gray-500 bg-gray-800 px-2 py-0.5 rounded-full whitespace-nowrap">
            {info.memories} memories · {info.relations} relations
          </span>
        )}
      </div>

      <div className="flex items-center gap-2">
        <button
          onClick={onRefresh}
          disabled={loading}
          className="p-2 rounded-lg text-gray-400 hover:text-white bg-gray-900 border border-gray-800 disabled:opacity-50"
          title="Refresh now"
        >
          <RefreshCw size={13} className={loading ? 'animate-spin' : ''} />
        </button>
        <div className="flex bg-gray-900 rounded-lg p-0.5 border border-gray-800">
          {TABS.map(({ key, label, icon: Icon, active }) => (
            <button
              key={key}
              onClick={() => onTab(key)}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium transition-all ${
                tab === key ? `${active} text-white shadow-lg` : 'text-gray-400 hover:text-white'
              }`}
            >
              <Icon size={14} /> {label}
            </button>
          ))}
        </div>
      </div>
    </header>
  )
}
