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
`x integration not connected in HUMR — connect it from the Integrations pane.`
Surface it verbatim and stop — the user connects the X card themselves; you
can't do it for them.

Posts, replies, deletes, and DMs act publicly on the user's real account —
confirm intent before writing.

## Cost — read calls bill per object returned

X bills per **object returned**, not per request, so a single read can cost
far more than it looks. Two separately-metered objects bill: **posts**
(~$0.005 each) and **users** (~$0.01 each) — the billed count is the response's
`data[]` plus anything pulled into `includes` (`includes.tweets[]`,
`includes.users[]`). Billing dedups per object per 24h, so refetching the
*same* post/user is free after the first time.

What this means in practice:

- A single-post lookup (`/2/tweets/:id`) bills 1 post; the cheap path when the
  user gives a post URL/id.
- `/2/users/me` and a handful of handle lookups (`/2/users/by/username/…`,
  `/2/users?ids=…`) are cheap — one distinct user each, and `/2/users/me`
  dedups across the session. Use `?user.fields=public_metrics` for
  follower/following *counts* (one user object) instead of listing the accounts.
- Follower/following/list-member pages (`/2/users/:id/followers`, `/following`)
  return up to ~100 **distinct** users — ~$1 a page. Treat them like post
  sweeps: bound hard, never enumerate a large graph to find or count accounts.
- `tweet.fields`/`user.fields`/`media.fields` only annotate objects already in
  the response and add nothing — request `public_metrics` freely. Media, list,
  and DM-event objects are not known to bill as posts or users.

Keep reads minimal:

- Set the smallest `max_results` that answers the question, and always set it
  explicitly (omitting it defaults to a large page). Floors differ by endpoint:
  the user-timeline accepts 5–100, but `search/recent` rejects anything below 10.
- Need a referenced/parent/quoted post? Look it up separately with
  `/2/tweets/:id` (bills 1) rather than `expansions=referenced_tweets.id`, which
  pulls — and bills — the referenced post of *every* item in the page (e.g.
  `max_results=5` with that expansion billed 7, not 5).
- Never silently paginate or sweep "everything." Each page is ~100 more billed
  posts, and a `conversation_id` search bills every reply it returns. When the
  user asks for "all" / a full history / a whole thread, estimate the cost
  (≈ pages × 100; narrow a conversation search with `from:<author>` to pay only
  for that author's posts) and confirm before fetching.

The injected token is OAuth 2.0 **user context**, so endpoints that require
app-only auth are unreachable (403 "Unsupported Authentication") — notably
full-archive `search/all`. Use `search/recent` (last ~7 days); for older posts,
tell the user it's out of reach rather than retrying.
