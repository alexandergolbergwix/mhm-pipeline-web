# /run-backend

Start the FastAPI backend on `http://127.0.0.1:8000`.

```bash
pkill -f "uvicorn app.main:app" 2>/dev/null
cd /Users/alexandergo/Documents/Doctorat/mhm-pipeline-web/backend
nohup .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 \
  > /tmp/mhm-web-backend.log 2>&1 &
disown
sleep 2
tail -5 /tmp/mhm-web-backend.log
```

Tail the log later with `tail -f /tmp/mhm-web-backend.log`.
