import { apiPost, ApiError } from './api'
import { supabase } from './supabase'

/**
 * Change the signed-in user's own password.
 *
 * The API does it (POST /auth/password, R1 security 24-Sep-2026) so that it can clear the
 * server-owned `must_reset` flag with certainty — while that flag is set every other API route
 * answers 403, so the old client-side `supabase.auth.updateUser` alone would leave the member
 * locked out. An API that does not have the route yet (plain 404) still gets the direct change.
 * Resolves to an error message to show, or null on success.
 */
export async function changeOwnPassword(password: string): Promise<string | null> {
  try {
    await apiPost('/auth/password', { password })
  } catch (e) {
    if (e instanceof ApiError && e.status === 404 && !e.body.includes('Account')) {
      const { error } = await supabase.auth.updateUser({ password, data: { must_reset: false } })
      return error ? error.message : null
    }
    if (e instanceof ApiError) {
      if (e.status === 400) return 'Password must be 8 to 128 characters.'
      if (e.status === 429) return 'Too many attempts — wait a minute and try again.'
      return `Could not change the password (${e.status}).`
    }
    return 'Could not reach the server.'
  }
  // The session's user_metadata copy (read by the temporary-password banner) refreshes with the
  // token; the server flag is already cleared, so a failed refresh changes nothing that matters.
  try {
    await supabase.auth.refreshSession()
  } catch {
    /* ignore */
  }
  return null
}
