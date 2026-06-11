import { useState, useCallback, useEffect } from 'react'
import { Brain, LayoutDashboard, Network, Upload, RefreshCw } from 'lucide-react'
import { MemoryExport, Memory, ViewTab } from './types'
import KnowledgeGraph from './components/KnowledgeGraph'
import MemoryDetail from './components/MemoryDetail'
import Dashboard from './components/Dashboard'
import sampleData from './data/sample.json'

const EMPTY_EXPORT: MemoryExport = { format_version: 1, exported_at: '', namespaces: [], memories: [], relations: [] }

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
    setLoading(false)
  }, [])

  useEffect(() => { refresh() }, [refresh])

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
      } catch {
        alert('Invalid JSON file')
      }
    }
    reader.readAsText(file)
  }, [])

  return (
    <div className="h-screen flex flex-col bg-gray-950">
      {/* Header */}
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
            <span className="text-[10px] text-emerald-400 bg-emerald-950 px-2 py-0.5 rounded-full border border-emerald-800">
              LIVE
            </span>
          ) : (
            <span className="text-[10px] text-amber-400 bg-amber-950 px-2 py-0.5 rounded-full border border-amber-800">
              STATIC
            </span>
          )}
        </div>

        <div className="flex items-center gap-2">
          <button
            onClick={refresh}
            disabled={loading}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium text-gray-400 hover:text-white bg-gray-900 border border-gray-800 hover:border-gray-700 transition-all disabled:opacity-50"
            title="Refresh from live database"
          >
            <RefreshCw size={14} className={loading ? 'animate-spin' : ''} /> Refresh
          </button>
          {/* Tab toggles */}
          <div className="flex bg-gray-900 rounded-lg p-0.5 border border-gray-800">
            <button
              onClick={() => setTab('graph')}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium transition-all ${
                tab === 'graph'
                  ? 'bg-blue-600 text-white shadow-lg shadow-blue-600/20'
                  : 'text-gray-400 hover:text-white'
              }`}
            >
              <Network size={14} /> Graph
            </button>
            <button
              onClick={() => setTab('dashboard')}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium transition-all ${
                tab === 'dashboard'
                  ? 'bg-purple-600 text-white shadow-lg shadow-purple-600/20'
                  : 'text-gray-400 hover:text-white'
              }`}
            >
              <LayoutDashboard size={14} /> Dashboard
            </button>
          </div>

          {/* Upload */}
          <label className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium text-gray-400 hover:text-white bg-gray-900 border border-gray-800 hover:border-gray-700 cursor-pointer transition-all">
            <Upload size={14} /> Load JSON
            <input type="file" accept=".json" onChange={handleFileUpload} className="hidden" />
          </label>
        </div>
      </header>

      {/* Content */}
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
      </main>
    </div>
  )
}
