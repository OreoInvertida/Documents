# main.py
import os
from fastapi import FastAPI, UploadFile, File, HTTPException
from google.cloud import storage

app = FastAPI()
BUCKET_NAME = os.getenv("BUCKET_NAME") 

if not BUCKET_NAME:
    raise RuntimeError("BUCKET_NAME must be set")

# initialize client once
storage_client = storage.Client()


@app.post("/citizens/{citizen_id}/documents")
async def upload_document(citizen_id: str, file: UploadFile = File(...)):
    """
    Uploads `file` into GCS under folder `<citizen_id>/`.
    """
    try:
        bucket = storage_client.bucket(BUCKET_NAME)
        blob = bucket.blob(f"{citizen_id}/{file.filename}")
        # upload_from_file consumes the underlying SpooledTemporaryFile
        blob.upload_from_file(file.file, content_type=file.content_type)
        # make public if desired:
        # blob.make_public()
        return {
            "bucket": BUCKET_NAME,
            "path": blob.name,
            "url": blob.public_url
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
