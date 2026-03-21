import os
import json
import logging
from pathlib import Path
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import RedirectResponse, JSONResponse
from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request as GoogleRequest
from app.config import settings

logger = logging.getLogger(__name__)

auth_router = APIRouter(prefix="/auth/google", tags=["auth"])

# OAuth configuration
SCOPES = [
    'https://www.googleapis.com/auth/userinfo.email',
    'https://www.googleapis.com/auth/generative-language.retriever',
]

# We store the final token here
TOKEN_PATH = Path("data/credentials.json")
TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)

# The client secret must be provided by the developer building the app
# Can be a file or built from environment variables.
CLIENT_SECRETS_FILE = Path("secrets/client_secret.json")

def get_client_config():
    if CLIENT_SECRETS_FILE.exists():
        with open(CLIENT_SECRETS_FILE, "r") as f:
            return json.load(f)
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
            "redirect_uris": ["http://localhost:8000/api/auth/google/callback"]
        }
    }

@auth_router.get("/start")
async def start_oauth(request: Request):
    """Starts the Google OAuth flow."""
    client_config = get_client_config()
    if not client_config:
        return JSONResponse(status_code=500, content={"error": "OAuth Client ID and Secret not configured."})
        
    # We must use localhost:8000 exactly as registered in the Google Cloud Console
    redirect_uri = "http://localhost:8000/api/auth/google/callback"
    
    flow = Flow.from_client_config(
        client_config,
        scopes=SCOPES,
        redirect_uri=redirect_uri
    )
    
    authorization_url, state = flow.authorization_url(
        access_type='offline',
        include_granted_scopes='true',
        prompt='consent' # Force re-consent to ensure we get a refresh token
    )
    
    # Store state in session or simply pass it along. In a native app, 
    # we can trust localhost redirects enough for a single-user system.
    return {"auth_url": authorization_url}

@auth_router.get("/callback")
async def oauth_callback(request: Request):
    """Handles the OAuth redirect from Google."""
    code = request.query_params.get("code")
    if not code:
        return {"error": "Missing authorization code."}
        
    client_config = get_client_config()
    if not client_config:
        return JSONResponse(status_code=500, content={"error": "OAuth Client ID and Secret not configured."})
        
    redirect_uri = "http://localhost:8000/api/auth/google/callback"
    flow = Flow.from_client_config(
        client_config,
        scopes=SCOPES,
        redirect_uri=redirect_uri
    )
    
    try:
        # Reconstruct the full URL to fetch the token
        # Fetch token parses the code
        flow.fetch_token(code=code)
        credentials = flow.credentials
        
        # Save credentials securely
        token_data = {
            'token': credentials.token,
            'refresh_token': credentials.refresh_token,
            'token_uri': credentials.token_uri,
            'client_id': credentials.client_id,
            'client_secret': credentials.client_secret,
            'scopes': credentials.scopes
        }
        
        with open(TOKEN_PATH, 'w') as f:
            json.dump(token_data, f)
            
        logger.info("Successfully connected Google Account.")
        
        # Redirect the user back to the frontend setup/settings page
        return RedirectResponse(url="http://localhost:5173/settings?auth=success")
        
    except Exception as e:
        logger.error("OAuth callback failed: %s", str(e))
        return JSONResponse(status_code=500, content={"error": f"Authentication failed: {str(e)}"})

@auth_router.get("/status")
async def auth_status():
    """Checks if we have a valid, unexpired Google token."""
    if not TOKEN_PATH.exists():
        return {"connected": False}
        
    try:
        with open(TOKEN_PATH, 'r') as f:
            token_data = json.load(f)
            
        creds = Credentials.from_authorized_user_info(token_data)
        if creds.valid:
            return {"connected": True}
        if creds.expired and creds.refresh_token:
            creds.refresh(GoogleRequest())
            # Save refreshed token
            token_data['token'] = creds.token
            with open(TOKEN_PATH, 'w') as f:
                json.dump(token_data, f)
            return {"connected": True}
            
        return {"connected": False}
    except Exception as e:
        logger.error("Failed to check auth status: %s", str(e))
        return {"connected": False}

@auth_router.post("/disconnect")
async def disconnect_auth():
    """Removes the stored OAuth token."""
    if TOKEN_PATH.exists():
        try:
            os.remove(TOKEN_PATH)
            return {"message": "Disconnected successfully."}
        except Exception as e:
            return JSONResponse(status_code=500, content={"error": f"Failed to disconnect: {str(e)}"})
    return {"message": "Already disconnected."}
