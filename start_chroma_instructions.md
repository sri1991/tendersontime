
To start the ChromaDB server on your VM pointing to a specific path and port in the background, follow these steps:

### 1. SSH into your VM
```bash
ssh username@136.114.154.210
```

### 2. Activate your Virtual Environment (if applicable)
```bash
source venv/bin/activate
```

### 3. Run ChromaDB in the Background (nohup)
Replace `/full/path/to/your/chroma_db` with the actual absolute path to your data directory.

```bash
nohup chroma run --path /full/path/to/your/chroma_db --port 8000 --host 0.0.0.0 > chroma.log 2>&1 &
```

### 4. Verify it's Running
Check the logs:
```bash
tail -f chroma.log
```

Check the process:
```bash
ps aux | grep chroma
```

### Note:
- `--host 0.0.0.0` is required to allow external connections.
- The default port is `8000`.
- Ensure your VM firewall (e.g., AWS Security Group, UFW) allows inbound traffic on port `8000`.
