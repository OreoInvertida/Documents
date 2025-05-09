# main.py
import os
import datetime
from fastapi import FastAPI, UploadFile, File, HTTPException, Path, Header, Request, Depends, Body
from fastapi.responses import StreamingResponse
from typing import List, Optional
from pymongo import MongoClient, DESCENDING
from pymongo.errors import DuplicateKeyError
from google.cloud import storage
from bson.objectid import ObjectId
from services.token_service import verify_token
from utils.logger import logger
import httpx


app = FastAPI()

# Configuration
BUCKET_NAME = os.getenv("BUCKET_NAME")
MONGO_URI = os.getenv("MONGO_URI")
USERS_SERVICE_URL = os.getenv("USERS_SERVICE_URL")
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


async def get_user_type(user_id: str, token :str) -> str:
    logger.info(f"token {token}")
    async with httpx.AsyncClient() as client:
        response = await client.get(f"{USERS_SERVICE_URL}/get/{user_id}", headers={"Authorization" : f"{token}"},timeout=30)
        if response.status_code != 200:
            raise HTTPException(status_code=503, detail="User service not available")
        data = response.json()
        return data.get("type", None)

# Routes
@app.put("/doc/{path:path}")
async def upload_or_replace_document(
    path: str,
    file: UploadFile = File(...),
    token_data: dict = Depends(verify_token)
):


    print(token_data)
    user_id = token_data["sub"]

    clean_path = path.lstrip("/").removeprefix("doc/")
    logger.info("cleaned path: %s", clean_path)


    blob = get_blob(path)
    blob.upload_from_file(file.file, content_type=file.content_type)

    metadata = {
        "user_id": user_id,
        "path": clean_path,
        "filename": file.filename,
        "content_type": file.content_type,
        "created_at": now_utc(),
        "last_modified": now_utc(),
        "signed": False,
    }

    collection.update_one(
        {"path": clean_path},
        {"$set": metadata},
        upsert=True
    )
    return {"message": "Document uploaded", "path": clean_path}

@app.patch("/doc/{path:path}")
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

@app.get("/doc/{path:path}")
async def download_document(path: str, token_data: dict = Depends(verify_token)):
    user_id = token_data["sub"]
    logger.info("path %s",path)

    clean_path = path.lstrip("/").removeprefix("doc/")
    logger.info("cleaned path: %s", clean_path)
    
    doc = collection.find_one({"path": clean_path})
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    if doc["user_id"] != user_id:
        raise HTTPException(status_code=403, detail="You do not have permission to access this document")

    blob = get_blob(clean_path)
    if not blob.exists():
        raise HTTPException(status_code=404, detail="File not found")
    stream = blob.download_as_bytes()
    return StreamingResponse(iter([stream]), media_type=blob.content_type)

# ─── Endpoint: List ALL metadata (gov only) ────────────────────────────────────
@app.get("/metadata")
async def list_metadata_all(
    request : Request,
    limit: int = 10,
    offset: int = 0,
    token_data: dict = Depends(verify_token)
):
    requester_id = token_data.get("sub")
    token = request.headers.get("authorization", "")
    if not requester_id:
        raise HTTPException(status_code=401, detail="Invalid token")


    user_type = await get_user_type(requester_id,token=token)
    if user_type != "gov_official":
        raise HTTPException(status_code=403, detail="Forbidden: Government access only")

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

# ─── Endpoint: List metadata by user_id (gov only) ─────────────────────────────
@app.get("/metadata/{user_id}")
async def list_metadata_user(
    user_id: str,
    limit: int = 10,
    offset: int = 0,
    token_data: dict = Depends(verify_token)
):
    requester_id = token_data.get("sub")
    if not requester_id:
        raise HTTPException(status_code=401, detail="Invalid token")

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

@app.delete("/doc/{path:path}")
async def delete_document(request : Request,path: str, token_data: dict = Depends(verify_token)):
    user_id = token_data["sub"]
    token = request.headers.get("authorization", "")
    user_type = get_user_type(user_id, token=token)
    clean_path = path.lstrip("/").removeprefix("doc/")
    logger.info("Deleting document: %s", clean_path)

    # Check metadata
    doc = collection.find_one({"path": clean_path})
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    if doc["user_id"] != user_id and user_type != "gov_official":
        raise HTTPException(status_code=403, detail="You do not have permission to delete this document")

    # Delete from GCS
    blob = get_blob(clean_path)
    if blob.exists():
        blob.delete()
    else:
        logger.warning(f"Blob not found in GCS: {clean_path}")

    # Delete from DB
    collection.delete_one({"path": clean_path})

    return {"message": "Document deleted", "path": clean_path}



    from fastapi import Body

@app.post("/signed-urls")
async def get_signed_urls(
    request: Request,
    document_paths: List[str] = Body(..., embed=True),
    token_data: dict = Depends(verify_token)
):
    requester_id = token_data.get("sub")
    token = request.headers.get("authorization", "")

    user_type = await get_user_type(requester_id, token)

    # 🔐 Validate ownership first (only for non-gov users)
    if user_type != "gov_official":
        for path in document_paths:
            if not path.startswith(f"{requester_id}/"):
                raise HTTPException(
                    status_code=403,
                    detail=f"Access denied for file '{path}': you can only request signed URLs for your own documents."
                )

    signed_urls = {}
    for path in document_paths:
        logger.info(f"Generating signed URL for path: {path}")

        blob = get_blob(path)
        if not blob.exists():
            raise HTTPException(status_code=404, detail=f"File not found: {path}")

        url = blob.generate_signed_url(
            version="v4",
            expiration=datetime.timedelta(minutes=30),
            method="GET"
        )
        signed_urls[path] = url

    return {"signed_urls": signed_urls}
