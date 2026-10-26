// `?view=<tab>&m=<memory id>`: a link straight to one view and one memory.
import type { ViewTab } from '../types'

const VIEWS: readonly ViewTab[] = ['brain', 'list', 'dashboard', 'timeline', 'heatmap']
// Same shape the API accepts for a memory id - anything else is ignored.
const MEMORY_ID = /^[A-Za-z0-9_-]{1,128}$/

export interface DeepLink {
  tab: ViewTab
  selectedId: string | null
}

export function readDeepLink(search: string): DeepLink {
  const params = new URLSearchParams(search)
  const view = params.get('view') as ViewTab | null
  const id = params.get('m')
  return {
    tab: view && VIEWS.includes(view) ? view : 'brain',
    selectedId: id && MEMORY_ID.test(id) ? id : null,
  }
}

export function writeDeepLink({ tab, selectedId }: DeepLink): string {
  const params = new URLSearchParams()
  if (tab !== 'brain') params.set('view', tab)
  if (selectedId) params.set('m', selectedId)
  const query = params.toString()
  return query ? `?${query}` : ''
}
