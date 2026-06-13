import { useState, useCallback, useEffect, useRef } from 'react'
import {
  Brain, LayoutDashboard, Network, Upload, RefreshCw, Activity, Flame, Clock,
  type LucideIcon,
} from 'lucide-react'
import { MemoryExport, Memory, ViewTab } from './types'
import KnowledgeGraph from './components/KnowledgeGraph'
import MemoryDetail from './components/MemoryDetail'
import Dashboard from './components/Dashboard'
import DecayHeatmap from './components/DecayHeatmap'
import TimelineView from './components/TimelineView'
import sampleData from './data/sample.json'

const EMPTY_EXPORT: MemoryExport = { format_version: 1, exported_at: '', namespaces: [], memories: [], relations: [] }

type RefreshInterval = 0 | 5_000 | 30_000 | 60_000

const REFRESH_OPTIONS: { value: RefreshInterval; label: string }[] = [
  { value: 0, label: 'Off' },
  { value: 5_000, label: '5s' },
  { value: 30_000, label: '30s' },
  { value: 60_000, label: '1m' },
]

async function fetchLiveData(): Promise<MemoryExport | null> {
  try {
    const res = await fetch('/api/export')
    if (!res.ok) return null
    return await res.json()
  } catch {
    return null
  }
}

export default function App() {
  const [data, setData] = useState<MemoryExport>(EMPTY_EXPORT)
  const [tab, setTab] = useState<ViewTab>('graph')
  const [selected, setSelected] = useState<Memory | null>(null)
  const [live, setLive] = useState(false)
  const [loading, setLoading] = useState(true)
  const [refreshInterval, setRefreshInterval] = useState<RefreshInterval>(0)
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null)
  const intervalRef = useRef<number | null>(null)

  const refresh = useCallback(async () => {
    setLoading(true)
    const liveData = await fetchLiveData()
    if (liveData) {
      setData(liveData)
      setLive(true)
    } else {
      setData(sampleData as unknown as MemoryExport)
      setLive(false)
    }
    setLastRefresh(new Date())
    setLoading(false)
  }, [])

  useEffect(() => { refresh() }, [refresh])

  // Auto-refresh — only fires when live mode is active and interval > 0.
  useEffect(() => {
    if (intervalRef.current !== null) {
      window.clearInterval(intervalRef.current)
      intervalRef.current = null
    }
    if (refreshInterval > 0 && live) {
      intervalRef.current = window.setInterval(() => {
        refresh()
      }, refreshInterval)
    }
    return () => {
      if (intervalRef.current !== null) {
        window.clearInterval(intervalRef.current)
        intervalRef.current = null
      }
    }
  }, [refreshInterval, live, refresh])

  const handleFileUpload = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    const reader = new FileReader()
    reader.onload = (ev) => {
      try {
        const parsed = JSON.parse(ev.target?.result as string)
        const exportData = parsed.export ?? parsed
        setData(exportData)
        setSelected(null)
        setLive(false)
        setRefreshInterval(0)
      } catch {
        alert('Invalid JSON file')
      }
    }
    reader.readAsText(file)
  }, [])

  const tabs: { key: ViewTab; label: string; icon: LucideIcon; activeClass: string }[] = [
    { key: 'graph', label: 'Graph', icon: Network, activeClass: 'bg-blue-600 shadow-blue-600/20' },
    { key: 'dashboard', label: 'Dashboard', icon: LayoutDashboard, activeClass: 'bg-purple-600 shadow-purple-600/20' },
    { key: 'timeline', label: 'Timeline', icon: Clock, activeClass: 'bg-cyan-600 shadow-cyan-600/20' },
    { key: 'heatmap', label: 'Trust Map', icon: Flame, activeClass: 'bg-orange-600 shadow-orange-600/20' },
  ]

  return (
    <div className="h-screen flex flex-col bg-gray-950">
      <header className="flex items-center justify-between px-6 py-3 border-b border-gray-800 bg-gray-950/90 backdrop-blur-sm">
        <div className="flex items-center gap-3">
          <Brain className="text-blue-500" size={24} />
          <h1 className="text-lg font-bold text-white tracking-tight">Memory Explorer</h1>
          {loading ? (
            <span className="text-[10px] text-gray-500 bg-gray-800 px-2 py-0.5 rounded-full animate-pulse">
              loading...
            </span>
          ) : (
            <span className="text-[10px] text-gray-600 bg-gray-800 px-2 py-0.5 rounded-full">
              {data.memories.length} memories
            </span>
          )}
          {live ? (
            <span className="text-[10px] text-emerald-400 bg-emerald-950 px-2 py-0.5 rounded-full border border-emerald-800 flex items-center gap-1">
              <Activity size={10} className={refreshInterval > 0 ? 'animate-pulse' : ''} />
              LIVE
            </span>
          ) : (
            <span className="text-[10px] text-amber-400 bg-amber-950 px-2 py-0.5 rounded-full border border-amber-800">
              STATIC
            </span>
          )}
          {lastRefresh && (
            <span className="text-[10px] text-gray-600">
              updated {lastRefresh.toLocaleTimeString()}
            </span>
          )}
        </div>

        <div className="flex items-center gap-2">
          <div className="flex items-center bg-gray-900 rounded-lg p-0.5 border border-gray-800">
            <button
              onClick={refresh}
              disabled={loading}
              className="flex items-center gap-1.5 px-2.5 py-1 rounded text-xs font-medium text-gray-400 hover:text-white transition-all disabled:opacity-50"
              title="Refresh now"
            >
              <RefreshCw size={12} className={loading ? 'animate-spin' : ''} />
            </button>
            <select
              value={refreshInterval}
              onChange={(e) => setRefreshInterval(Number(e.target.value) as RefreshInterval)}
              disabled={!live}
              title={live ? 'Auto-refresh interval' : 'Auto-refresh requires live mode'}
              className="bg-transparent text-xs text-gray-400 px-1 py-1 border-l border-gray-800 focus:outline-none disabled:opacity-40"
            >
              {REFRESH_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value} className="bg-gray-900">{opt.label}</option>
              ))}
            </select>
          </div>

          <div className="flex bg-gray-900 rounded-lg p-0.5 border border-gray-800">
            {tabs.map(({ key, label, icon: Icon, activeClass }) => (
              <button
                key={key}
                onClick={() => setTab(key)}
                className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium transition-all ${
                  tab === key ? `${activeClass} text-white shadow-lg` : 'text-gray-400 hover:text-white'
                }`}
              >
                <Icon size={14} /> {label}
              </button>
            ))}
          </div>

          <label className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium text-gray-400 hover:text-white bg-gray-900 border border-gray-800 hover:border-gray-700 cursor-pointer transition-all">
            <Upload size={14} /> Load JSON
            <input type="file" accept=".json" onChange={handleFileUpload} className="hidden" />
          </label>
        </div>
      </header>

      <main className="flex-1 relative overflow-hidden">
        {tab === 'graph' && (
          <>
            <KnowledgeGraph
              memories={data.memories}
              namespaces={data.namespaces}
              relations={data.relations}
              onSelectMemory={setSelected}
            />
            {selected && (
              <MemoryDetail
                memory={selected}
                namespaces={data.namespaces}
                onClose={() => setSelected(null)}
              />
            )}
          </>
        )}
        {tab === 'dashboard' && (
          <Dashboard
            memories={data.memories}
            namespaces={data.namespaces}
            relations={data.relations}
          />
        )}
        {tab === 'timeline' && (
          <TimelineView memories={data.memories} namespaces={data.namespaces} />
        )}
        {tab === 'heatmap' && (
          <DecayHeatmap memories={data.memories} namespaces={data.namespaces} />
        )}
      </main>
    </div>
  )
}
