// Memory bodies are untrusted text written by agents or imported. Rendering one
// must never reach out to the network or crash the drawer.
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import MemoryBody from './MemoryBody'

const render = (body: string) =>
  renderToStaticMarkup(
    <MemoryBody body={body} titleIndex={new Map([['known', 'm-1']])} onSelect={() => {}} />,
  )

describe('MemoryBody', () => {
  it('never renders a remote image - a pixel would phone home on view', () => {
    const html = render('look ![tracker](https://attacker.example/p.gif?who=me) here')
    expect(html).not.toContain('<img')
    expect(html).not.toContain('attacker.example')
    expect(html).toContain('tracker') // the alt text survives
  })

  it('external links open safely and show where they go', () => {
    const html = render('[docs](https://example.org/x)')
    expect(html).toContain('rel="noreferrer noopener"')
    expect(html).toContain('title="https://example.org/x"')
  })

  it('script urls are never clickable', () => {
    const html = render('[click](javascript:alert(1))')
    expect(html).not.toContain('javascript:')
  })

  it('raw html in a body is text, not markup', () => {
    const html = render('<script>alert(1)</script><b>x</b>')
    expect(html).not.toContain('<script')
    expect(html).not.toContain('<b>')
  })

  it('a malformed memory link does not crash the drawer', () => {
    expect(() => render('[x](#mem-%E0%A4%A)')).not.toThrow()
  })

  it('a resolved wiki link becomes a button', () => {
    expect(render('see [[known]]')).toContain('<button')
  })
})
