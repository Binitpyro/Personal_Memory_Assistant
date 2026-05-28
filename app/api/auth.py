import asyncio
import json
import logging
import os
from pathlib import Path

from pydantic import BaseModel
import keyring
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow  # type: ignore

from app.config import settings

logger = logging.getLogger(__name__)

auth_router = APIRouter(prefix="/auth/google", tags=["auth"])

# In Tauri mode the port is dynamic (set via PORT env var by the shell).
# In web-dev mode it defaults to 8000.
def _get_oauth_base() -> str:
    port = os.environ.get("PORT", "8000")
    return f"http://localhost:{port}"


# OAuth configuration
SCOPES = [
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/generative-language.retriever",
]

# We store the final token here
TOKEN_PATH = Path("data/credentials.json")


def _ensure_data_dir():
    """L-21: Defer directory creation to first usage rather than import time."""
    TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)


# The client secret must be provided by the developer building the app
# Can be a file or built from environment variables.
CLIENT_SECRETS_FILE = Path("secrets/client_secret.json")


def _oauth_status_page(title: str, message: str, *, success: bool) -> HTMLResponse:
    accent = "#16a34a" if success else "#dc2626"
    symbol = "Success" if success else "Problem"
    html = f"""<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>{title}</title>
    <style>
      :root {{
        color-scheme: light dark;
        --bg: #0f172a;
        --card: #111827;
        --text: #e5e7eb;
        --muted: #9ca3af;
        --accent: {accent};
      }}
      body {{
        margin: 0;
        font-family: Arial, sans-serif;
        background: radial-gradient(circle at top, #1f2937, var(--bg));
        color: var(--text);
        min-height: 100vh;
        display: grid;
        place-items: center;
        padding: 24px;
      }}
      main {{
        max-width: 520px;
        width: 100%;
        background: color-mix(in srgb, var(--card) 88%, black);
        border: 1px solid color-mix(in srgb, var(--accent) 40%, transparent);
        border-radius: 18px;
        padding: 28px;
        box-shadow: 0 18px 50px rgba(0, 0, 0, 0.35);
      }}
      h1 {{
        margin: 0 0 12px;
        font-size: 28px;
      }}
      .badge {{
        display: inline-block;
        margin-bottom: 14px;
        padding: 6px 10px;
        border-radius: 999px;
        background: color-mix(in srgb, var(--accent) 18%, transparent);
        color: var(--accent);
        font-size: 12px;
        font-weight: 700;
        letter-spacing: 0.08em;
        text-transform: uppercase;
      }}
      p {{
        margin: 0 0 18px;
        line-height: 1.6;
      }}
      .muted {{
        color: var(--muted);
        font-size: 14px;
      }}
      button {{
        background: var(--accent);
        color: white;
        border: 0;
        border-radius: 10px;
        padding: 12px 16px;
        font-weight: 700;
        cursor: pointer;
      }}
    </style>
  </head>
  <body>
    <main>
      <div class="badge">{symbol}</div>
      <h1>{title}</h1>
      <p>{message}</p>
      <p class="muted">Return to Personal Memory Assistant.
         The app refreshes your Google connection status when it regains focus.</p>
      <button onclick="window.close()">Close This Tab</button>
    </main>
  </body>
</html>"""
    return HTMLResponse(content=html)


async def get_client_config():
    """Load OAuth client config from file or env vars without blocking the event loop."""

    def _read():
        if CLIENT_SECRETS_FILE.exists():
            with open(CLIENT_SECRETS_FILE) as f:
                return json.load(f)
        return None

    config = await asyncio.to_thread(_read)
    if config:
        return config

    # Fallback to env vars if running locally without a file
    client_id = os.environ.get("GOOGLE_CLIENT_ID")
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET")
    if not client_id or not client_secret:
        return None
    return {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "project_id": "pma-local-auth",
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
            "redirect_uris": [f"{_get_oauth_base()}/api/auth/google/callback"],
        }
    }


@auth_router.get("/start")
async def start_oauth(request: Request):
    """Starts the Google OAuth flow."""
    client_config = await get_client_config()
    if not client_config:
        return _oauth_status_page(
            "Google Sign-In Unavailable",
            "OAuth client credentials are not configured for this build. "
            "Add GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET, or provide "
            "secrets/client_secret.json, then try again.",
            success=False,
        )

    # Build redirect URI from the dynamic port assigned by Tauri shell
    redirect_uri = f"{_get_oauth_base()}/api/auth/google/callback"

    flow = Flow.from_client_config(client_config, scopes=SCOPES, redirect_uri=redirect_uri)

    authorization_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",  # Force re-consent to ensure we get a refresh token
    )

    return RedirectResponse(url=authorization_url)


