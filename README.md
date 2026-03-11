# Spatial photo web app (Hexa UI)

This project provides a backend + frontend flow to convert uploaded 2D photos into 3D spatial outputs using Apple's `ml-sharp` pipeline.

## Features

- FastAPI backend with upload API and generated artifact serving.
- Hexa UI style front-end feed with upload modal and spatial cards.
- `scripts/run_ml_sharp.py` pipeline that:
  - runs `sharp predict` when available,
  - falls back to Python ml-sharp inference,
  - writes `output.ply` + `depth.png`,
  - converts PLY to `output.spz`,
  - emits success/failure `manifest.json`.

## Endpoints

- `GET /` - UI.
- `GET /health` - service health check.
- `POST /api/spatial-photos` - upload image and trigger conversion.
- `GET /api/spatial-photos/{job_id}/status` - poll job.
- `DELETE /api/spatial-photos/{job_id}` - delete job.
- `GET /api/feed` - feed pagination.
- `GET /generated/...` - static serving for generated artifacts.

## Local development

### Backend

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload
```

### Frontend

```bash
npm install
npm run dev
```

Vite proxies `/api` and `/generated` to `http://127.0.0.1:8000`.

---

## Modal + Cloudflare deployment

### First-time setup

```bash
pip install modal
python3 -m modal setup
make checkpoints
```

`make checkpoints` downloads the ml-sharp checkpoint (~500MB) into the Modal volume.

### Deploy

```bash
make deploy
```

This deploys backend to Modal and builds frontend assets.

After first deploy backend is live at:

- `https://aubincorinaldiecooper--spatial-fastapi-modal.modal.run`

### Deploy frontend to Cloudflare Pages

1. Run: `npm run build`
2. Go to Cloudflare dashboard → Workers & Pages → Create → Pages
3. Click **Direct Upload**
4. Name the project: `sptl`
5. Upload the `dist/` folder
6. Go to **Custom Domains** → add `sptl.sinestudios.space`

### Local shortcuts

```bash
make dev-backend
make dev-frontend
```

## Environment variables

### Backend

- `ML_SHARP_COMMAND` (default: `python <repo>/scripts/run_ml_sharp.py`)
- `SPATIAL_OUTPUT_ROOT` (default: `<repo>/data/spatial-photos`)
- `MLSHARP_CHECKPOINT` (default: `/app/checkpoints/mlsharp/sharp_2572gikvuh.pt`)

### Frontend

Production `.env.production`:

- `VITE_API_URL=https://aubincorinaldiecooper--spatial-fastapi-modal-fastapi-modal.modal.run`
- `VITE_APP_NAME=SPTL`
