# Open the distro from PowerShell:  wsl -d Ubuntu
cd /mnt/c/desarrollo/fondos/scripts/ops

# 1. Current state: WAL size, base backups, free disk
bash setup_wal_archive.sh status

# 2. New base backup (pg_basebackup, then pg_verifybackup)
bash setup_wal_archive.sh basebackup

# 3. Restore test: proves the NEW base plus the archived WAL can be restored, before anything old is deleted
bash setup_wal_archive.sh drill

# 4. Prune, dry run first: shows how many old bases and WAL files would be deleted
KEEP_BASES=1 bash setup_wal_archive.sh prune

# 5. Prune for real
KEEP_BASES=1 bash setup_wal_archive.sh prune --yes

# 6. Confirm space was freed
bash setup_wal_archive.sh status