# Django PostgreSQL App

A minimal Django application with PostgreSQL integration. Designed as a test case for DevOps Hero deployment agent.

## Features

- Django 5.x with Python 3.12
- PostgreSQL database via `dj-database-url`
- Built-in Django authentication
- Simple Note model with CRUD via admin
- Health check endpoint at `/health`
- Django admin interface

## Local Setup

### Prerequisites

- Python 3.12+
- PostgreSQL running locally (or accessible via DATABASE_URL)

### Installation

1. Create and activate a virtual environment:

```bash
python3.12 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Configure environment variables:

```bash
cp .env.example .env
# Edit .env with your database credentials and secret key
```

4. Run database migrations:

```bash
python manage.py migrate
```

5. Create a superuser for admin access:

```bash
python manage.py createsuperuser
```

6. Start the development server:

```bash
python manage.py runserver 8000
```

The app will be available at http://localhost:8000

## Endpoints

- `/` - App info (JSON)
- `/health` - Health check endpoint (JSON)
- `/admin/` - Django admin interface

## Environment Variables

- **SECRET_KEY** - Django secret key (required in production)
- **DEBUG** - Enable debug mode (default: False)
- **ALLOWED_HOSTS** - Comma-separated list of allowed hosts
- **DATABASE_URL** - PostgreSQL connection URL (format: `postgres://USER:PASSWORD@HOST:PORT/DBNAME`)

## Production

For production deployment, use gunicorn:

```bash
gunicorn config.wsgi:application --bind 0.0.0.0:8000
```
