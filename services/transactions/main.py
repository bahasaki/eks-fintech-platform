from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional
import uuid
import datetime
from prometheus_fastapi_instrumentator import Instrumentator

app = FastAPI(title="Transactions Service")
Instrumentator().instrument(app).expose(app)

# In-memory storage (заменим на RDS позже)
transactions_db = {}

class TransactionCreate(BaseModel):
    account_id: str
    amount: float
    transaction_type: str  # deposit, withdrawal, transfer
    description: Optional[str] = None

class Transaction(BaseModel):
    transaction_id: str
    account_id: str
    amount: float
    transaction_type: str
    description: Optional[str]
    status: str
    created_at: str

@app.get("/health")
def health():
    return {"status": "healthy", "service": "transactions"}

@app.post("/transactions", response_model=Transaction)
def create_transaction(data: TransactionCreate):
    if data.amount <= 0:
        raise HTTPException(status_code=400, detail="Amount must be positive")
    transaction_id = str(uuid.uuid4())
    transaction = {
        "transaction_id": transaction_id,
        "account_id": data.account_id,
        "amount": data.amount,
        "transaction_type": data.transaction_type,
        "description": data.description,
        "status": "completed",
        "created_at": datetime.datetime.utcnow().isoformat()
    }
    transactions_db[transaction_id] = transaction
    return transaction

@app.get("/transactions/{transaction_id}", response_model=Transaction)
def get_transaction(transaction_id: str):
    if transaction_id not in transactions_db:
        raise HTTPException(status_code=404, detail="Transaction not found")
    return transactions_db[transaction_id]

@app.get("/transactions/account/{account_id}")
def get_account_transactions(account_id: str):
    account_transactions = [
        t for t in transactions_db.values()
        if t["account_id"] == account_id
    ]
    return {"transactions": account_transactions, "total": len(account_transactions)}