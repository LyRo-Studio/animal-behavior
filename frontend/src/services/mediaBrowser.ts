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
