# OAuth Login Flow

## Overview

JobHuntAI uses Chainlit's built-in OAuth support. Users authenticate via **Google** or **GitHub** — no username/password is ever created or stored. On first login a user row is inserted into SQLite; on subsequent logins the row is refreshed with the latest profile data from the provider. A Chainlit session is created and the thread sidebar is populated from the user's previous chat history.

---

## Step-by-Step Flow

### 1. User lands on the login page

**File:** `.chainlit/config.toml`

Chainlit reads `OAUTH_GOOGLE_CLIENT_ID` and `OAUTH_GITHUB_CLIENT_ID` from the environment and automatically renders the provider buttons. No custom route or template is needed.

The right panel displays `public/login_bg.svg` (the JobHuntAI brand panel), configured via:

```toml
[UI]
login_page_image = "/public/login_bg.svg"
login_page_image_filter = "brightness-100"
login_page_image_dark_filter = "brightness-100"
```

The login heading is overridden in `.chainlit/translations/en-US.json`:

```json
"login": {
  "title": "Login to JobHuntAI"
}
```

---

### 2. User clicks a provider button

Chainlit redirects the browser to the provider's OAuth authorisation URL:

| Provider | Authorisation URL |
|---|---|
| Google | `https://accounts.google.com/o/oauth2/auth?client_id=...&redirect_uri=...&scope=openid email profile` |
| GitHub | `https://github.com/login/oauth/authorize?client_id=...&redirect_uri=...&scope=user:email` |

The `redirect_uri` sent is:

```
http://localhost:8000/auth/oauth/google/callback
http://localhost:8000/auth/oauth/github/callback
```

These must match exactly what is registered in the Google Cloud Console and GitHub OAuth App settings respectively.

---

### 3. Provider redirects back with an auth code

After the user approves the consent screen, the provider redirects to the callback URL with a short-lived `code` parameter:

```
GET http://localhost:8000/auth/oauth/google/callback?code=4/P7q7W91...&state=...
```

Chainlit's internal OAuth handler intercepts this route, exchanges the `code` for an access token by calling the provider's token endpoint, then fetches the user's profile.

---

### 4. `@cl.oauth_callback` runs

**File:** `chainlit_app.py` — lines 32–48

```python
@cl.oauth_callback
def oauth_callback(provider_id, token, raw_user_data, default_user):
    email   = raw_user_data.get("email", "")
    name    = raw_user_data.get("name") or raw_user_data.get("login", "")
    picture = raw_user_data.get("picture") or raw_user_data.get("avatar_url", "")
    if not email:
        return None   # reject — no email means no identity
    user = sqlite.get_or_create_oauth_user(email, name, provider=provider_id, picture_url=picture)
    return cl.User(identifier=user["user_id"], metadata={"email": email, "name": name, "picture": picture})
```

**Raw profile data by provider:**

| Field | Google key | GitHub key |
|---|---|---|
| Email | `email` | `email` |
| Display name | `name` | `login` |
| Avatar | `picture` | `avatar_url` |

**File:** `db/sqlite.py` — `get_or_create_oauth_user()`

```python
def get_or_create_oauth_user(email, name, provider=None, picture_url=None):
    user = get_user_by_email(email)
    if user:
        # returning user — refresh display name, picture, provider
        UPDATE users SET display_name=?, picture_url=?, oauth_provider=? WHERE email=?
        return get_user_by_email(email)
    # new user — derive username from email prefix, ensure uniqueness
    return create_user(username, email, oauth_provider=provider, display_name=name, picture_url=picture_url)
```

**Row written / updated in the `users` table:**

| Column | Example value | Notes |
|---|---|---|
| `user_id` | `51bdc820-8f91-...` | UUID v4, primary key |
| `username` | `purushotham` | Derived from email prefix, de-duped |
| `email` | `user@gmail.com` | Unique, used as lookup key |
| `hashed_password` | `NULL` | Not set for OAuth users |
| `oauth_provider` | `google` / `github` | Provider id string |
| `display_name` | `purushotham v` | From provider profile, refreshed each login |
| `picture_url` | `https://...` | Avatar URL, refreshed each login |
| `created_at` | `2026-06-26 ...` | Set once on first login |

Returning `None` from `oauth_callback` rejects the login (e.g. provider returned no email).

---

### 5. Chainlit creates a session

Chainlit signs a session cookie using `CHAINLIT_AUTH_SECRET` from `.env` and calls `@cl.on_chat_start`.

