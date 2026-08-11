# Backup and restore before the M1 pilot

Take and verify both PostgreSQL and object-store backups before pilot data is admitted. Commands use
placeholders only. Keep backup media encrypted and access-controlled.

## Quiesce writes and back up

Announce maintenance, stop writers, then create a PostgreSQL custom-format dump and an object mirror.

```powershell
$BackupFile='<ABSOLUTE_POSTGRES_BACKUP_FILE>'
$ObjectBackupDir='<ABSOLUTE_OBJECT_BACKUP_DIRECTORY>'
docker compose --env-file .env -f docker-compose.yml stop nginx web api worker scheduler
docker compose --env-file .env -f docker-compose.yml exec -T postgres `
  sh -ceu 'pg_dump --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --format custom --file /tmp/superboss-m1.backup'
docker compose --env-file .env -f docker-compose.yml cp `
  postgres:/tmp/superboss-m1.backup "$BackupFile"
docker compose --env-file .env -f docker-compose.yml exec -T postgres `
  rm -f /tmp/superboss-m1.backup
docker compose --env-file .env -f docker-compose.yml run --rm --no-deps `
  -v "${ObjectBackupDir}:/backup" `
  --entrypoint /bin/sh minio-init -c `
  'mc alias set local http://minio:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" && mc mirror --overwrite "local/$MINIO_BUCKET" /backup'
Get-FileHash -Algorithm SHA256 -LiteralPath "$BackupFile"
docker compose --env-file .env -f docker-compose.yml start api worker scheduler web nginx
```

Record the checksum, timestamp, migration head, object count, operator, and storage location in the
change record. Do not place the checksum beside a publicly writable backup.

## Restore drill

This overwrites the configured database and bucket. Perform it only in the approved isolated restore
environment after checking the resolved backup paths and obtaining the change approval.

```powershell
Test-Path -LiteralPath "$BackupFile" -PathType Leaf
Test-Path -LiteralPath "$ObjectBackupDir" -PathType Container
docker compose --env-file .env -f docker-compose.yml stop nginx web api worker scheduler
docker compose --env-file .env -f docker-compose.yml cp `
  "$BackupFile" postgres:/tmp/superboss-m1.backup
docker compose --env-file .env -f docker-compose.yml exec -T postgres `
  sh -ceu 'pg_restore --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --clean --if-exists --exit-on-error /tmp/superboss-m1.backup'
docker compose --env-file .env -f docker-compose.yml exec -T postgres `
  rm -f /tmp/superboss-m1.backup
docker compose --env-file .env -f docker-compose.yml run --rm --no-deps `
  -v "${ObjectBackupDir}:/backup:ro" `
  --entrypoint /bin/sh minio-init -c `
  'mc alias set local http://minio:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" && mc mirror --overwrite /backup "local/$MINIO_BUCKET"'
docker compose --env-file .env -f docker-compose.yml start api worker scheduler web nginx
docker compose --env-file .env -f docker-compose.yml exec -T api alembic upgrade head
docker compose --env-file .env -f docker-compose.yml exec -T nginx `
  wget -q -O /dev/null http://api:8000/api/v1/health/ready
```

Compare record counts and object counts with the backup record, run the clean-file download smoke,
and record PASS or FAIL. A backup without a successful isolated restore drill is not accepted.
