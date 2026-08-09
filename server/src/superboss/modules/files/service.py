from datetime import datetime, timezone
from pathlib import PurePosixPath
import re
from uuid import UUID, uuid4
from sqlalchemy import select
from superboss.core.actors import Actor,require_project_access
from superboss.core.errors import ConflictError,NotFoundError
from superboss.modules.files.models import File,FileState,Upload
from superboss.modules.files.schemas import UploadStart
from superboss.modules.files.storage import CompletedPart,ObjectStorage
class FileNotReadyError(ConflictError):
 def __init__(self): super().__init__(); self.code="FILE_NOT_READY"; self.message="File is not available for download"
class FileService:
 def __init__(self,session,storage:ObjectStorage|None,enqueue_scan=None): self.session=session;self.storage=storage;self.enqueue_scan=enqueue_scan or (lambda _file_id: None)
 async def ensure_downloadable(self,file:File)->None:
  if file.state != FileState.CLEAN: raise FileNotReadyError()
 @staticmethod
 def _segment(value:str,fallback:str)->str:
  value=value.replace("\\","/").split("/")[-1]; value="".join(c for c in value if ord(c)>=32); value=re.sub(r"[^\\w.\-\u4e00-\u9fff]","_",value,flags=re.UNICODE).strip("._")
  return value or fallback
 async def start_upload(self,actor:Actor,command:UploadStart,idempotency_key:str):
  require_project_access(actor,command.project_id)
  if not 1<=len(idempotency_key)<=255: raise ValueError("invalid Idempotency-Key")
  existing=await self.session.scalar(select(Upload).where(Upload.project_id==command.project_id,Upload.uploader_id==actor.subject_id,Upload.idempotency_key==idempotency_key))
  if existing:
   old=await self.session.get(File,existing.file_id)
   if old is None or (old.filename,old.category,old.file_date,old.size_bytes,old.sha256)!=(command.filename,command.category,command.file_date,command.size_bytes,command.sha256): raise ConflictError()
   return existing
  file_id=uuid4(); category=self._segment(command.category,"uncategorized"); name=self._segment(command.filename,"file")
  key=f"projects/{command.project_id}/{category}/{command.file_date.isoformat()}/{file_id}/{name}"
  multipart_id=await self.storage.create_multipart(key,command.content_type) # type: ignore[union-attr]
  file=File(id=file_id,project_id=command.project_id,filename=command.filename,category=command.category,file_date=command.file_date,object_key=key,size_bytes=command.size_bytes,sha256=command.sha256,uploader_id=actor.subject_id)
  upload=Upload(file_id=file_id,project_id=command.project_id,uploader_id=actor.subject_id,idempotency_key=idempotency_key,multipart_id=multipart_id)
  self.session.add_all([file,upload]); await self.session.flush(); return upload
 async def presign_download(self,actor:Actor,file_id:UUID)->str:
  file=await self.session.get(File,file_id)
  if file is None: raise NotFoundError()
  require_project_access(actor,file.project_id); await self.ensure_downloadable(file)
  return await self.storage.presign_get(file.object_key,300) # type: ignore[union-attr]
 async def presign_part(self,actor:Actor,upload_id:UUID,part_number:int)->str:
  if not 1<=part_number<=10000: raise ValueError("invalid part number")
  upload=await self.session.get(Upload,upload_id)
  if upload is None: raise NotFoundError()
  require_project_access(actor,upload.project_id); file=await self.session.get(File,upload.file_id)
  if file is None or file.state != FileState.UPLOADING: raise ConflictError()
  return await self.storage.presign_upload_part(file.object_key,upload.multipart_id,part_number,300) # type: ignore[union-attr]
 async def complete_upload(self,actor:Actor,upload_id:UUID,parts:list[CompletedPart])->File:
  upload=await self.session.get(Upload,upload_id)
  if upload is None: raise NotFoundError()
  require_project_access(actor,upload.project_id); file=await self.session.get(File,upload.file_id)
  if file is None or file.state!=FileState.UPLOADING: raise ConflictError()
  if len({p.part_number for p in parts})!=len(parts): raise ValueError("duplicate part number")
  metadata=await self.storage.complete_multipart(file.object_key,upload.multipart_id,sorted(parts,key=lambda p:p.part_number)) # type: ignore[union-attr]
  if metadata.size_bytes!=file.size_bytes:
   await self.storage.abort_multipart(file.object_key,upload.multipart_id) # type: ignore[union-attr]
   file.state=FileState.FAILED; await self.session.flush(); raise ConflictError()
  file.state=FileState.QUARANTINED; await self.session.flush(); result=self.enqueue_scan(file.id)
  if hasattr(result,"__await__"): await result
  return file
