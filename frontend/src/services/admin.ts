import { API_BASE_URL, authHeaders, errorFromResponse } from '@/services/apiBase'

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

// Thrown by createAccount when the email belongs to an existing but
// deactivated Account (backend returns a structured `detail` for this one
// case, not a plain string) — carries the account id so the UI can offer
// reactivating it instead of just reporting a dead-end failure.
export class DeactivatedAccountExistsError extends Error {
  existingAccountId: number

  constructor(message: string, existingAccountId: number) {
    super(message)
    this.name = 'DeactivatedAccountExistsError'
    this.existingAccountId = existingAccountId
  }
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
    const body = await response.json().catch(() => null)
    // The backend distinguishes "email already active elsewhere" (plain
    // string detail) from "email belongs to a deactivated account"
    // (structured detail, since the UI needs the account id back too).
    if (body?.detail && typeof body.detail === 'object' && 'existing_account_id' in body.detail) {
      throw new DeactivatedAccountExistsError(
        body.detail.message ?? 'Failed to create account.',
        body.detail.existing_account_id,
      )
    }
    throw new Error(typeof body?.detail === 'string' ? body.detail : 'Failed to create account.')
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

export async function reactivateAccount(accessToken: string, id: number): Promise<AdminAccount> {
  const response = await fetch(`${API_BASE_URL}/admin/accounts/${id}/reactivate`, {
    method: 'POST',
    headers: authHeaders(accessToken),
  })

  if (!response.ok) {
    throw await errorFromResponse(response, 'Failed to reactivate account.')
  }

  return toAdminAccount(await response.json())
}
