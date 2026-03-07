from __future__ import annotations

import modal

modal_app = modal.App("spatial-fastapi-modal")

data_volume = modal.Volume.from_name("spatial-data", create_if_missing=True)
checkpoints_volume = modal.Volume.from_name("spatial-checkpoints", create_if_missing=True)

image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install(["git", "ffmpeg", "libgl1-mesa-glx", "libglib2.0-0"])
    .run_commands("git clone https://github.com/apple/ml-sharp /app/ml-sharp")
    .run_commands("pip install -r /app/ml-sharp/requirements.in")
    .pip_install_from_requirements("requirements.txt")
    .pip_install(["spz", "huggingface_hub"])
    .copy_local_dir(".", "/app")
)


@modal_app.function(
    image=image,
    gpu=modal.gpu.A10G(),
    volumes={
        "/app/data": data_volume,
        "/app/checkpoints": checkpoints_volume,
    },
    secrets=[modal.Secret.from_name("spatial-app-secrets")],
    allow_concurrent_inputs=10,
    timeout=300,
)
@modal.asgi_app()
def fastapi_modal():
    import sys

    sys.path.insert(0, "/app")
    from app.main import app as fastapi_app

    return fastapi_app


@modal_app.function(
    image=image,
    volumes={"/app/checkpoints": checkpoints_volume},
    secrets=[modal.Secret.from_name("spatial-app-secrets")],
    timeout=600,
)
def download_checkpoints():
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

    checkpoints_volume.commit()
    print("✓ Checkpoints ready")


@modal_app.local_entrypoint()
def main():
    download_checkpoints.remote()
    print("Checkpoints ready. Run: modal deploy modal_app.py")
