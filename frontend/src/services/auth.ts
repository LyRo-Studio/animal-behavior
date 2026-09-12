import { API_BASE_URL } from '@/services/apiBase'

export interface AuthTokens {
  accessToken: string
  refreshToken: string
}

export interface CurrentAccount {
  id: number
  email: string
  displayName: string
  role: 'admin' | 'user'
}

const GENERIC_LOGIN_ERROR = 'Invalid email or password'

export async function login(email: string, password: string): Promise<AuthTokens> {
  const response = await fetch(`${API_BASE_URL}/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password }),
  })

  if (!response.ok) {
    // The backend already returns one generic failure for every reason
    // (wrong password, unknown email, deactivated account) — mirror that
    // here rather than surfacing its response body verbatim.
    throw new Error(GENERIC_LOGIN_ERROR)
  }

  const data = await response.json()
  return { accessToken: data.access_token, refreshToken: data.refresh_token }
}

export async function refreshTokens(refreshToken: string): Promise<AuthTokens> {
  const response = await fetch(`${API_BASE_URL}/auth/refresh`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ refresh_token: refreshToken }),
  })

  if (!response.ok) {
    throw new Error('Session expired')
  }

  const data = await response.json()
  return { accessToken: data.access_token, refreshToken: data.refresh_token }
}

export async function logout(refreshToken: string): Promise<void> {
  await fetch(`${API_BASE_URL}/auth/logout`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ refresh_token: refreshToken }),
  })
}

/**
 * Request a password-reset email. Always resolves — the backend gives the
 * same generic response whether or not the email matches an active
 * account (never reveal which), so there's nothing meaningful to reject on.
 */
export async function requestPasswordReset(email: string): Promise<void> {
  await fetch(`${API_BASE_URL}/auth/forgot-password`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email }),
  })
}

export async function fetchCurrentAccount(accessToken: string): Promise<CurrentAccount> {
  const response = await fetch(`${API_BASE_URL}/accounts/me`, {
    headers: { Authorization: `Bearer ${accessToken}` },
  })

  if (!response.ok) {
    throw new Error('Not authenticated')
  }

  const data = await response.json()
  return { id: data.id, email: data.email, displayName: data.display_name, role: data.role }
}
