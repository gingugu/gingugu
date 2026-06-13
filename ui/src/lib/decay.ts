// Mirrors src/gingugu/decay.py for UI-side scoring without round-tripping
// the API. Keep weights/lambda/floor in sync with config + decay defaults.

import { Memory, DecayScored } from '../types'

const DEFAULT_LAMBDA = 0.01
const ACCESS_SATURATION = 50
// Freshness never decays below this — dormant is not worthless (see decay.py).
const FRESHNESS_FLOOR = 0.35
// Untouched longer than this = dormant (a badge, not a penalty).
export const DORMANT_AFTER_DAYS = 90

const CONFIDENCE_WEIGHT: Record<string, number> = {
  verified: 1.0,
  inferred: 0.7,
  stale: 0.3,
  deprecated: 0.0,
}

// Trust-centric weights: confidence dominates, freshness is a soft signal.
// Renormalized from config defaults: freshness 0.10, access 0.10, confidence 0.35.
const W = {
  freshness: 0.1 / 0.55,
  access: 0.1 / 0.55,
  confidence: 0.35 / 0.55,
}

function daysBetween(iso: string | null | undefined, now: Date): number {
  if (!iso) return 0
  const t = new Date(iso).getTime()
  if (Number.isNaN(t)) return 0
  return Math.max(0, (now.getTime() - t) / 86_400_000)
}

function freshnessScore(days: number, lambda = DEFAULT_LAMBDA): number {
  const raw = Math.exp(-lambda * Math.max(0, days))
  return FRESHNESS_FLOOR + (1 - FRESHNESS_FLOOR) * raw
}

function accessScore(count: number): number {
  if (count <= 0) return 0
  return Math.min(1, Math.log(count + 1) / Math.log(ACCESS_SATURATION))
}

function confidenceScore(confidence: string): number {
  return CONFIDENCE_WEIGHT[confidence] ?? 0
}

export function scoreMemory(m: Memory, now: Date = new Date()): DecayScored {
  const anchor = m.last_confirmed || m.updated_at || m.created_at
  const days = daysBetween(anchor, now)
  const daysAccessed = daysBetween(m.last_accessed || m.updated_at || m.created_at, now)
  const fresh = freshnessScore(days)
  const acc = accessScore(m.access_count)
  const conf = confidenceScore(m.confidence)
  const health = W.freshness * fresh + W.access * acc + W.confidence * conf
  return {
    memory: m,
    health,
    freshness: fresh,
    access: acc,
    confidence: conf,
    daysSinceConfirmed: days,
    daysSinceAccessed: daysAccessed,
    dormant: daysAccessed >= DORMANT_AFTER_DAYS,
  }
}

export function scoreAll(memories: Memory[], now: Date = new Date()): DecayScored[] {
  return memories.map((m) => scoreMemory(m, now))
}

// Tailwind-ish gradient: green (1.0) -> yellow (0.5) -> red (0.0).
export function healthColor(health: number): string {
  const h = Math.max(0, Math.min(1, health))
  // green-500 #22c55e (34,197,94) -> yellow-500 #eab308 (234,179,8) -> red-500 #ef4444 (239,68,68)
  if (h >= 0.5) {
    const t = (h - 0.5) * 2
    const r = Math.round(234 + (34 - 234) * t)
    const g = Math.round(179 + (197 - 179) * t)
    const b = Math.round(8 + (94 - 8) * t)
    return `rgb(${r}, ${g}, ${b})`
  }
  const t = h * 2
  const r = Math.round(239 + (234 - 239) * t)
  const g = Math.round(68 + (179 - 68) * t)
  const b = Math.round(68 + (8 - 68) * t)
  return `rgb(${r}, ${g}, ${b})`
}
