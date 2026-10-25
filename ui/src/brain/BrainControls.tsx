import { useState } from 'react'
import { ChevronDown, ChevronUp, Maximize2, SlidersHorizontal } from 'lucide-react'
import { getNamespaceColor } from '../lib/colors'
import type { ColorMode } from './visuals'

export interface BrainSettings {
  mode: ColorMode
  showTags: boolean
  hiddenNs: Set<string>
  lobeStrength: number
}

interface Props {
  settings: BrainSettings
  onChange: (next: BrainSettings) => void
  namespaces: { id: string; name: string; count: number }[]
  includeDates: boolean
  onIncludeDates: (on: boolean) => void
  onFit: () => void
  shifted?: boolean // slide left of the detail drawer
}

const MODES: ColorMode[] = ['type', 'namespace', 'trust']

export default function BrainControls({
  settings, onChange, namespaces, includeDates, onIncludeDates, onFit, shifted,
}: Props) {
  const [open, setOpen] = useState(true)
  const set = (patch: Partial<BrainSettings>) => onChange({ ...settings, ...patch })

  const toggleNs = (id: string) => {
    const hidden = new Set(settings.hiddenNs)
    if (hidden.has(id)) hidden.delete(id)
    else hidden.add(id)
    set({ hiddenNs: hidden })
  }

  return (
    <div className={`absolute top-3 ${shifted ? 'right-[29rem]' : 'right-3'} z-10 w-64 bg-gray-900/90 backdrop-blur-sm border border-gray-800 rounded-xl text-xs text-gray-300`}>
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center justify-between px-3 py-2 font-semibold text-gray-200"
      >
        <span className="flex items-center gap-2"><SlidersHorizontal size={13} /> View</span>
        {open ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
      </button>
      {open && (
        <div className="px-3 pb-3 space-y-3 border-t border-gray-800 pt-3">
          <div>
            <div className="text-[10px] uppercase tracking-wider text-gray-500 mb-1">Color by</div>
            <div className="flex bg-gray-950 rounded-lg p-0.5 border border-gray-800">
              {MODES.map((m) => (
                <button
                  key={m}
                  onClick={() => set({ mode: m })}
                  className={`flex-1 px-2 py-1 rounded-md capitalize ${
                    settings.mode === m ? 'bg-blue-600 text-white' : 'text-gray-400 hover:text-white'
                  }`}
                >
                  {m}
                </button>
              ))}
            </div>
          </div>

          <label className="flex items-center gap-2 cursor-pointer">
            <input type="checkbox" checked={settings.showTags}
              onChange={(e) => set({ showTags: e.target.checked })} />
            Show tag links
          </label>
          <label className="flex items-center gap-2 cursor-pointer">
            <input type="checkbox" checked={includeDates}
              onChange={(e) => onIncludeDates(e.target.checked)} />
            Include date tags
          </label>

          <div>
            <div className="flex justify-between text-[10px] uppercase tracking-wider text-gray-500 mb-1">
              <span>Lobe pull</span><span>{settings.lobeStrength.toFixed(2)}</span>
            </div>
            <input type="range" min={0} max={0.15} step={0.005} className="w-full"
              value={settings.lobeStrength}
              onChange={(e) => set({ lobeStrength: Number(e.target.value) })} />
          </div>

          <div>
            <div className="text-[10px] uppercase tracking-wider text-gray-500 mb-1">Namespaces</div>
            <div className="flex flex-wrap gap-1 max-h-32 overflow-y-auto">
              {namespaces.map((ns) => {
                const off = settings.hiddenNs.has(ns.id)
                return (
                  <button
                    key={ns.id}
                    onClick={() => toggleNs(ns.id)}
                    title={`${ns.count} memories`}
                    className={`flex items-center gap-1 px-2 py-0.5 rounded-full border ${
                      off ? 'border-gray-800 text-gray-600' : 'border-gray-600 text-gray-200'
                    }`}
                  >
                    <span className="w-2 h-2 rounded-full"
                      style={{ backgroundColor: off ? '#374151' : getNamespaceColor(ns.name) }} />
                    {ns.name}
                  </button>
                )
              })}
            </div>
          </div>

          <button onClick={onFit}
            className="w-full flex items-center justify-center gap-1.5 px-2 py-1.5 rounded-lg bg-gray-800 hover:bg-gray-700 text-gray-200">
            <Maximize2 size={12} /> Fit to view
          </button>
        </div>
      )}
    </div>
  )
}