**File:** `chainlit_app.py` — `on_chat_start()`

```python
@cl.on_chat_start
async def on_chat_start():
    user    = cl.user_session.get("user")      # cl.User from oauth_callback
    user_id = user.identifier                  # our SQLite user_id UUID
    # Chainlit's thread_id becomes our session_id so data layer stays in sync
    cl.user_session.set("session_id", cl.context.session.thread_id)
    # session row created lazily on first message — not here — to avoid empty sessions
```

The session row in SQLite is **not** written here. It is created on the user's first actual message to avoid cluttering the sidebar with empty sessions from every page load.

---

### 6. Welcome message shown

```python
    name   = user.metadata.get("name") or sqlite.get_user_by_id(user_id)["display_name"]
    resume = sqlite.get_current_resume(user_id)

    if not resume:
        "Welcome to JobHuntAI, {name}! … upload your resume …"
    else:
        "Welcome back, {name}! …"
```

New users are prompted to upload a resume. Returning users with an existing resume profile see the standard chat welcome.

---

### 7. Sidebar populates with thread history

**File:** `db/chainlit_data_layer.py` — `list_threads()`

The `@cl.data_layer` is called immediately after login to populate the left sidebar. It queries:

```sql
SELECT * FROM sessions WHERE user_id = ? ORDER BY last_active DESC
```

Each session becomes a thread entry in the sidebar. Sessions are named from the first user message (auto-titled on send).

---

## Credentials & Storage

| What | Where |
|---|---|
| Google client ID / secret | `.env` → `OAUTH_GOOGLE_CLIENT_ID`, `OAUTH_GOOGLE_CLIENT_SECRET` |
| GitHub client ID / secret | `.env` → `OAUTH_GITHUB_CLIENT_ID`, `OAUTH_GITHUB_CLIENT_SECRET` |
| Session signing key | `.env` → `CHAINLIT_AUTH_SECRET` |
| User records | `users` table in `jobhuntai.db` |
| Chat thread history | `sessions` + `messages` tables in `jobhuntai.db` |

---

## Database Tables Touched

| Table | Operation | When |
|---|---|---|
| `users` | `SELECT` | Look up by email on every login |
| `users` | `INSERT` | First login only |
| `users` | `UPDATE` | Every login (refresh name, picture, provider) |
| `sessions` | `SELECT` | Sidebar population after login |
| `sessions` | `INSERT` | First message in a new chat |

---

## Error Cases

| Scenario | Behaviour |
|---|---|
| Provider returns no email | `oauth_callback` returns `None` → login rejected |
| `redirect_uri` mismatch | Provider shows error page — must match Google / GitHub app settings exactly |
| User session expires | `on_message` guard returns "Session expired. Please refresh and log in again." |
| DB write fails | Unhandled exception — Chainlit shows a generic error page |

---

## Sequence Diagram

```
Browser              OAuth Provider          Chainlit              db/sqlite.py
   |                       |                     |                      |
   |-- click provider ---->|                     |                      |
   |                       |-- consent screen -->|                      |
   |<-- redirect to app callback                 |                      |
   |-- GET /auth/oauth/{provider}/callback ----->|                      |
   |                       |<-- exchange code ---|                      |
   |                       |--- access token --->|                      |
   |                       |<-- user profile ----|                      |
   |                       |                     |-- oauth_callback()-->|
   |                       |                     |   get_or_create_     |
   |                       |                     |   oauth_user()       |
   |                       |                     |<-- cl.User ----------|
   |                       |                     |-- sign session cookie|
   |                       |                     |-- on_chat_start()    |
   |                       |                     |-- list_threads() --->|
   |<-- chat UI + sidebar populated              |<-- sessions ---------|
```

---

## Related Files

| File | Role |
|---|---|
| `chainlit_app.py` | `oauth_callback`, `on_chat_start`, `on_message` |
| `db/sqlite.py` | `get_or_create_oauth_user`, `get_user_by_email`, `create_user` |
| `db/chainlit_data_layer.py` | `get_user`, `create_user`, `list_threads` — Chainlit data layer interface |
| `.chainlit/config.toml` | OAuth provider keys, login page image, sidebar state |
| `.chainlit/translations/en-US.json` | Login page heading text override |
| `public/login_bg.svg` | Right-panel brand image on the login page |
| `.env` | All secrets — never committed |
