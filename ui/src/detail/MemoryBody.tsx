// Markdown body with [[Title]] wiki links. No raw HTML: react-markdown without
// rehype-raw escapes it, which matters because memory bodies are untrusted text.
import { useMemo } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { splitWikiLinks } from './links'

const MEM = '#mem-'

/** Resolved links become markdown links the `a` renderer intercepts. */
export function linkify(body: string, titleIndex: Map<string, string>): string {
  return splitWikiLinks(body, titleIndex)
    .map((seg) => {
      if (seg.kind === 'text') return seg.text
      return seg.id ? `[${seg.title}](${MEM}${encodeURIComponent(seg.id)})` : `[${seg.title}](#nomem)`
    })
    .join('')
}

interface Props {
  body: string
  titleIndex: Map<string, string>
  onSelect: (id: string) => void
}

export default function MemoryBody({ body, titleIndex, onSelect }: Props) {
  const source = useMemo(() => linkify(body, titleIndex), [body, titleIndex])
  return (
    <div className="memory-md text-sm text-gray-300 leading-relaxed break-words">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ href, children }) => {
            if (href?.startsWith(MEM)) {
              const id = decodeURIComponent(href.slice(MEM.length))
              return (
                <button onClick={() => onSelect(id)}
                  className="text-blue-400 hover:text-blue-300 underline decoration-dotted">
                  {children}
                </button>
              )
            }
            if (href === '#nomem') return <span className="text-gray-600">[[{children}]]</span>
            return (
              <a href={href} target="_blank" rel="noreferrer noopener"
                className="text-blue-400 hover:text-blue-300 underline">
                {children}
              </a>
            )
          },
          code: ({ children }) => (
            <code className="bg-gray-800 rounded-sm px-1 py-0.5 text-[12px] text-gray-200">{children}</code>
          ),
          pre: ({ children }) => (
            <pre className="bg-gray-900 border border-gray-800 rounded-lg p-3 overflow-x-auto my-2 text-[12px]">
              {children}
            </pre>
          ),
          p: ({ children }) => <p className="my-2">{children}</p>,
          ul: ({ children }) => <ul className="list-disc pl-5 my-2 space-y-1">{children}</ul>,
          ol: ({ children }) => <ol className="list-decimal pl-5 my-2 space-y-1">{children}</ol>,
          h1: ({ children }) => <h1 className="text-base font-bold text-white mt-3 mb-1">{children}</h1>,
          h2: ({ children }) => <h2 className="text-sm font-bold text-white mt-3 mb-1">{children}</h2>,
          h3: ({ children }) => <h3 className="text-sm font-semibold text-gray-100 mt-2 mb-1">{children}</h3>,
          blockquote: ({ children }) => (
            <blockquote className="border-l-2 border-gray-700 pl-3 text-gray-400 my-2">{children}</blockquote>
          ),
          table: ({ children }) => (
            <div className="overflow-x-auto my-2"><table className="text-xs border-collapse">{children}</table></div>
          ),
          th: ({ children }) => <th className="border border-gray-700 px-2 py-1 text-left">{children}</th>,
          td: ({ children }) => <td className="border border-gray-800 px-2 py-1">{children}</td>,
        }}
      >
        {source}
      </ReactMarkdown>
    </div>
  )
}
