// Thin fetch layer over the Explorer API. Never throws: every outcome is a value.

export type Params = Record<string, string | number | boolean | null | undefined>

export type FetchResult<T> =
  | { ok: true; status: number; body: T }
  | { ok: false; status: number; error: string; mode?: string; json: boolean }

export type Source = 'live' | 'error' | 'static'

export const BASE: string = import.meta.env?.BASE_URL ?? '/'

export function apiUrl(base: string, path: string, params?: Params): string {
  const pairs: string[] = []
  for (const [key, value] of Object.entries(params ?? {})) {
    if (value === null || value === undefined || value === false) continue
    const text = value === true ? '1' : String(value)
    pairs.push(`${encodeURIComponent(key)}=${encodeURIComponent(text)}`)
  }
  return `${base}api${path}${pairs.length ? `?${pairs.join('&')}` : ''}`
}

export function memoryPath(id: string): string {
  return `/memory/${encodeURIComponent(id)}`
}

export async function fetchJson<T>(
  url: string,
  fetchImpl: typeof fetch = fetch,
): Promise<FetchResult<T>> {
  let res: Response
  try {
    res = await fetchImpl(url)
  } catch (err) {
    return {
      ok: false,
      status: 0,
      error: err instanceof Error ? err.message : 'network error',
      json: false,
    }
  }
  const type = res.headers.get('content-type') ?? ''
  if (!type.includes('json')) {
    return { ok: false, status: res.status, error: 'not json', json: false }
  }
  let body: unknown
  try {
    body = await res.json()
  } catch {
    return { ok: false, status: res.status, error: 'bad json', json: false }
  }
  const obj = body as { ok?: boolean; error?: string; mode?: string }
  if (res.ok && obj.ok !== false) return { ok: true, status: res.status, body: body as T }
  return {
    ok: false,
    status: res.status,
    error: obj.error ?? `HTTP ${res.status}`,
    mode: obj.mode,
    json: true,
  }
}

export function sourceOf(result: FetchResult<unknown>): Source {
  if (result.ok) return 'live'
  return result.json ? 'error' : 'static'
}

export const api = <T>(path: string, params?: Params) =>
  fetchJson<T>(apiUrl(BASE, path, params))
