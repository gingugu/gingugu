// [[Title]] wiki links in memory bodies.

export type Segment =
  | { kind: 'text'; text: string }
  | { kind: 'link'; title: string; id: string | null }

const WIKI = /\[\[([^\[\]]*?)\]\]/g

export function splitWikiLinks(text: string, titleIndex: Map<string, string>): Segment[] {
  const out: Segment[] = []
  let last = 0
  for (const m of text.matchAll(WIKI)) {
    const title = m[1].trim()
    if (!title) continue
    const at = m.index ?? 0
    if (at > last) out.push({ kind: 'text', text: text.slice(last, at) })
    out.push({ kind: 'link', title, id: titleIndex.get(title.toLowerCase()) ?? null })
    last = at + m[0].length
  }
  if (last < text.length || out.length === 0) out.push({ kind: 'text', text: text.slice(last) })
  return out
}
