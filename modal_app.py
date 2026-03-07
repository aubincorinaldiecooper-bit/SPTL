from __future__ import annotations

import modal

app = modal.App("spatial-fastapi-modal")

DATA_VOLUME = modal.Volume.from_name("spatial-data", create_if_missing=True)
CHECKPOINTS_VOLUME = modal.Volume.from_name(
    "spatial-checkpoints", create_if_missing=True
)
SECRETS = [modal.Secret.from_name("spatial-app-secrets")]

image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("git", "ffmpeg", "libgl1-mesa-glx", "libglib2.0-0")
    .run_commands(
        "git clone https://github.com/apple/ml-sharp /app/ml-sharp",
        "pip install -r /app/ml-sharp/requirements.in",
    )
    .pip_install_from_requirements("requirements.txt")
    .pip_install("spz", "huggingface_hub")
    .add_local_dir(".", remote_path="/app")
)


@app.function(
    image=image,
    gpu=modal.gpu.A10G(),
    volumes={
        "/app/data": DATA_VOLUME,
        "/app/checkpoints": CHECKPOINTS_VOLUME,
    },
    secrets=SECRETS,
    allow_concurrent_inputs=10,
    timeout=300,
)
@modal.asgi_app()
def fastapi_modal():
    import sys

    sys.path.insert(0, "/app")
    from app.main import app as fastapi_app

    return fastapi_app


@app.function(
    image=image,
    volumes={"/app/checkpoints": CHECKPOINTS_VOLUME},
    secrets=SECRETS,
    timeout=600,
)
def download_checkpoints() -> None:
    import os
    from pathlib import Path

    from huggingface_hub import hf_hub_download

    ckpt_path = Path("/app/checkpoints/mlsharp/sharp_2572gikvuh.pt")

    if not ckpt_path.exists():
        ckpt_path.parent.mkdir(parents=True, exist_ok=True)
        hf_hub_download(
            repo_id="apple/Sharp",
            filename="sharp_2572gikvuh.pt",
            local_dir=str(ckpt_path.parent),
            token=os.environ.get("HF_TOKEN"),
        )
        print("✓ ml-sharp checkpoint downloaded")
    else:
        print("✓ ml-sharp checkpoint already exists, skipping")

    CHECKPOINTS_VOLUME.commit()
    print("✓ Checkpoints ready")


@app.local_entrypoint()
def main() -> None:
    download_checkpoints.remote()
    print("Checkpoints ready. Run: modal deploy modal_app.py")
