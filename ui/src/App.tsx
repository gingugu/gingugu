import { useEffect, useMemo, useState } from 'react'
import { useBrainData } from './api/useBrainData'
import Brain3D from './brain/Brain3D'
import { toBrain, toLiteMemories } from './brain/model'
import Dashboard from './components/Dashboard'
import DecayHeatmap from './components/DecayHeatmap'
import TimelineView from './components/TimelineView'
import DetailDrawer from './detail/DetailDrawer'
import ListView from './list/ListView'
import Header from './shell/Header'
import type { ViewTab } from './types'
import { readDeepLink, writeDeepLink } from './shell/deepLink'

export default function App() {
  const [link] = useState(() => readDeepLink(window.location.search))
  const [tab, setTab] = useState<ViewTab>(link.tab)
  const [selectedId, setSelectedId] = useState<string | null>(link.selectedId)
  // Keep the address bar a shareable link to what is on screen.
  useEffect(() => {
    const query = writeDeepLink({ tab, selectedId })
    window.history.replaceState(null, '', `${window.location.pathname}${query}`)
  }, [tab, selectedId])
  const [includeDates, setIncludeDates] = useState(false)
  const { source, info, graph, error, loading, refresh, fetchDetail } = useBrainData(includeDates)

  const brain = useMemo(() => (graph ? toBrain(graph) : null), [graph])
  const lite = useMemo(() => (graph ? toLiteMemories(graph) : []), [graph])

  return (
    <div className="h-screen flex flex-col bg-gray-950">
      <Header
        tab={tab} onTab={setTab} source={source} info={info}
        error={error} loading={loading} onRefresh={refresh}
      />
      <main className="flex-1 relative overflow-hidden">
        {!brain && (
          <div className="h-full flex items-center justify-center text-sm text-gray-500 px-6 text-center">
            {error ?? 'Loading the brain...'}
          </div>
        )}
        {brain && tab === 'brain' && (
          <Brain3D
            brain={brain}
            persona={info?.persona ?? null}
            selectedId={selectedId}
            onSelect={setSelectedId}
            includeDates={includeDates}
            onIncludeDates={setIncludeDates}
            drawerOpen={selectedId !== null}
          />
        )}
        {brain && tab === 'list' && (
          <ListView brain={brain} selectedId={selectedId} onSelect={setSelectedId} />
        )}
        {brain && tab === 'dashboard' && (
          <Dashboard memories={lite} namespaces={brain.namespaces} relationCount={graph?.relations.length ?? 0} />
        )}
        {brain && tab === 'timeline' && <TimelineView memories={lite} namespaces={brain.namespaces} />}
        {brain && tab === 'heatmap' && (
          <DecayHeatmap memories={lite} namespaces={brain.namespaces} onSelect={setSelectedId} />
        )}
        {brain && selectedId && (
          <DetailDrawer
            id={selectedId}
            titleIndex={brain.titleIndex}
            fetchDetail={fetchDetail}
            onSelect={setSelectedId}
            onClose={() => setSelectedId(null)}
          />
        )}
      </main>
    </div>
  )
}
