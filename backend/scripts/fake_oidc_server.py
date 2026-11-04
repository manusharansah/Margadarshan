"""
TEST DOUBLE of an identity provider on :9200 for local development and the browser smoke test.
It shows a tiny account chooser instead of a password page. Never expose it. See gis_backend/devtools/fake_oidc.py.

    python scripts/fake_oidc_server.py
    # then run the API with:
    #   OIDC_ISSUER=http://localhost:9200 OIDC_CLIENT_ID=test-client OIDC_CLIENT_SECRET=test-secret
"""
import html
import sys
from pathlib import Path
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import uvicorn  # noqa: E402
from fastapi import FastAPI, Form, Request  # noqa: E402
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse  # noqa: E402

from gis_backend.devtools.fake_oidc import USERS, FakeProvider  # noqa: E402

provider = FakeProvider()
app = FastAPI()


@app.get("/.well-known/openid-configuration")
def discovery():
    return provider.metadata()


@app.get("/jwks")
def jwks():
    return provider.jwks()


@app.get("/authorize", response_class=HTMLResponse)
def authorize(request: Request):
    q = dict(request.query_params)
    items = "".join(
        f'<li><a href="/pick?{html.escape(urlencode({**q, "email": e}))}">{html.escape(u["name"])} ({html.escape(e)})'
        f'{"" if u["email_verified"] else " - email NOT verified"}</a></li>' for e, u in USERS.items())
    return f"<html><body><h1>Test identity provider</h1><p>Choose an account:</p><ul>{items}</ul></body></html>"


@app.get("/pick")
def pick(request: Request):
    q = dict(request.query_params)
    email = q.pop("email")
    return RedirectResponse(provider.authorize(q, email), status_code=302)


@app.post("/token")
async def token(request: Request):
    form = dict((await request.form()).items())
    status, body = provider.token(form)
    return JSONResponse(body, status_code=status)


if __name__ == "__main__":
    uvicorn.run(app, port=9200, log_level="warning")
