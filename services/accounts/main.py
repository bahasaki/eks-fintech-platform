from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional
import uuid
import datetime

app = FastAPI(title="Accounts Service")

# In-memory storage (заменим на RDS позже)
accounts_db = {}

class AccountCreate(BaseModel):
    owner_name: str
    email: str
    account_type: str = "checking"

class Account(BaseModel):
    account_id: str
    owner_name: str
    email: str
    account_type: str
    balance: float
    created_at: str

@app.get("/health")
def health():
    return {"status": "healthy", "service": "accounts"}

@app.post("/accounts", response_model=Account)
def create_account(data: AccountCreate):
    account_id = str(uuid.uuid4())
    account = {
        "account_id": account_id,
        "owner_name": data.owner_name,
        "email": data.email,
        "account_type": data.account_type,
        "balance": 0.0,
        "created_at": datetime.datetime.utcnow().isoformat()
    }
    accounts_db[account_id] = account
    return account

@app.get("/accounts/{account_id}", response_model=Account)
def get_account(account_id: str):
    if account_id not in accounts_db:
        raise HTTPException(status_code=404, detail="Account not found")
    return accounts_db[account_id]

@app.get("/accounts")
def list_accounts():
    return {"accounts": list(accounts_db.values()), "total": len(accounts_db)}