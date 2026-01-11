# HTMX Navigation Paradigm

We use HTMX for SPA-like navigation. The app shell stays static; only `#main-content` is swapped.

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│  app_shell.html (static frame)                              │
│  ┌──────────────┐  ┌─────────────────────────────────────┐  │
│  │ Sidebar      │  │ #main-content (hx-history-elt)      │  │
│  │ (static)     │  │                                     │  │
│  │              │  │  ← htmx swaps content here          │  │
│  └──────────────┘  └─────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

## Key Concepts

1. **App Shell Pattern**: `app_shell.html` is the outer frame with sidebar and header. Content loads into `#main-content` via htmx.

2. **History Element**: `#main-content` has `hx-history-elt` attribute, required for browser back/forward to work in shell-based SPAs.

3. **Client-Side Nav Highlighting**: Sidebar active states update via JS in `_sidebar_nav.html` (listens to `htmx:pushedIntoHistory` and `popstate`). Server renders initial state via `is_active` flags; JS handles subsequent navigations.

## Adding a New Top-Level Page

### Step 1: Add to navigation_items in `views.py`

```python
# In get_app_shell_context()
navigation_items = [
    {"name": "Dashboard", "url": "/dashboard/", "icon": "dashboard", "is_active": current_page == "dashboard"},
    # ... existing items ...
    {"name": "NewPage", "url": "/newpage/", "icon": "newpage", "is_active": current_page == "newpage"},
]
```

### Step 2: Create the view in `views.py`

```python
@login_required
def newpage(request):
    if request.htmx:
        context = get_app_shell_context(current_page="newpage")
        return render(request, "devopshero_app/newpage.html", context=context)

    context = get_app_shell_context(current_page="newpage")
    context["content_url"] = "/newpage/"
    return render(request, "devopshero_app/app_shell.html", context=context)
```

### Step 3: Create the template `templates/devopshero_app/newpage.html`

```html
<div class="max-w-2xl">
    <h1 class="text-2xl font-bold text-gray-900 dark:text-white mb-4">New Page</h1>
    <p class="text-gray-600 dark:text-gray-400">Content here.</p>
</div>
```

Page templates contain just content — no boilerplate needed.

### Step 4: Add the icon `templates/devopshero_app/partials/icons/newpage.html`

Create an SVG icon file for the sidebar.

### Step 5: Add URL route in `urls.py`

```python
path("newpage/", views.newpage, name="newpage"),
```

## Adding Nested Pages (like Settings subsections)

For pages with their own sub-navigation (tabs):

### Step 1: Create base template with tab nav (e.g., `settings.html`)

```html
<div class="max-w-4xl">
    <h1 class="text-2xl font-bold mb-6">Settings</h1>

    <!-- Tab Navigation -->
    <div class="flex h-12 border-b border-gray-200 dark:border-white/10">
        <div class="flex space-x-8">
            <a href="/settings/organization/"
               hx-get="/settings/organization/"
               hx-target="#main-content"
               hx-swap="innerHTML"
               hx-push-url="true"
               class="inline-flex items-center border-b-2 px-1 pt-1 text-sm font-medium
                      {% if active_tab == 'organization' %}border-indigo-600 text-gray-900{% else %}border-transparent text-gray-500 hover:border-gray-300{% endif %}">
                Organization
            </a>
            <!-- More tabs... -->
        </div>
    </div>

    <!-- Content block for subsections -->
    <div class="mt-6">
        {% block settings_content %}{% endblock %}
    </div>
</div>
```

### Step 2: Create subsection templates that extend the base

```html
{% extends "devopshero_app/settings.html" %}

{% block settings_content %}
<div>
    <h2 class="text-lg font-semibold">Organization Settings</h2>
    <!-- Subsection content -->
</div>
{% endblock %}
```

### Step 3: Create views for each subsection

```python
@login_required
def settings_organization(request):
    context = get_app_shell_context(current_page="settings")
    context["active_tab"] = "organization"

    if request.htmx:
        return render(request, "devopshero_app/settings/organization.html", context=context)

    context["content_url"] = "/settings/organization/"
    return render(request, "devopshero_app/app_shell.html", context=context)
```

**Key points:**
- `current_page="settings"` keeps the sidebar Settings item active
- `active_tab="organization"` controls which tab is highlighted
- Subsection templates extend the parent, so the whole settings section (nav + content) is returned
- `hx-target="#main-content"` means tab clicks replace the entire settings section, re-rendering the tab nav with correct active states


## Why This Works

1. **Minimal payload**: Only page content is sent; sidebar stays static.
2. **Browser history works**: `hx-push-url="true"` + `hx-history-elt` enables proper back/forward.
3. **Direct URL access works**: Views handle both htmx requests (return partial) and full page loads (return app_shell with content_url).
4. **DRY**: Subsection templates extend their parent, so tab navigation is defined once.
