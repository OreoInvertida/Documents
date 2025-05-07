# main.py
import os
import datetime
from fastapi import FastAPI, UploadFile, File, HTTPException, Path, Header, Request, Depends
from fastapi.responses import StreamingResponse
from typing import List, Optional
from pymongo import MongoClient, DESCENDING
from pymongo.errors import DuplicateKeyError
from google.cloud import storage
from bson.objectid import ObjectId
from services.token_service import verify_token

app = FastAPI()

# Configuration
BUCKET_NAME = os.getenv("BUCKET_NAME")
MONGO_URI = os.getenv("MONGO_URI")

if not BUCKET_NAME or not MONGO_URI:
    raise RuntimeError("BUCKET_NAME and MONGO_URI must be set")

# Initialize clients
storage_client = storage.Client()
mongo_client = MongoClient(MONGO_URI)
db = mongo_client["documents_db"]
collection = db["files"]

# Utilities
def now_utc():
    return datetime.datetime.utcnow()

def get_blob(path: str):
    bucket = storage_client.bucket(BUCKET_NAME)
    return bucket.blob(path)

# Routes
@app.put("/{path:path}")
async def upload_or_replace_document(
    path: str,
    file: UploadFile = File(...),
    token_data: dict = Depends(verify_token)
):


    print(token_data)
    user_id = token_data["sub"]


    blob = get_blob(path)
    blob.upload_from_file(file.file, content_type=file.content_type)

    metadata = {
        "user_id": user_id,
        "path": path,
        "filename": file.filename,
        "content_type": file.content_type,
        "created_at": now_utc(),
        "last_modified": now_utc(),
        "signed": False,
    }

    collection.update_one(
        {"path": path},
        {"$set": metadata},
        upsert=True
    )
    return {"message": "Document uploaded", "path": path}

@app.patch("/{path:path}")
async def update_document(
    path: str,
    file: UploadFile = File(...),
    token_data: dict = Depends(verify_token)
):
    user_id = token_data["sub"]

    doc = collection.find_one({"path": path})
    if not doc:
        raise HTTPException(status_code=404, detail="Document does not exist")

    if doc["user_id"] != user_id:
        raise HTTPException(status_code=403, detail="You do not have permission to update this document")

    blob = get_blob(path)
    blob.upload_from_file(file.file, content_type=file.content_type)

    collection.update_one(
        {"path": path},
        {"$set": {
            "last_modified": now_utc(),
            "size": file.spool_max_size,
            "content_type": file.content_type,
        }}
    )
    return {"message": "Document updated", "path": path}

@app.get("/{path:path}")
async def download_document(path: str, token_data: dict = Depends(verify_token)):
    user_id = token_data["sub"]

    doc = collection.find_one({"path": path})
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    if doc["user_id"] != user_id:
        raise HTTPException(status_code=403, detail="You do not have permission to access this document")

    blob = get_blob(path)
    if not blob.exists():
        raise HTTPException(status_code=404, detail="File not found")
    stream = blob.download_as_bytes()
    return StreamingResponse(iter([stream]), media_type=blob.content_type)

@app.get("/metadata")
async def list_metadata_all(limit: int = 10, offset: int = 0, token_data: dict = Depends(verify_token)):
    if token_data.get("type") != "gov_official":
        raise HTTPException(status_code=403, detail="Forbidden: Only government officials can access this endpoint")

    total = collection.count_documents({})
    items = list(
        collection.find()
        .sort("created_at", DESCENDING)
        .skip(offset)
        .limit(limit)
    )
    for item in items:
        item["id"] = str(item["_id"])
        del item["_id"]
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": items
    }

@app.get("/metadata/{user_id}")
async def list_metadata_user(user_id: str, limit: int = 10, offset: int = 0, token_data: dict = Depends(verify_token)):
    if token_data.get("type") != "gov_official":
        raise HTTPException(status_code=403, detail="Forbidden: Only government officials can access this endpoint")

    total = collection.count_documents({"user_id": user_id})
    items = list(
        collection.find({"user_id": user_id})
        .sort("created_at", DESCENDING)
        .skip(offset)
        .limit(limit)
    )
    for item in items:
        item["id"] = str(item["_id"])
        del item["_id"]
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": items
    }

@app.patch("/metadata/{document_id}/sign")
async def sign_document(document_id: str, token_data: dict = Depends(verify_token)):


    result = collection.update_one(
        {"_id": ObjectId(document_id)},
        {"$set": {"signed": True, "last_modified": now_utc()}}
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Document not found")
    return {"message": "Document marked as signed"}
