#!/bin/bash
# Sync Obsidian vault to Hetzner server

VAULT_PATH="/Users/sonyjr/Documents/OpenClaw-Vault"
SERVER="root@65.108.155.83"
REMOTE_PATH="/root/obsidian-vault"

echo "Syncing vault to Hetzner..."
sshpass -p 'Le210898' rsync -avz --delete \
  --exclude='.git' \
  --exclude='.obsidian' \
  --exclude='.DS_Store' \
  --exclude='*.canvas' \
  --exclude='*.base' \
  --exclude='venv' \
  --exclude='__pycache__' \
  --exclude='chroma_db' \
  "$VAULT_PATH/" "$SERVER:$REMOTE_PATH/"

echo "Sync complete: $(date)"
