from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
import httpx
import os
from prometheus_fastapi_instrumentator import Instrumentator

app = FastAPI(title="API Gateway")
Instrumentator().instrument(app).expose(app)

ACCOUNTS_URL = os.getenv("ACCOUNTS_SERVICE_URL", "http://accounts.accounts.svc.cluster.local")
TRANSACTIONS_URL = os.getenv("TRANSACTIONS_SERVICE_URL", "http://transactions.transactions.svc.cluster.local")

@app.get("/health")
def health():
    return {"status": "healthy", "service": "api-gateway"}

@app.api_route("/accounts", methods=["GET", "POST"])
@app.api_route("/accounts/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def proxy_accounts(request: Request, path: str = ""):
    url = f"{ACCOUNTS_URL}/accounts"
    if path:
        url = f"{url}/{path}"
    async with httpx.AsyncClient() as client:
        try:
            response = await client.request(
                method=request.method,
                url=url,
                headers={"Content-Type": "application/json"},
                content=await request.body(),
                follow_redirects=True
            )
            return JSONResponse(content=response.json(), status_code=response.status_code)
        except httpx.ConnectError:
            raise HTTPException(status_code=503, detail="Accounts service unavailable")

@app.api_route("/transactions", methods=["GET", "POST"])
@app.api_route("/transactions/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def proxy_transactions(request: Request, path: str = ""):
    url = f"{TRANSACTIONS_URL}/transactions"
    if path:
        url = f"{url}/{path}"
    async with httpx.AsyncClient() as client:
        try:
            response = await client.request(
                method=request.method,
                url=url,
                headers={"Content-Type": "application/json"},
                content=await request.body(),
                follow_redirects=True
            )
            return JSONResponse(content=response.json(), status_code=response.status_code)
        except httpx.ConnectError:
            raise HTTPException(status_code=503, detail="Transactions service unavailable")