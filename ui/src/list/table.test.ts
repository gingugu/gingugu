import { describe, expect, it } from 'vitest'
import { toBrain } from '../brain/model'
import { GRAPH, NOW } from '../test/fixtures'
import { filterSortNodes } from './table'

const nodes = toBrain(GRAPH, NOW).nodes
const ids = (xs: { id: string }[]) => xs.map((x) => x.id)

describe('filterSortNodes', () => {
  it('empty text keeps everything', () => {
    expect(filterSortNodes(nodes, { text: '', sort: 'title', dir: 'asc' })).toHaveLength(5)
  })

  it('matches title, tag or namespace, case-insensitively, every word required', () => {
    expect(ids(filterSortNodes(nodes, { text: 'STACK', sort: 'title', dir: 'asc' }))).toEqual([
      'm-rule',
    ])
    expect(ids(filterSortNodes(nodes, { text: 'sqlite', sort: 'title', dir: 'asc' }))).toEqual([
      'm-old',
      'm-schema',
    ])
    expect(filterSortNodes(nodes, { text: 'crow git', sort: 'title', dir: 'asc' })).toHaveLength(2)
    expect(filterSortNodes(nodes, { text: 'crow sqlite', sort: 'title', dir: 'asc' })).toHaveLength(0)
  })

  it('sorts by access descending', () => {
    expect(ids(filterSortNodes(nodes, { text: '', sort: 'access', dir: 'desc' }))).toEqual([
      'm-rule',
      'm-dated',
      'm-lesson',
      'm-old',
      'm-schema',
    ])
  })

  it('sorts by updated ascending and by namespace then title', () => {
    expect(ids(filterSortNodes(nodes, { text: '', sort: 'updated', dir: 'asc' }))[0]).toBe('m-schema')
    expect(ids(filterSortNodes(nodes, { text: '', sort: 'namespace', dir: 'asc' }))).toEqual([
      'm-rule',
      'm-lesson',
      'm-old',
      'm-schema',
      'm-dated',
    ])
  })

  it('sorts by health with the least trusted first ascending', () => {
    expect(ids(filterSortNodes(nodes, { text: '', sort: 'health', dir: 'asc' }))[0]).toBe('m-old')
  })

  it('never mutates its input', () => {
    const before = ids(nodes)
    filterSortNodes(nodes, { text: '', sort: 'access', dir: 'desc' })
    expect(ids(nodes)).toEqual(before)
  })
})
