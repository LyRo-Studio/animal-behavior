import { API_BASE_URL } from '@/services/apiBase'

export interface AdminAccount {
  id: number
  email: string
  displayName: string
  isActive: boolean
}

interface AdminAccountResponse {
  id: number
  email: string
  display_name: string
  is_active: boolean
}

function toAdminAccount(row: AdminAccountResponse): AdminAccount {
  return { id: row.id, email: row.email, displayName: row.display_name, isActive: row.is_active }
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
    // The backend gives a specific reason (bad domain, duplicate email) for
    // this Admin-only endpoint — unlike login, there's no reason to hide it.
    const body = await response.json().catch(() => null)
    throw new Error(body?.detail ?? 'Failed to create account.')
  }

  return toAdminAccount(await response.json())
}
