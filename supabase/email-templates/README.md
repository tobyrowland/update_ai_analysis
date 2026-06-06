# Supabase Auth email templates

Source of truth for the HTML used by Supabase Auth's transactional emails.

Supabase stores these templates **only in the project dashboard** — they
are not picked up from this directory automatically. These files exist so
the templates are version-controlled, reviewable, and recoverable. When a
template here changes, paste the new HTML into the dashboard by hand.

## Delivery — Resend via Custom SMTP

Auth emails are delivered by **Resend** (not Supabase's built-in sender),
wired as a Custom SMTP relay. Supabase still renders these templates and
mints the token links; Resend only delivers. No app code is involved — it
is dashboard + DNS config:

1. **Resend → Domains** — add `alphamolt.ai` and publish the SPF/DKIM DNS
   records it gives you. Wait for "Verified".
2. **Resend → API Keys** — create a key (`re_…`) with send permission.
3. **Supabase dashboard → Authentication → Emails → SMTP Settings** →
   enable **Custom SMTP**:
   - **Sender email**: `login@alphamolt.ai` (must be on the verified domain)
   - **Sender name**: `AlphaMolt`
   - **Host**: `smtp.resend.com`
   - **Port**: `465`
   - **Username**: `resend`
   - **Password**: the Resend API key from step 2
4. Raise **Authentication → Rate Limits → Emails** above the tiny default
   that applies while using the built-in sender.

Send a magic link to a fresh address to confirm it arrives from
`login@alphamolt.ai` via Resend (check the Resend dashboard's Emails log).

## Deploy templates

Supabase dashboard → **Authentication → Email Templates** → select the
template → replace the body with the file's contents → **Save**.

| File | Dashboard template | Supabase variables used |
|---|---|---|
| `magic-link.html` | Magic Link | `{{ .SiteURL }}`, `{{ .TokenHash }}` |
| `confirm-signup.html` | Confirm signup | `{{ .SiteURL }}`, `{{ .TokenHash }}` |

The app's sole auth path is `signInWithOtp` (`web/components/login-form.tsx`),
but it fires **two** templates depending on the address:

- **Returning** address → **Magic Link**.
- **First-time** address → **Confirm signup** (Supabase sends this, not Magic
  Link, when `signInWithOtp` creates the user). Leaving it on the Supabase
  default is why first-time sign-ins got an unbranded, dead link — so both
  templates must be deployed.

Both link straight back to the app's own `/auth/callback` with a
`token_hash` (`?token_hash={{ .TokenHash }}&type=magiclink|signup`) rather
than `{{ .ConfirmationURL }}`. The callback route
(`web/app/auth/callback/route.ts`) verifies the hash via `verifyOtp`, so the
session is established without depending on Supabase's legacy
`/auth/v1/verify` redirect. The callback still accepts a PKCE `?code=` link
for backward compatibility.

**Dashboard URL config (required):** Authentication → URL Configuration →
set **Site URL** to `https://www.alphamolt.ai` (substituted into
`{{ .SiteURL }}`) and add `https://www.alphamolt.ai/auth/callback` to the
**Redirect URLs** allow-list.

## Notes

- All CSS is inline and the layout is table-based — email clients strip
  `<style>` blocks and don't support modern CSS layout.
- The design is dark to match the site (`web/app/opengraph-image.tsx`
  palette: `#0A0A0A` background, `#EDEDED` text, `#00FF41` accent).
- `{{ .ConfirmationURL }}` is substituted by Supabase with the one-time
  sign-in link; it appears both as the CTA button and as a copy-pasteable
  fallback link.
