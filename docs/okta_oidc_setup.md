# Okta OIDC Setup Guide

How to onboard a customer who uses Okta for SSO, so they can log in to DevOps Hero without going through WorkOS ($125/customer saved).

## Background

We have two auth providers:
- **WorkOS** (`/auth/login/`) — the default for most customers
- **OIDC** (`/oidc/login/?org=<slug>`) — for customers with Okta

OIDC customers get their own login URL. When they visit it, they're redirected to Okta, authenticate there, and come back to `/oidc/callback/` where we create their user account automatically.

## What you need from the customer

Ask the customer's Okta admin for:
- Their **Okta domain** (e.g. `acme.okta.com` or `dev-12345678.okta.com`)

You'll give them back:
- A **sign-in redirect URI** to configure in their Okta app (https://devopshero.ai/oidc/callback/）

## Step-by-step

### 1. Customer creates an app integration in Okta

Tell the customer (or their Okta admin) to do this:

1. Go to the Okta admin console > **Applications** > **Create App Integration**
2. Choose **OIDC - OpenID Connect** and **Web Application**, click Next
3. Set the **Sign-in redirect URI** to:
   ```
   https://devopshero.ai/oidc/callback/
   ```
4. Under **Assignments**, assign the users/groups who should have access
5. Save the app — Okta will show a **Client ID** and **Client Secret**

Ask them to send you the Client ID and Client Secret (securely).

### 2. Customer configures an access policy

This is the step most likely to cause issues. Without it, Okta will reject login attempts with "Policy evaluation failed".

Tell the customer:

1. Go to **Security** > **API** > **Authorization Servers** > click **default**
2. Go to the **Access Policies** tab
3. Either the default policy already covers their app, or they need to create one
4. The policy needs at least one **rule** — if there are no rules, it blocks everything
5. Add a rule (e.g. "Allow all") with default settings — this grants the `openid`, `email`, `profile` scopes we need

### 3. Get the issuer URL

The issuer URL is on the same page: **Security** > **API** > **Authorization Servers** > **default**. It looks like:

```
https://acme.okta.com/oauth2/default
```

The customer can send this along with the Client ID and Client Secret.

### 4. Create the organization in DevOps Hero

Run the setup command (works on both local and prod):

```bash
uv run manage.py setup_oidc_org
```

It will prompt for:
- **Slug**: a URL-friendly identifier (e.g. `acme`) — this goes in the login URL
- **Name**: display name (e.g. "Acme Corp")
- **Issuer URL**: from step 3
- **Client ID**: from step 1
- **Client Secret**: from step 1
- **Bootstrap admin email**: the email of the customer's first admin user — when this user logs in for the first time, the org gets fully bootstrapped (seed ABAC policies + admin role)

Or pass everything as flags:

```bash
uv run manage.py setup_oidc_org \
  --slug acme \
  --name "Acme Corp" \
  --issuer-url "https://acme.okta.com/oauth2/default" \
  --client-id "0oa..." \
  --client-secret "..." \
  --bootstrap-admin-email "admin@acme.com"
```

### 5. Give the customer their login URL

```
https://devopshero.ai/oidc/login/?org=acme
```

This is the only URL they need. The bootstrap admin's first login will fully initialize the org (seed ABAC policies + admin role). Subsequent users are auto-created with the default role (viewer).

**If you (the operator) already have a DOH account via WorkOS** and want that same account to be the bootstrap admin, you must visit `/oidc/login/?org=<slug>` once after running `setup_oidc_org`. That first visit identity-links your existing row into the new org: it matches by `(org, email)`, back-fills `oidc_sub` on your user, runs the org bootstrap, and clears `bootstrap_admin_email`. If you skip this step, the Okta `sub` never gets written to your user, and any app-level Okta login (e.g. through a personal-assistant policy proxy) will fail with "you do not have access to this application" because the PDP can't find your user by sub.

## Troubleshooting

| Error | Cause | Fix |
|-------|-------|-----|
| "User is not assigned to the client application" | The Okta user isn't assigned to the app | Customer needs to go to the app's **Assignments** tab and add the user/group |
| "Policy evaluation failed" | The authorization server has no access policy rule | Customer needs to add a rule to the access policy (see step 2) |
| "Organization not found" | Wrong slug in the URL, or org doesn't exist | Check the slug matches what you used in `setup_oidc_org` |
| "OIDC authentication failed" | Bad client secret, or issuer URL is wrong | Re-check the three values from the customer |

## How it works (for the curious)

```
Browser                    DevOps Hero                 Okta
  |                            |                         |
  |  GET /oidc/login/?org=acme |                         |
  |--------------------------->|                         |
  |                            |  (store state+slug      |
  |                            |   in session)           |
  |  302 to Okta /authorize    |                         |
  |<---------------------------|                         |
  |                            |                         |
  |  User logs in at Okta      |                         |
  |----------------------------------------------->      |
  |                            |                         |
  |  302 to /oidc/callback/?code=...&state=...           |
  |<-----------------------------------------------|     |
  |--------------------------->|                         |
  |                            |  POST /v1/token         |
  |                            |------------------------>|
  |                            |  { access_token }       |
  |                            |<------------------------|
  |                            |  GET /v1/userinfo       |
  |                            |------------------------>|
  |                            |  { sub, email, name }   |
  |                            |<------------------------|
  |                            |                         |
  |                            |  find/create user       |
  |                            |  log in                 |
  |  302 to /dashboard/        |                         |
  |<---------------------------|                         |
```
