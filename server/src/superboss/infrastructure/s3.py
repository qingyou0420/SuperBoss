"""Async wrapper around the blocking boto3 S3 client."""
import asyncio
from collections.abc import AsyncIterator
import boto3
from superboss.modules.files.storage import CompletedPart,ObjectMetadata
class Boto3ObjectStorage:
 def __init__(self,bucket:str,client=None): self.bucket=bucket;self.client=client or boto3.client("s3")
 async def create_multipart(self,key:str,content_type:str)->str:
  result=await asyncio.to_thread(self.client.create_multipart_upload,Bucket=self.bucket,Key=key,ContentType=content_type); return str(result["UploadId"])
 async def presign_upload_part(self,key:str,multipart_id:str,part_number:int,expires_seconds:int)->str:
  return await asyncio.to_thread(self.client.generate_presigned_url,"upload_part",Params={"Bucket":self.bucket,"Key":key,"UploadId":multipart_id,"PartNumber":part_number},ExpiresIn=expires_seconds)
 async def complete_multipart(self,key:str,multipart_id:str,parts:list[CompletedPart])->ObjectMetadata:
  await asyncio.to_thread(self.client.complete_multipart_upload,Bucket=self.bucket,Key=key,UploadId=multipart_id,MultipartUpload={"Parts":[{"PartNumber":p.part_number,"ETag":p.etag} for p in parts]})
  head=await asyncio.to_thread(self.client.head_object,Bucket=self.bucket,Key=key); return ObjectMetadata(int(head["ContentLength"]),head.get("ETag"))
 async def abort_multipart(self,key:str,multipart_id:str)->None: await asyncio.to_thread(self.client.abort_multipart_upload,Bucket=self.bucket,Key=key,UploadId=multipart_id)
 async def presign_get(self,key:str,expires_seconds:int)->str: return await asyncio.to_thread(self.client.generate_presigned_url,"get_object",Params={"Bucket":self.bucket,"Key":key},ExpiresIn=expires_seconds)
 async def stream(self,key:str)->AsyncIterator[bytes]:
  body=(await asyncio.to_thread(self.client.get_object,Bucket=self.bucket,Key=key))["Body"]
  while chunk:=await asyncio.to_thread(body.read,64*1024): yield chunk
