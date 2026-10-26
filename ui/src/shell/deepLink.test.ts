import { describe, expect, it } from 'vitest'
import { readDeepLink, writeDeepLink } from './deepLink'

describe('readDeepLink', () => {
  it('defaults to the brain with nothing selected', () => {
    expect(readDeepLink('')).toEqual({ tab: 'brain', selectedId: null })
  })

  it('reads the view and the selected memory', () => {
    expect(readDeepLink('?view=list&m=abc-123')).toEqual({ tab: 'list', selectedId: 'abc-123' })
  })

  it('ignores an unknown view and a malformed id', () => {
    expect(readDeepLink('?view=hax&m=../../x')).toEqual({ tab: 'brain', selectedId: null })
  })
})

describe('writeDeepLink', () => {
  it('omits defaults so the plain URL stays plain', () => {
    expect(writeDeepLink({ tab: 'brain', selectedId: null })).toBe('')
  })

  it('round-trips', () => {
    const state = { tab: 'heatmap' as const, selectedId: 'm-1' }
    expect(readDeepLink(writeDeepLink(state))).toEqual(state)
  })
})