@auth_router.get("/callback")
async def oauth_callback(request: Request):
    """Handles the OAuth redirect from Google."""
    code = request.query_params.get("code")
    if not code:
        return _oauth_status_page(
            "Google Sign-In Failed",
            "The callback did not include an authorization code. "
            "Please restart Google sign-in from PMA and try again.",
            success=False,
        )

    client_config = await get_client_config()
    if not client_config:
        return _oauth_status_page(
            "Google Sign-In Unavailable",
            "OAuth client credentials are not configured for this build.",
            success=False,
        )

    redirect_uri = f"{_get_oauth_base()}/api/auth/google/callback"
    flow = Flow.from_client_config(client_config, scopes=SCOPES, redirect_uri=redirect_uri)

    try:
        # fetch_token makes a blocking HTTP request — offload it.
        await asyncio.to_thread(flow.fetch_token, code=code)
        credentials = flow.credentials

        # Save credentials securely
        token_data = {
            "token": credentials.token,
            "refresh_token": credentials.refresh_token,
            "token_uri": credentials.token_uri,
            "client_id": credentials.client_id,
            "client_secret": credentials.client_secret,
            "scopes": credentials.scopes,
        }

        def save_token():
            with open(TOKEN_PATH, "w") as f:
                json.dump(token_data, f)

        await asyncio.to_thread(save_token)

        logger.info("Successfully connected Google Account.")
        return _oauth_status_page(
            "Google Account Connected",
            "Your Google account is now connected to PMA. "
            "You can close this tab and return to the app.",
            success=True,
        )

    except Exception as e:
        logger.error("OAuth callback failed: %s", str(e))
        return _oauth_status_page(
            "Google Sign-In Failed",
            f"Authentication failed: {e!s}",
            success=False,
        )


@auth_router.get("/status")
async def auth_status():
    """Checks for a Gemini key in settings OR a valid Google token."""
    # Priority 1: Check .env for PMA_GEMINI_API_KEY
    if settings.gemini_api_key:
        return {"connected": True, "method": "env"}

    # Priority 2: Check for OAuth token
    if not TOKEN_PATH.exists():
        return {"connected": False}

    try:

        def load_token():
            with open(TOKEN_PATH) as f:
                return json.load(f)

        token_data = await asyncio.to_thread(load_token)

        creds = Credentials.from_authorized_user_info(token_data)
        if creds.valid:
            return {"connected": True, "method": "oauth"}
        if creds.expired and creds.refresh_token:
            # creds.refresh makes a blocking HTTP request — offload it.
            await asyncio.to_thread(creds.refresh, GoogleRequest())
            # Save refreshed token
            token_data["token"] = creds.token

            def save_token():
                with open(TOKEN_PATH, "w") as f:
                    json.dump(token_data, f)

            await asyncio.to_thread(save_token)
            return {"connected": True, "method": "oauth"}

        return {"connected": False}
    except Exception as e:
        logger.error("Failed to check auth status: %s", str(e))
        return {"connected": False}


@auth_router.post("/disconnect")
async def disconnect_auth():
    """Removes the stored OAuth token."""
    if TOKEN_PATH.exists():
        try:
            await asyncio.to_thread(os.remove, TOKEN_PATH)
            return {"message": "Disconnected successfully."}
        except Exception as e:
            return JSONResponse(status_code=500, content={"error": f"Failed to disconnect: {e!s}"})
    return {"message": "Already disconnected."}

class KeyRequest(BaseModel):
    provider: str
    api_key: str

@auth_router.post("/keys")
async def set_api_key(req: KeyRequest):
    """Sets an API key in the OS keyring."""
    try:
        await asyncio.to_thread(keyring.set_password, "pma_backend", req.provider, req.api_key)
        return {"status": "success"}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

@auth_router.get("/keys/{provider}")
async def get_api_key(provider: str):
    """Gets an API key status/preview from the OS keyring."""
    try:
        key = await asyncio.to_thread(keyring.get_password, "pma_backend", provider)
        if key:
            preview = key[:4] + "..." + key[-4:] if len(key) > 8 else "****"
            return {"provider": provider, "preview": preview, "is_set": True}
        return {"provider": provider, "is_set": False}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

@auth_router.delete("/keys/{provider}")
async def delete_api_key(provider: str):
    """Deletes an API key from the OS keyring."""
    try:
        await asyncio.to_thread(keyring.delete_password, "pma_backend", provider)
        return {"status": "success"}
    except Exception as e:
        # deletion might fail if not found
        return JSONResponse(status_code=500, content={"error": str(e)})
