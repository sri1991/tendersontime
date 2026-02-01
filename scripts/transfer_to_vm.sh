#!/bin/bash

# Usage: ./transfer_to_vm.sh <INSTANCE_NAME> <ZONE>
# Example: ./transfer_to_vm.sh my-vm us-central1-a

if [ -z "$1" ] || [ -z "$2" ]; then
    echo "Usage: $0 <INSTANCE_NAME> <ZONE>"
    exit 1
fi

VM_NAME=$1
ZONE=$2
REMOTE_DIR="~/tendersontime" # Adjust if you put code elsewhere

echo "--- 1. Zipping ChromaDB (Size: ~1.3GB) ---"
# Zip efficiently (-1 for speed, we just want a single file)
tar -czf chroma_db.tar.gz chroma_db

echo "--- 2. Uploading .env and ChromaDB ---"
# Upload .env
gcloud compute scp .env $VM_NAME:$REMOTE_DIR/ --zone=$ZONE
if [ $? -ne 0 ]; then
    echo "Error uploading .env. Check gcloud authentication."
    exit 1
fi

# Upload Archive
gcloud compute scp chroma_db.tar.gz $VM_NAME:$REMOTE_DIR/ --zone=$ZONE

echo "--- 3. Unpacking on Remote VM ---"
# Run remote command to unzip
gcloud compute ssh $VM_NAME --zone=$ZONE --command "cd $REMOTE_DIR && tar -xzf chroma_db.tar.gz && rm chroma_db.tar.gz"

echo "✅ Transfer Complete!"
