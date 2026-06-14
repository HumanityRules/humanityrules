---
name: x
description: "Post, search, read, and engage on X (Twitter): tweets, replies, timelines, mentions, DMs, likes, reposts, follows."
version: 1.0.0
license: MIT
metadata:
  hermes:
    tags: [X, Twitter, social-media, tweet, DM]
---

# X (Twitter)

Call the X API v2 with `curl`. Use the host `https://api.x.com` — only it gets
the token (below).

The integrations broker injects the connected user's access token on
`api.x.com` requests, so send **no** `Authorization` header:

```bash
curl -s https://api.x.com/2/users/me   # 200 → connected; data.id is the caller id
```

The token carries the full read+write scope set (tweets, users, DMs, likes,
follows, bookmarks, lists, mutes, blocks, media), so no action is scope-gated —
a connected user can do anything the API offers.

If X isn't connected, the broker (not X) replies 503:
`x integration not connected in DOH — connect it from the Integrations pane.`
Surface it verbatim and stop — the user connects the X card themselves; you
can't do it for them.

Posts, replies, deletes, and DMs act publicly on the user's real account —
confirm intent before writing.

## Cost — read calls bill per post returned

X bills the user per **post object returned**, not per request, so a single
read can cost far more than it looks. The billed count is the response's
`data[]` length **plus** any `includes.tweets[]` from expansions. Only post
objects count: user-lookup endpoints (`/2/users/me`, `/2/users/by/username/…`)
return a user object, not posts, and cost nothing — prefer them, e.g.
`?user.fields=public_metrics` for follower/following counts rather than the
followers/following list endpoints. A single-post lookup (`/2/tweets/:id`)
bills exactly 1 and is the cheap path when the user gives a post URL/id.
`tweet.fields`/`user.fields` annotate existing objects and add nothing billed —
request `public_metrics` freely. Keep reads minimal:

- Set the smallest `max_results` that answers the question (the timeline/search
  endpoints reject anything below 5, and default to large pages if you omit it,
  so always set it explicitly).
- Avoid `expansions=referenced_tweets.id` unless the user actually needs the
  referenced posts — it silently adds billed objects (e.g. `max_results=5` with
  that expansion billed 7 posts, not 5).
- Never auto-paginate with `next_token` to "get everything" — each page is
  another ~100 billed posts. Page only when the user explicitly asks for more.
