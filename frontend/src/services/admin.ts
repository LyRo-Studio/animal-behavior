import { API_BASE_URL } from '@/services/apiBase'

export type AdminAccountRole = 'admin' | 'user'

export interface AdminAccount {
  id: number
  email: string
  displayName: string
  role: AdminAccountRole
  isActive: boolean
}

interface AdminAccountResponse {
  id: number
  email: string
  display_name: string
  role: AdminAccountRole
  is_active: boolean
}

function toAdminAccount(row: AdminAccountResponse): AdminAccount {
  return {
    id: row.id,
    email: row.email,
    displayName: row.display_name,
    role: row.role,
    isActive: row.is_active,
  }
}

async function errorFromResponse(response: Response, fallback: string): Promise<Error> {
  // The backend gives a specific reason (bad domain, duplicate email, admin
  // account, unknown id, ...) for these Admin-only endpoints — unlike
  // login, there's no reason to hide it.
  const body = await response.json().catch(() => null)
  return new Error(body?.detail ?? fallback)
}

function authHeaders(accessToken: string): HeadersInit {
  return { Authorization: `Bearer ${accessToken}` }
}

export async function listAccounts(accessToken: string): Promise<AdminAccount[]> {
  const response = await fetch(`${API_BASE_URL}/admin/accounts`, {
    headers: authHeaders(accessToken),
  })

  if (!response.ok) {
    throw new Error('Failed to load accounts.')
  }

  const rows: AdminAccountResponse[] = await response.json()
  return rows.map(toAdminAccount)
}

export async function createAccount(accessToken: string, email: string): Promise<AdminAccount> {
  const response = await fetch(`${API_BASE_URL}/admin/accounts`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders(accessToken) },
    body: JSON.stringify({ email }),
  })

  if (!response.ok) {
    throw await errorFromResponse(response, 'Failed to create account.')
  }

  return toAdminAccount(await response.json())
}

export async function deactivateAccount(accessToken: string, id: number): Promise<AdminAccount> {
  const response = await fetch(`${API_BASE_URL}/admin/accounts/${id}/deactivate`, {
    method: 'POST',
    headers: authHeaders(accessToken),
  })

  if (!response.ok) {
    throw await errorFromResponse(response, 'Failed to deactivate account.')
  }

  return toAdminAccount(await response.json())
}
