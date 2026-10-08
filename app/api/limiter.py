from slowapi import Limiter
from slowapi.util import get_remote_address

# Shared global limiter keyed on the socket peer address. Proxy headers must not
# influence it: __main__.py starts uvicorn with proxy_headers=False, so
# request.client.host is the real peer rather than an X-Forwarded-For value.
limiter = Limiter(key_func=get_remote_address)
