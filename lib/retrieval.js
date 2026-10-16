// In-memory hybrid retrieval (keyword + vector, fused with RRF k=60), standing
// in for the doc's Couchbase FTS + vector search. Product vectors come from the
// gateway's embedding model and are computed once per process.
import { readFile } from 'node:fs/promises'
import { performance } from 'node:perf_hooks'
import { cosine } from './gateway.js'

const products = JSON.parse(await readFile(new URL('../data/products.json', import.meta.url), 'utf8'))
let productVectors = null
let vectorsKey = ''

const docText = p => `${p.title}. ${p.description}`
const tokens = s => s.toLowerCase().match(/[a-z0-9]+/g) ?? []

// key identifies the embedding source (gateway + model); a change re-embeds.
export async function ensureProductVectors(gw, key = '') {
  if (key !== vectorsKey) productVectors = null
  if (productVectors) return { ms: 0, cached: true }
  vectorsKey = key
  const t0 = performance.now()
  const vectors = []
  for (const p of products) vectors.push((await gw.embed(docText(p))).vector)
  productVectors = vectors
  return { ms: Math.round(performance.now() - t0), cached: false }
}

function keywordRank(query) {
  const q = new Set(tokens(query))
  return products
    .map((p, i) => ({ i, score: tokens(docText(p)).filter(t => q.has(t)).length }))
    .filter(r => r.score > 0)
    .sort((a, b) => b.score - a.score)
    .map(r => r.i)
}

function vectorRank(queryVector) {
  return productVectors
    .map((v, i) => ({ i, score: cosine(queryVector, v) }))
    .sort((a, b) => b.score - a.score)
    .map(r => r.i)
}

export function hybridSearch(query, queryVector, topK = 3, k = 60) {
  const t0 = performance.now()
  const fused = new Map()
  for (const list of [keywordRank(query).slice(0, 5), vectorRank(queryVector).slice(0, 5)]) {
    list.forEach((idx, rank) => fused.set(idx, (fused.get(idx) ?? 0) + 1 / (k + rank + 1)))
  }
  const top = [...fused.entries()]
    .sort((a, b) => b[1] - a[1])
    .slice(0, topK)
    .map(([idx]) => products[idx])
  return { top, ms: performance.now() - t0 }
}

export function contextBlock(items) {
  return items
    .map(p => `- ${p.product_id} | ${p.title} | $${p.price} | ${p.description}`)
    .join('\n')
}

export { products }
