/**
 * frontend/src/__tests__/utils.test.ts
 * Unit tests for pure utility functions in the frontend.
 * These are language-level tests with no DOM or network dependency.
 */
import { describe, it, expect } from 'vitest'

// ── File size formatter ────────────────────────────────────────────────────

function formatBytes(bytes: number): string {
  if (bytes === 0) return '0 B'
  const k = 1024
  const sizes = ['B', 'KB', 'MB', 'GB', 'TB']
  const i = Math.floor(Math.log(bytes) / Math.log(k))
  return `${parseFloat((bytes / Math.pow(k, i)).toFixed(2))} ${sizes[i]}`
}

describe('formatBytes', () => {
  it('returns 0 B for zero bytes', () => {
    expect(formatBytes(0)).toBe('0 B')
  })
  it('formats bytes correctly', () => {
    expect(formatBytes(512)).toBe('512 B')
  })
  it('formats kilobytes', () => {
    expect(formatBytes(1024)).toBe('1 KB')
  })
  it('formats megabytes', () => {
    expect(formatBytes(1024 * 1024)).toBe('1 MB')
  })
  it('formats gigabytes', () => {
    expect(formatBytes(1024 ** 3)).toBe('1 GB')
  })
})

// ── Duration formatter ────────────────────────────────────────────────────

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms.toFixed(0)}ms`
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`
  return `${(ms / 60000).toFixed(1)}m`
}

describe('formatDuration', () => {
  it('formats milliseconds', () => {
    expect(formatDuration(500)).toBe('500ms')
  })
  it('formats seconds', () => {
    expect(formatDuration(2500)).toBe('2.5s')
  })
  it('formats minutes', () => {
    expect(formatDuration(90000)).toBe('1.5m')
  })
})

// ── File extension extractor ───────────────────────────────────────────────

function getExtension(filename: string): string {
  const parts = filename.split('.')
  if (parts.length < 2) return ''
  return `.${parts[parts.length - 1].toLowerCase()}`
}

describe('getExtension', () => {
  it('extracts extension from filename', () => {
    expect(getExtension('report.pdf')).toBe('.pdf')
  })
  it('handles no extension', () => {
    expect(getExtension('Makefile')).toBe('')
  })
  it('handles multiple dots', () => {
    expect(getExtension('archive.tar.gz')).toBe('.gz')
  })
  it('lowercases extension', () => {
    expect(getExtension('Image.PNG')).toBe('.png')
  })
})

// ── Relative path shortener ───────────────────────────────────────────────

function shortenPath(fullPath: string, maxLength = 60): string {
  if (fullPath.length <= maxLength) return fullPath
  const parts = fullPath.split(/[/\\]/)
  if (parts.length <= 2) return '...' + fullPath.slice(-(maxLength - 3))
  return parts[0] + '/.../' + parts[parts.length - 1]
}

describe('shortenPath', () => {
  it('returns unchanged path when short enough', () => {
    expect(shortenPath('C:/Users/alice/doc.pdf')).toBe('C:/Users/alice/doc.pdf')
  })
  it('shortens long paths', () => {
    const long = 'C:/Users/alice/documents/projects/my_project/deep/nested/file.txt'
    const short = shortenPath(long, 40)
    expect(short.length).toBeLessThanOrEqual(60)
  })
})

// ── Query sanitizer ──────────────────────────────────────────────────────

function sanitizeQuery(query: string): string {
  return query.trim().replace(/\s+/g, ' ')
}

describe('sanitizeQuery', () => {
  it('trims whitespace', () => {
    expect(sanitizeQuery('  hello world  ')).toBe('hello world')
  })
  it('collapses multiple spaces', () => {
    expect(sanitizeQuery('hello   world')).toBe('hello world')
  })
  it('handles empty string', () => {
    expect(sanitizeQuery('')).toBe('')
  })
  it('handles tabs and newlines', () => {
    expect(sanitizeQuery('hello\t\nworld')).toBe('hello world')
  })
})
