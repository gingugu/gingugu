import { describe, expect, it } from 'vitest'
import { splitWikiLinks } from './links'

const INDEX = new Map([
  ['never delete a stack parent branch', 'm-rule'],
  ['rule: recall memory before acting', 'm-recall'],
])

describe('splitWikiLinks', () => {
  it('plain text is one text segment', () => {
    expect(splitWikiLinks('just words', INDEX)).toEqual([{ kind: 'text', text: 'just words' }])
  })

  it('resolves [[Title]] case-insensitively and keeps the text around it', () => {
    expect(splitWikiLinks('see [[Never Delete a Stack Parent Branch]] first', INDEX)).toEqual([
      { kind: 'text', text: 'see ' },
      { kind: 'link', title: 'Never Delete a Stack Parent Branch', id: 'm-rule' },
      { kind: 'text', text: ' first' },
    ])
  })

  it('an unknown title is a link with no id', () => {
    expect(splitWikiLinks('[[Not a memory]]', INDEX)).toEqual([
      { kind: 'link', title: 'Not a memory', id: null },
    ])
  })

  it('handles several links and titles with colons', () => {
    const out = splitWikiLinks(
      '[[RULE: recall memory before acting]] and [[never delete a stack parent branch]]',
      INDEX,
    )
    expect(out.filter((s) => s.kind === 'link').map((s) => (s as { id: string }).id)).toEqual([
      'm-recall',
      'm-rule',
    ])
  })

  it('an unclosed or empty bracket stays text', () => {
    expect(splitWikiLinks('broken [[half', INDEX)).toEqual([{ kind: 'text', text: 'broken [[half' }])
    expect(splitWikiLinks('empty [[]] here', INDEX)).toEqual([
      { kind: 'text', text: 'empty [[]] here' },
    ])
  })

  it('trims whitespace inside the brackets for lookup', () => {
    const [seg] = splitWikiLinks('[[  never delete a stack parent branch ]]', INDEX)
    expect(seg).toEqual({ kind: 'link', title: 'never delete a stack parent branch', id: 'm-rule' })
  })
})
