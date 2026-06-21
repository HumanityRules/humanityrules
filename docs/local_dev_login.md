# Local Dev Login

The app uses WorkOS AuthKit for authentication, which requires an external OAuth flow. For local browser testing, use the **dev login endpoint** (available only when `DEBUG=True`):

```
http://127.0.0.1:8000/auth/dev-login/
```

This auto-logs in as the first superuser and redirects to `/dashboard/`. Use the `next` query param to land on a specific page:

```
http://127.0.0.1:8000/auth/dev-login/?next=/deploy/new/default/<repo-id>/
```

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
