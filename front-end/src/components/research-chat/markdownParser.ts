export type AuditBadgeKind =
  | "citation"
  | "table"
  | "calculator"
  | "kg"
  | "unverified"

export interface AuditBadgeToken {
  type: "badge"
  raw: string
  label: string
  kind: AuditBadgeKind
  slot?: string
  page?: number
  verified?: boolean
  index?: number
  refId?: string
  tier?: string
}

export type InlineToken =
  | { type: "text"; text: string }
  | { type: "bold"; children: InlineToken[] }
  | { type: "italic"; children: InlineToken[] }
  | { type: "code"; text: string }
  | { type: "link"; label: string; href: string }
  | AuditBadgeToken

export type MarkdownBlock =
  | { type: "heading"; level: 1 | 2 | 3 | 4; tokens: InlineToken[] }
  | { type: "paragraph"; tokens: InlineToken[] }
  | { type: "blockquote"; blocks: MarkdownBlock[] }
  | { type: "list"; ordered: boolean; items: InlineToken[][] }
  | { type: "code"; lang: string; content: string }
  | { type: "table"; headers: InlineToken[][]; rows: InlineToken[][][] }
  | { type: "hr" }

const SAFE_URL_RE = /^https?:\/\/[^\s<>"]+$/i

export function isSafeHttpUrl(url: string): boolean {
  return SAFE_URL_RE.test(url.trim())
}

export function classifyAuditBadge(innerRaw: string): AuditBadgeToken | null {
  const inner = innerRaw.trim()
  if (!inner) return null

  if (/^UNVERIFIED_CLAIM$/i.test(inner)) {
    return {
      type: "badge",
      raw: `[${innerRaw}]`,
      label: "UNVERIFIED_CLAIM",
      kind: "unverified",
      verified: false,
    }
  }

  // [S07·P168 ✓] or [S07_REV_COGS_DETAIL·P168 ✓] or [S07·P168] or [S07 P168 ✗]
  const slotPageMatch = inner.match(
    /^(S\d{2}(?:_[A-Z0-9_]+)?)\s*[·:\-\s]+\s*P?(\d+)(?:\s*([✓✗✔✕]|UNVERIFIED))?$/i,
  )
  if (slotPageMatch) {
    const slot = slotPageMatch[1].toUpperCase()
    const page = Number.parseInt(slotPageMatch[2], 10)
    const mark = slotPageMatch[3]
    const verified =
      mark === "✗" || mark === "✕" || mark?.toUpperCase() === "UNVERIFIED"
        ? false
        : true
    return {
      type: "badge",
      raw: `[${innerRaw}]`,
      label: inner,
      kind: "citation",
      slot,
      page,
      verified,
    }
  }

  // [Bảng #1], [Bảng 1], [Table #1], [Table 1]
  const tableMatch = inner.match(/^(?:Bảng|Table)\s*#?(\d+)$/i)
  if (tableMatch) {
    return {
      type: "badge",
      raw: `[${innerRaw}]`,
      label: inner,
      kind: "table",
      index: Number.parseInt(tableMatch[1], 10),
    }
  }

  // [Calc #1], [CALC-1], [calc_01]
  const calcMatch = inner.match(/^(?:Calc|CALC)\s*(?:#|-|_)?0*(\d+)$/i)
  if (calcMatch) {
    return {
      type: "badge",
      raw: `[${innerRaw}]`,
      label: inner,
      kind: "calculator",
      index: Number.parseInt(calcMatch[1], 10),
      refId: inner,
    }
  }

  // [KG: CONFIRMED_BOTH], [KG: TRUE_PEER], [KG #1]
  const kgMatch = inner.match(/^KG\s*(?::\s*([A-Z0-9_]+)|#(\d+))$/i)
  if (kgMatch) {
    const tier = kgMatch[1]?.toUpperCase()
    const index = kgMatch[2] ? Number.parseInt(kgMatch[2], 10) : undefined
    return {
      type: "badge",
      raw: `[${innerRaw}]`,
      label: inner,
      kind: "kg",
      tier,
      index,
    }
  }

  // [C1], [C12]
  const cidMatch = inner.match(/^C(\d+)$/i)
  if (cidMatch) {
    return {
      type: "badge",
      raw: `[${innerRaw}]`,
      label: inner.toUpperCase(),
      kind: "citation",
      refId: inner.toUpperCase(),
      index: Number.parseInt(cidMatch[1], 10),
      verified: true,
    }
  }

  // [1], [2] numeric citation marker (reasonable range 1..99)
  const numCidMatch = inner.match(/^(\d{1,2})$/)
  if (numCidMatch) {
    const n = Number.parseInt(numCidMatch[1], 10)
    if (n >= 1) {
      return {
        type: "badge",
        raw: `[${innerRaw}]`,
        label: `[${n}]`,
        kind: "citation",
        index: n,
        verified: true,
      }
    }
  }

  return null
}

export function tokenizeInline(input: string): InlineToken[] {
  const tokens: InlineToken[] = []
  let i = 0
  let textBuf = ""

  const flushText = () => {
    if (textBuf) {
      tokens.push({ type: "text", text: textBuf })
      textBuf = ""
    }
  }

  while (i < input.length) {
    // Inline code `...`
    if (input[i] === "`") {
      const end = input.indexOf("`", i + 1)
      if (end !== -1) {
        flushText()
        tokens.push({ type: "code", text: input.slice(i + 1, end) })
        i = end + 1
        continue
      }
    }

    // Bold **...**
    if (input[i] === "*" && input[i + 1] === "*") {
      const end = input.indexOf("**", i + 2)
      if (end !== -1 && end > i + 2) {
        flushText()
        tokens.push({ type: "bold", children: tokenizeInline(input.slice(i + 2, end)) })
        i = end + 2
        continue
      }
    }

    // Italic *...* (avoid matching list bullets or stray *)
    if (input[i] === "*" && input[i + 1] !== "*" && input[i + 1] !== " ") {
      const end = input.indexOf("*", i + 1)
      if (end !== -1 && end > i + 1 && input[end - 1] !== " ") {
        flushText()
        tokens.push({ type: "italic", children: tokenizeInline(input.slice(i + 1, end)) })
        i = end + 1
        continue
      }
    }

    // Bracketed expression: either [label](url) link OR audit badge [...]
    if (input[i] === "[") {
      const closeBracket = input.indexOf("]", i + 1)
      if (closeBracket !== -1) {
        const inner = input.slice(i + 1, closeBracket)
        if (input[closeBracket + 1] === "(") {
          const closeParen = input.indexOf(")", closeBracket + 2)
          if (closeParen !== -1) {
            const href = input.slice(closeBracket + 2, closeParen).trim()
            flushText()
            if (isSafeHttpUrl(href)) {
              tokens.push({ type: "link", label: inner, href })
            } else {
              // Strip unsafe scheme (e.g. javascript:, data:) safely to plain text
              tokens.push({ type: "text", text: inner })
            }
            i = closeParen + 1
            continue
          }
        }

        const badge = classifyAuditBadge(inner)
        if (badge) {
          flushText()
          tokens.push(badge)
          i = closeBracket + 1
          continue
        }
      }
    }

    textBuf += input[i]
    i += 1
  }

  flushText()
  return tokens
}

function splitTableRow(line: string): string[] {
  const trimmed = line.trim().replace(/^\|/, "").replace(/\|$/, "")
  return trimmed.split("|").map((cell) => cell.trim())
}

function isTableSeparator(line: string): boolean {
  const trimmed = line.trim()
  if (!trimmed.includes("|") && !trimmed.includes("-")) return false
  const cells = splitTableRow(trimmed)
  return cells.length >= 1 && cells.every((cell) => /^:?-{2,}:?$/.test(cell))
}

export function parseMarkdownBlocks(markdown: string): MarkdownBlock[] {
  const lines = markdown.replace(/\r\n/g, "\n").split("\n")
  const blocks: MarkdownBlock[] = []
  let i = 0

  while (i < lines.length) {
    const line = lines[i]
    const trimmed = line.trim()

    if (!trimmed) {
      i += 1
      continue
    }

    // Fenced code block ```
    if (trimmed.startsWith("```")) {
      const lang = trimmed.slice(3).trim()
      const codeLines: string[] = []
      i += 1
      while (i < lines.length && !lines[i].trim().startsWith("```")) {
        codeLines.push(lines[i])
        i += 1
      }
      if (i < lines.length) i += 1
      blocks.push({ type: "code", lang, content: codeLines.join("\n") })
      continue
    }

    // Horizontal rule
    if (/^(-{3,}|\*{3,}|_{3,})$/.test(trimmed)) {
      blocks.push({ type: "hr" })
      i += 1
      continue
    }

    // Heading #..####
    const headingMatch = trimmed.match(/^(#{1,4})\s+(.+)$/)
    if (headingMatch) {
      const level = headingMatch[1].length as 1 | 2 | 3 | 4
      blocks.push({ type: "heading", level, tokens: tokenizeInline(headingMatch[2]) })
      i += 1
      continue
    }

    // Markdown pipe table
    if (trimmed.includes("|") && i + 1 < lines.length && isTableSeparator(lines[i + 1])) {
      const headerCells = splitTableRow(trimmed).map((cell) => tokenizeInline(cell))
      i += 2
      const rows: InlineToken[][][] = []
      while (i < lines.length && lines[i].trim().includes("|") && lines[i].trim() !== "") {
        rows.push(splitTableRow(lines[i]).map((cell) => tokenizeInline(cell)))
        i += 1
      }
      blocks.push({ type: "table", headers: headerCells, rows })
      continue
    }

    // Blockquote
    if (trimmed.startsWith(">")) {
      const quoteLines: string[] = []
      while (i < lines.length && lines[i].trim().startsWith(">")) {
        quoteLines.push(lines[i].trim().replace(/^>\s?/, ""))
        i += 1
      }
      blocks.push({ type: "blockquote", blocks: parseMarkdownBlocks(quoteLines.join("\n")) })
      continue
    }

    // Unordered list
    if (/^[-*]\s+/.test(trimmed)) {
      const items: InlineToken[][] = []
      while (i < lines.length && /^\s*[-*]\s+/.test(lines[i])) {
        const text = lines[i].replace(/^\s*[-*]\s+/, "")
        items.push(tokenizeInline(text))
        i += 1
      }
      blocks.push({ type: "list", ordered: false, items })
      continue
    }

    // Ordered list
    if (/^\d+\.\s+/.test(trimmed)) {
      const items: InlineToken[][] = []
      while (i < lines.length && /^\s*\d+\.\s+/.test(lines[i])) {
        const text = lines[i].replace(/^\s*\d+\.\s+/, "")
        items.push(tokenizeInline(text))
        i += 1
      }
      blocks.push({ type: "list", ordered: true, items })
      continue
    }

    // Paragraph
    const paraLines: string[] = [trimmed]
    i += 1
    while (i < lines.length) {
      const next = lines[i].trim()
      if (
        !next ||
        next.startsWith("```") ||
        /^#{1,4}\s+/.test(next) ||
        next.startsWith(">") ||
        /^[-*]\s+/.test(next) ||
        /^\d+\.\s+/.test(next) ||
        /^(-{3,}|\*{3,}|_{3,})$/.test(next) ||
        (next.includes("|") && i + 1 < lines.length && isTableSeparator(lines[i + 1]))
      ) {
        break
      }
      paraLines.push(next)
      i += 1
    }
    blocks.push({ type: "paragraph", tokens: tokenizeInline(paraLines.join(" ")) })
  }

  return blocks
}
