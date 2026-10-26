import { describe, expect, it } from 'vitest'
import { apiUrl, fetchJson, memoryPath, sourceOf } from './client'

describe('apiUrl', () => {
  it('joins the base and the api prefix', () => {
    expect(apiUrl('/', '/graph')).toBe('/api/graph')
    expect(apiUrl('/gingugu/', '/info')).toBe('/gingugu/api/info')
  })

  it('encodes params, including the + in an ISO cursor', () => {
    expect(apiUrl('/', '/events', { after: '2026-10-20T10:00:00.000000+00:00' })).toBe(
      '/api/events?after=2026-10-20T10%3A00%3A00.000000%2B00%3A00',
    )
  })

  it('skips null, undefined and false; true becomes 1', () => {
    expect(apiUrl('/', '/graph', { dates: true, ns: undefined, q: null, x: false })).toBe(
      '/api/graph?dates=1',
    )
    expect(apiUrl('/', '/search', { q: 'a b', limit: 20 })).toBe('/api/search?q=a%20b&limit=20')
  })

  it('memoryPath encodes the id', () => {
    expect(memoryPath('abc-123')).toBe('/memory/abc-123')
    expect(memoryPath('../x')).toBe('/memory/..%2Fx')
  })
})

function fakeFetch(status: number, body: string, contentType = 'application/json') {
  return async () =>
    new Response(body, { status, headers: { 'content-type': contentType } })
}

describe('fetchJson', () => {
  it('returns the body on a 200 ok payload', async () => {
    const r = await fetchJson<{ ok: boolean; n: number }>('/x', fakeFetch(200, '{"ok":true,"n":3}'))
    expect(r).toEqual({ ok: true, status: 200, body: { ok: true, n: 3 } })
  })

  it('surfaces a JSON error body with its message and mode', async () => {
    const r = await fetchJson('/x', fakeFetch(502, '{"ok":false,"error":"brain unreachable","mode":"remote"}'))
    expect(r).toEqual({ ok: false, status: 502, error: 'brain unreachable', mode: 'remote', json: true })
  })

  it('treats HTML (no API behind the page) as not-json', async () => {
    const r = await fetchJson('/x', fakeFetch(200, '<!doctype html>', 'text/html'))
    expect(r).toMatchObject({ ok: false, json: false })
  })

  it('treats a network failure as status 0, not-json', async () => {
    const r = await fetchJson('/x', async () => {
      throw new TypeError('Failed to fetch')
    })
    expect(r).toMatchObject({ ok: false, status: 0, json: false })
  })
})

describe('sourceOf', () => {
  it('live when the API answered', () => {
    expect(sourceOf({ ok: true, status: 200, body: {} })).toBe('live')
  })
  it('error when the API answered with a JSON error - show it, never fake data', () => {
    expect(sourceOf({ ok: false, status: 503, error: 'no local brain', json: true })).toBe('error')
  })
  it('static when there is no API at all (Pages, or Vite without a backend)', () => {
    expect(sourceOf({ ok: false, status: 404, error: 'not json', json: false })).toBe('static')
    expect(sourceOf({ ok: false, status: 0, error: 'Failed to fetch', json: false })).toBe('static')
  })
})
