# Phoenix App

A minimal Elixir/Phoenix application for testing the DevOps Hero deployment agent.

## Requirements

- Elixir 1.16+
- Phoenix 1.7+
- PostgreSQL

## Local Setup

1. Install dependencies:
   ```bash
   mix deps.get
   ```

2. Create and migrate the database:
   ```bash
   mix ecto.create
   mix ecto.migrate
   ```

3. Start the server:
   ```bash
   mix phx.server
   ```

The app runs on port **4000** by default.

## Environment Variables

Copy `.env.example` to `.env` and configure:

- `DATABASE_URL` — PostgreSQL connection string (e.g., `ecto://user:pass@localhost/phoenix_app_dev`)
- `SECRET_KEY_BASE` — Phoenix secret key (generate with `mix phx.gen.secret`)
- `PHX_HOST` — Hostname for production (e.g., `example.com`)
- `PORT` — Server port (defaults to 4000)

## Endpoints

- `GET /health` — Health check endpoint
- `GET /posts` — List all posts
- `GET /posts/:id` — Show a post
- `POST /posts` — Create a post (JSON: `{"post": {"title": "...", "body": "..."}}`)
- `PUT /posts/:id` — Update a post
- `DELETE /posts/:id` — Delete a post

## Testing

```bash
mix test
```
