# Emergent Auth (Google OAuth) — Testing Playbook for CCMS

This file captures the Emergent-managed Google sign-in playbook so the
testing agent (and future fork agents) have a single source of truth.

## Where Google sign-in plugs into our auth model

We already have JWT cookie auth (admin/doctor/staff/patient). Google
OAuth is an **alternative** sign-in method for staff (admin/doctor/staff)
only. Patients stay on phone-first SMS OTP — Google must NOT auto-create
`role=patient` rows.

## Backend endpoint

`POST /api/auth/google/exchange {session_id}`
  - Calls Emergent's `https://demobackend.emergentagent.com/auth/v1/env/oauth/session-data`
    with the `X-Session-ID` header
  - Receives `{id, email, name, picture, session_token}`
  - Looks up an existing CCMS user by lowercased email
  - If not found, checks the tenant's `google_oauth_allowed_domains`
    setting (admin-editable). If the domain is allowlisted, auto-creates
    a `role=staff` user. Otherwise returns 403.
  - Mints our existing JWT access + refresh cookies
  - Writes an `audit_logs` row with `action=auth.google.signin`

## Frontend flow

1. `/login` page exposes a **Sign in with Google** button.
2. Clicking the button does:
   ```js
   // REMINDER: DO NOT HARDCODE THE URL, OR ADD ANY FALLBACKS OR REDIRECT URLS
   const redirect = window.location.origin + "/auth/google/callback";
   window.location.href =
     `https://auth.emergentagent.com/?redirect=${encodeURIComponent(redirect)}`;
   ```
3. After Google login Emergent redirects to `/auth/google/callback#session_id=…`.
4. `<GoogleAuthCallback>` reads the fragment **synchronously during
   render**, POSTs `{session_id}` to `/api/auth/google/exchange`, then
   navigates to `/` on success.

## Test credentials & accounts

This is OAuth — there are no app-managed passwords. To test E2E in a
preview environment, register a Google email under one of the
allowlisted domains. The default tenant ships with `ccms.app` allowed,
so any `*@ccms.app` Google account will succeed.

For backend-only testing (no real Google login required), seed an
`oauth_emergent_session` row keyed to a deterministic `session_id` and
call the exchange endpoint directly. See
`/app/backend/tests/test_identity_google.py` for a worked example.
