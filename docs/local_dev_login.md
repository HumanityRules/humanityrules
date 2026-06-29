# Local Dev Login

The app uses WorkOS AuthKit for authentication, which requires an external OAuth flow. For local browser testing, use the **dev login endpoint** (available only when `DEBUG=True`):

```
http://127.0.0.1:8000/auth/dev-login/
```

With no query string this renders a **picker page**: a list of existing users (each a one-click login) plus a box to create a new test user. You can also drive it directly with an `email` query param:

- **Existing user** — `/auth/dev-login/?email=alice@test.com` logs straight in.
- **New user** — any email not in the DB simulates the WorkOS new-user callback and drops you into the real `/onboarding/` flow (org-name form → creates Organization + User + ABAC). Use throwaway addresses (`alice@test.com`, `bob@test.com`, …) to re-run onboarding freely.

Add `next` to land on a specific page after login (the picker forwards it too):

```
http://127.0.0.1:8000/auth/dev-login/?email=alice@test.com&next=/deploy/new/default/<repo-id>/
```

An invite link as `next` (`&next=/invite/<token>/`) routes a new user through invite-based onboarding instead of creating their own org.

For `curl` testing, create a session directly and use it as a cookie:

```bash
uv run manage.py shell -c "
from django.contrib.sessions.backends.db import SessionStore
from humanityrules_app.models import User
u = User.objects.get(email='vmendi@gmail.com')
s = SessionStore()
s['_auth_user_id'] = str(u.pk)
s['_auth_user_backend'] = 'django.contrib.auth.backends.ModelBackend'
s['_auth_user_hash'] = u.get_session_auth_hash()
s.create()
print(s.session_key)
"
```

Then pass the printed session key: `curl -b "sessionid=<key>" http://127.0.0.1:8000/...`
