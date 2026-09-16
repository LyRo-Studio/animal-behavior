import { API_BASE_URL } from '@/services/apiBase'

export interface Cut {
  key: string
  filename: string
  camera: string | null
  condition: string | null
  phase: string | null
  size: number
  lastModified: string
}

interface CutResponse {
  key: string
  filename: string
  camera: string | null
  condition: string | null
  phase: string | null
  size: number
  last_modified: string
}

function toCut(row: CutResponse): Cut {
  return {
    key: row.key,
    filename: row.filename,
    camera: row.camera,
    condition: row.condition,
    phase: row.phase,
    size: row.size,
    lastModified: row.last_modified,
  }
}

function authHeaders(accessToken: string): HeadersInit {
  return { Authorization: `Bearer ${accessToken}` }
}

export async function listTestIds(accessToken: string): Promise<string[]> {
  const response = await fetch(`${API_BASE_URL}/media/tests`, {
    headers: authHeaders(accessToken),
  })

  if (!response.ok) {
    throw new Error('Failed to load Tests.')
  }

  return response.json()
}

// Thrown by listCuts specifically for "no such Test" (backend 404) — kept
// distinct from a generic failure so the view can show a "not found" state
// rather than a load-error banner.
export class TestNotFoundError extends Error {}

export async function listCuts(accessToken: string, testId: string): Promise<Cut[]> {
  const response = await fetch(`${API_BASE_URL}/media/tests/${encodeURIComponent(testId)}/cuts`, {
    headers: authHeaders(accessToken),
  })

  if (response.status === 404) {
    throw new TestNotFoundError(`Test "${testId}" not found.`)
  }
  if (!response.ok) {
    throw new Error('Failed to load Cuts.')
  }

  const rows: CutResponse[] = await response.json()
  return rows.map(toCut)
}

export interface DatasetEntry {
  name: string
  key: string
  isFolder: boolean
  size: number | null
  lastModified: string | null
}

interface DatasetEntryResponse {
  name: string
  key: string
  is_folder: boolean
  size: number | null
  last_modified: string | null
}

function toDatasetEntry(row: DatasetEntryResponse): DatasetEntry {
  return {
    name: row.name,
    key: row.key,
    isFolder: row.is_folder,
    size: row.size,
    lastModified: row.last_modified,
  }
}

// Thrown by listDatasetFolder specifically for "no such folder" (backend
// 404) — kept distinct from a generic failure, same reasoning as
// TestNotFoundError above.
export class DatasetFolderNotFoundError extends Error {}

export async function listDatasetFolder(
  accessToken: string,
  path: string = '',
): Promise<DatasetEntry[]> {
  const url = new URL(`${API_BASE_URL}/media/datasets`)
  if (path) url.searchParams.set('path', path)

  const response = await fetch(url, { headers: authHeaders(accessToken) })

  if (response.status === 404) {
    throw new DatasetFolderNotFoundError(`Dataset folder "${path}" not found.`)
  }
  if (!response.ok) {
    throw new Error('Failed to load Dataset folder.')
  }

  const rows: DatasetEntryResponse[] = await response.json()
  return rows.map(toDatasetEntry)
}

// Ticket #21: play/download a Cut via a short-lived, single-Cut-scoped,
// single-action-scoped media token — see docs/adr/0002-media-access-tokens-
// in-url.md. The native <video>/download request that follows can't carry
// an Authorization header, so the token (not the session) authenticates it.
export type MediaTokenAction = 'play' | 'download'

interface MediaTokenResponse {
  token: string
  expires_in: number
}

export async function requestMediaToken(
  accessToken: string,
  key: string,
  action: MediaTokenAction,
): Promise<string> {
  const response = await fetch(`${API_BASE_URL}/media/cuts/token`, {
    method: 'POST',
    headers: { ...authHeaders(accessToken), 'Content-Type': 'application/json' },
    body: JSON.stringify({ key, action }),
  })

  if (response.status === 429) {
    throw new Error('Too many requests. Please try again shortly.')
  }
  if (!response.ok) {
    throw new Error(`Failed to start ${action === 'play' ? 'playback' : 'download'}.`)
  }

  const body: MediaTokenResponse = await response.json()
  return body.token
}

// The URL the browser's own <video src> / download link hits directly —
// never fetched via `fetch`, so the media token travels as a query
// parameter rather than a header (the deliberate, scoped exception in the
// ADR referenced above).
export function mediaStreamUrl(key: string, action: MediaTokenAction, token: string): string {
  const url = new URL(`${API_BASE_URL}/media/stream`)
  url.searchParams.set('key', key)
  url.searchParams.set('action', action)
  url.searchParams.set('token', token)
  return url.toString()
}

// Ticket #22: a Cut's probed media info (duration, resolution, codec),
// fetched lazily (like requestMediaToken above, only on request — not
// eagerly for a whole Test's Cuts) and cached server-side keyed by S3 key
// + ETag, so a repeated request for the same, unchanged Cut is cheap.
export interface CutMediaInfo {
  durationSeconds: number
  width: number
  height: number
  codec: string
}

interface CutMediaInfoResponse {
  duration_seconds: number
  width: number
  height: number
  codec: string
}

function toCutMediaInfo(row: CutMediaInfoResponse): CutMediaInfo {
  return {
    durationSeconds: row.duration_seconds,
    width: row.width,
    height: row.height,
    codec: row.codec,
  }
}

// Thrown by getCutMediaInfo specifically for "no such Cut" (backend 404) —
// kept distinct from a generic failure, same reasoning as TestNotFoundError
// above.
export class CutInfoNotFoundError extends Error {}

export async function getCutMediaInfo(accessToken: string, key: string): Promise<CutMediaInfo> {
  const url = new URL(`${API_BASE_URL}/media/cuts/info`)
  url.searchParams.set('key', key)

  const response = await fetch(url, { headers: authHeaders(accessToken) })

  if (response.status === 404) {
    throw new CutInfoNotFoundError('Cut not found.')
  }
  if (response.status === 429) {
    throw new Error('Too many requests. Please try again shortly.')
  }
  if (!response.ok) {
    throw new Error('Failed to load media info.')
  }

  return toCutMediaInfo(await response.json())
}
