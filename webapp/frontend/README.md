# Job Hunt Agent -- frontend

The React source for the web app. Builds into `../static/`, which `webapp/server.py` serves directly -- there's no separate frontend server in production.

```bash
npm install
npm run dev      # local dev server with hot reload, proxied against the FastAPI backend
npm run build     # rebuilds ../static/ from this source
```

See the repo's root [README](../../README.md) for how to run the whole app.
