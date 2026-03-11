#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib
import inspect
import json
import os
import pkgutil
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parents[1]


def _write_manifest(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _write_failure_manifest(output_dir: Path, step: str, exc: Exception) -> None:
    _write_manifest(
        output_dir / "manifest.json",
        {
            "status": "failed",
            "error": str(exc),
            "step": step,
        },
    )


def _default_checkpoint(checkpoints_dir: Path) -> Path:
    candidates = sorted(
        [
            *checkpoints_dir.glob("*.pt"),
            *checkpoints_dir.glob("*.pth"),
            *checkpoints_dir.glob("*.ckpt"),
            *checkpoints_dir.glob("*.bin"),
        ]
    )
    if not candidates:
        raise FileNotFoundError(f"No checkpoint files found in {checkpoints_dir}")
    return candidates[0]


def _load_image_tensor(image_path: Path) -> tuple[Any, Any]:
    import numpy as np
    import torch
    from PIL import Image

    image = Image.open(image_path).convert("RGB")
    image_np = np.array(image, dtype=np.uint8)
    tensor = torch.from_numpy(image_np).permute(2, 0, 1).float() / 255.0
    return tensor.unsqueeze(0), image_np


def _add_ml_sharp_to_path(repo_root: Path) -> Path:
    ml_sharp_root = repo_root / "ml-sharp"
    if not ml_sharp_root.exists():
        raise FileNotFoundError(
            f"Expected ml-sharp clone at {ml_sharp_root}. Clone https://github.com/apple/ml-sharp first."
        )
    sys.path.insert(0, str(ml_sharp_root))
    return ml_sharp_root


def _call_with_supported_args(func: Any, **kwargs: Any) -> Any:
    signature = inspect.signature(func)
    call_kwargs: dict[str, Any] = {}
    for name in signature.parameters:
        if name in kwargs:
            call_kwargs[name] = kwargs[name]
    return func(**call_kwargs)


def _discover_model_loader() -> Any:
    candidate_modules = [
        "ml_sharp",
        "ml_sharp.inference",
        "ml_sharp.pipeline",
        "ml_sharp.models",
        "inference",
        "pipeline",
    ]

    candidate_functions = [
        "load_model",
        "build_model",
        "create_model",
        "get_model",
    ]

    for module_name in candidate_modules:
        try:
            module = importlib.import_module(module_name)
        except Exception:
            continue
        for func_name in candidate_functions:
            func = getattr(module, func_name, None)
            if callable(func):
                return func

    # Broad scan inside ml_sharp package for a callable that looks like a model loader.
    try:
        package = importlib.import_module("ml_sharp")
    except Exception as exc:
        raise RuntimeError("Unable to import ml_sharp package") from exc

    if hasattr(package, "__path__"):
        for _, module_name, _ in pkgutil.walk_packages(package.__path__, prefix="ml_sharp."):
            try:
                module = importlib.import_module(module_name)
            except Exception:
                continue
            for attr_name in dir(module):
                if attr_name.lower() in {"load_model", "build_model", "create_model", "get_model"}:
                    func = getattr(module, attr_name)
                    if callable(func):
                        return func

    raise RuntimeError("Could not find an ml-sharp model loader (load_model/build_model/create_model/get_model)")


def _load_model(checkpoint_path: Path) -> Any:
    loader = _discover_model_loader()
    return _call_with_supported_args(
        loader,
        checkpoint_path=str(checkpoint_path),
        checkpoint=str(checkpoint_path),
        weights=str(checkpoint_path),
        ckpt=str(checkpoint_path),
        device="cpu",
    )


def _run_model_inference(model: Any, image_tensor: Any) -> Any:
    for method_name in ("infer", "predict", "forward", "__call__"):
        method = getattr(model, method_name, None)
        if method is None:
            continue
        if callable(method):
            import torch

            with torch.no_grad():
                if method_name == "__call__":
                    return method(image_tensor)
                return _call_with_supported_args(
                    method,
                    image=image_tensor,
                    image_tensor=image_tensor,
                    inputs=image_tensor,
                    x=image_tensor,
                )

    raise RuntimeError("Loaded model has no callable inference entrypoint")


def _to_numpy(array_like: Any) -> Any:
    import numpy as np
    import torch

    if isinstance(array_like, np.ndarray):
        return array_like
    if torch.is_tensor(array_like):
        return array_like.detach().cpu().numpy()
    return np.asarray(array_like)


def _extract_gaussians(prediction: Any, image_np: Any) -> tuple[Any, Any, Any, Any, Any, Any]:
    import numpy as np

    pred = prediction
    if isinstance(pred, (tuple, list)) and pred:
        pred = pred[0]

    if not isinstance(pred, dict):
        raise RuntimeError("ml-sharp inference output is expected to be a dict with gaussian/depth tensors")

    means_src = next((v for v in [pred.get("means"), pred.get("xyz"), pred.get("positions")] if v is not None), None)
    if means_src is None:
        raise RuntimeError("Inference output does not include gaussian means/xyz/positions")
    means = _to_numpy(means_src)
    if means.ndim == 3:
        means = means[0]

    if means.ndim != 2 or means.shape[1] < 3:
        raise RuntimeError("Inference output does not include valid gaussian means/xyz")
    means = means[:, :3].astype(np.float32)

    colors_src = next((v for v in [pred.get("colors"), pred.get("rgb"), pred.get("features_dc")] if v is not None), None)
    if colors_src is None:
        colors = np.full((means.shape[0], 3), 255, dtype=np.uint8)
    else:
        colors = _to_numpy(colors_src)
        if colors.ndim == 3:
            colors = colors[0]
        colors = colors[:, :3]
        if colors.max() <= 1.0:
            colors = (colors * 255.0).clip(0, 255)
        colors = colors.astype(np.uint8)

    scales_src = next((v for v in [pred.get("scales"), pred.get("scale")] if v is not None), None)
    if scales_src is None:
        scales = np.full((means.shape[0], 3), 0.01, dtype=np.float32)
    else:
        scales = _to_numpy(scales_src)
        if scales.ndim == 3:
            scales = scales[0]
        if scales.shape[1] == 1:
            scales = np.repeat(scales, 3, axis=1)
        scales = scales[:, :3].astype(np.float32)

    opacity_src = next((v for v in [pred.get("opacity"), pred.get("opacities"), pred.get("alpha")] if v is not None), None)
    if opacity_src is None:
        opacity = np.ones((means.shape[0], 1), dtype=np.float32)
    else:
        opacity = _to_numpy(opacity_src)
        if opacity.ndim == 3:
            opacity = opacity[0]
        if opacity.ndim == 1:
            opacity = opacity[:, None]
        opacity = opacity[:, :1].astype(np.float32)

    rot_src = next((v for v in [pred.get("rotations"), pred.get("rotation"), pred.get("quaternions")] if v is not None), None)
    if rot_src is None:
        rotations = np.zeros((means.shape[0], 4), dtype=np.float32)
        rotations[:, 0] = 1.0
    else:
        rotations = _to_numpy(rot_src)
        if rotations.ndim == 3:
            rotations = rotations[0]
        if rotations.shape[1] < 4:
            fixed = np.zeros((rotations.shape[0], 4), dtype=np.float32)
            fixed[:, 0] = 1.0
            fixed[:, : rotations.shape[1]] = rotations
            rotations = fixed
        rotations = rotations[:, :4].astype(np.float32)

    depth_src = next((v for v in [pred.get("depth"), pred.get("depth_map"), pred.get("disparity")] if v is not None), None)
    if depth_src is None:
        depth = np.linalg.norm(means, axis=1)
        h, w, _ = image_np.shape
        side = int(np.sqrt(depth.shape[0]))
        if side * side == depth.shape[0]:
            depth_map = depth.reshape(side, side)
        else:
            depth_map = np.full((h, w), float(depth.mean()), dtype=np.float32)
    else:
        depth_map = _to_numpy(depth_src)
        while depth_map.ndim > 2:
            depth_map = depth_map[0]
        depth_map = depth_map.astype(np.float32)

    return means, colors, scales, opacity, rotations, depth_map


def _write_ply(path: Path, means: Any, colors: Any, scales: Any, opacity: Any, rotations: Any) -> None:
    header = [
        "ply",
        "format ascii 1.0",
        f"element vertex {means.shape[0]}",
        "property float x",
        "property float y",
        "property float z",
        "property uchar red",
        "property uchar green",
        "property uchar blue",
        "property float opacity",
        "property float scale_0",
        "property float scale_1",
        "property float scale_2",
        "property float rot_0",
        "property float rot_1",
        "property float rot_2",
        "property float rot_3",
        "end_header",
    ]
    with path.open("w", encoding="utf-8") as handle:
        handle.write("\n".join(header) + "\n")
        for idx in range(means.shape[0]):
            xyz = means[idx]
            rgb = colors[idx]
            op = float(opacity[idx][0])
            scl = scales[idx]
            rot = rotations[idx]
            handle.write(
                f"{xyz[0]:.7f} {xyz[1]:.7f} {xyz[2]:.7f} "
                f"{int(rgb[0])} {int(rgb[1])} {int(rgb[2])} "
                f"{op:.7f} {scl[0]:.7f} {scl[1]:.7f} {scl[2]:.7f} "
                f"{rot[0]:.7f} {rot[1]:.7f} {rot[2]:.7f} {rot[3]:.7f}\n"
            )


def _save_depth_png(path: Path, depth_map: Any) -> None:
    import numpy as np

    depth = depth_map.astype(np.float32)
    finite_mask = np.isfinite(depth)
    if finite_mask.any():
        dmin = float(depth[finite_mask].min())
        dmax = float(depth[finite_mask].max())
        if dmax > dmin:
            depth_norm = (depth - dmin) / (dmax - dmin)
        else:
            depth_norm = np.zeros_like(depth, dtype=np.float32)
    else:
        depth_norm = np.zeros_like(depth, dtype=np.float32)
    depth_uint8 = np.clip(depth_norm * 255.0, 0, 255).astype(np.uint8)
    from PIL import Image

    Image.fromarray(depth_uint8, mode="L").save(path)


def _convert_rdf_to_rub(means: Any, rotations: Any) -> tuple[Any, Any]:
    import numpy as np

    # RDF (+Z forward) -> RUB (+Y up, +Z back): flip z axis.
    converted_means = means.copy()
    converted_means[:, 2] *= -1.0

    # Quaternion for pure z-flip represented as 180deg around Y axis: (w, x, y, z) = (0, 0, 1, 0)
    # Compose q' = q_flip * q (left-multiply).
    qf = np.array([0.0, 0.0, 1.0, 0.0], dtype=np.float32)
    q = rotations.astype(np.float32)
    w1, x1, y1, z1 = qf
    w2, x2, y2, z2 = q[:, 0], q[:, 1], q[:, 2], q[:, 3]
    composed = np.empty_like(q)
    composed[:, 0] = w1 * w2 - x1 * x2 - y1 * y2 - z1 * z2
    composed[:, 1] = w1 * x2 + x1 * w2 + y1 * z2 - z1 * y2
    composed[:, 2] = w1 * y2 - x1 * z2 + y1 * w2 + z1 * x2
    composed[:, 3] = w1 * z2 + x1 * y2 - y1 * x2 + z1 * w2
    return converted_means, composed


def _convert_ply_to_spz(output_dir: Path, ply_path: Path, means: Any, colors: Any, scales: Any, opacity: Any, rotations: Any) -> Path:
    import spz  # type: ignore

    spz_path = output_dir / "output.spz"

    rub_means, rub_rotations = _convert_rdf_to_rub(means, rotations)

    with tempfile.TemporaryDirectory() as tmp_dir:
        rub_ply = Path(tmp_dir) / "rub_output.ply"
        _write_ply(rub_ply, rub_means, colors, scales, opacity, rub_rotations)

        # Try known spz APIs.
        if hasattr(spz, "read_ply") and hasattr(spz, "write_spz"):
            cloud = spz.read_ply(str(rub_ply))
            spz.write_spz(cloud, str(spz_path))
            return spz_path

        io_mod = getattr(spz, "io", None)
        if io_mod and hasattr(io_mod, "read_ply") and hasattr(io_mod, "write_spz"):
            cloud = io_mod.read_ply(str(rub_ply))
            io_mod.write_spz(cloud, str(spz_path))
            return spz_path

        if hasattr(spz, "load_ply") and hasattr(spz, "save_spz"):
            cloud = spz.load_ply(str(rub_ply))
            spz.save_spz(cloud, str(spz_path))
            return spz_path

        point_cloud_cls = getattr(spz, "PointCloud", None)
        if point_cloud_cls and hasattr(point_cloud_cls, "from_ply"):
            cloud = point_cloud_cls.from_ply(str(rub_ply))
            if hasattr(cloud, "save"):
                cloud.save(str(spz_path))
                return spz_path
            if hasattr(cloud, "to_spz"):
                cloud.to_spz(str(spz_path))
                return spz_path

    raise RuntimeError("Unsupported spz API: could not find a PLY->SPZ conversion entrypoint")


def _resolve_checkpoint_path(checkpoint_arg: str | None) -> Path:
    default_path = os.environ.get("MLSHARP_CHECKPOINT", "/app/checkpoints/mlsharp/sharp_2572gikvuh.pt")
    raw = checkpoint_arg or default_path
    path = Path(raw)
    if not path.is_absolute():
        path = (BASE_DIR / path).resolve()
    return path


def _normalize_sharp_outputs(output_dir: Path, input_path: Path) -> tuple[Path, Path]:
    stem = input_path.stem
    ply_candidates = [output_dir / f"{stem}.ply", output_dir / "output.ply"]
    depth_candidates = [output_dir / f"{stem}_depth.png", output_dir / "depth.png"]

    ply_src = next((candidate for candidate in ply_candidates if candidate.exists()), None)
    depth_src = next((candidate for candidate in depth_candidates if candidate.exists()), None)

    if ply_src is None or depth_src is None:
        raise FileNotFoundError("sharp predict did not produce expected PLY/depth outputs")

    ply_dst = output_dir / "output.ply"
    depth_dst = output_dir / "depth.png"
    if ply_src != ply_dst:
        ply_src.replace(ply_dst)
    if depth_src != depth_dst:
        depth_src.replace(depth_dst)

    return ply_dst, depth_dst


def _run_sharp_cli_predict(input_path: Path, output_dir: Path, checkpoint_path: Path) -> tuple[Path, Path] | None:
    sharp_bin = shutil.which("sharp")
    if not sharp_bin:
        return None

    command = [
        sharp_bin,
        "predict",
        "-i",
        str(input_path),
        "-o",
        str(output_dir),
        "-c",
        str(checkpoint_path),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise RuntimeError(
            f"sharp predict failed (exit={completed.returncode}): {completed.stderr.strip() or completed.stdout.strip()}"
        )

    return _normalize_sharp_outputs(output_dir, input_path)


def _convert_ply_file_to_spz(ply_path: Path, output_dir: Path) -> Path:
    import spz  # type: ignore

    spz_path = output_dir / "output.spz"

    if hasattr(spz, "read_ply") and hasattr(spz, "write_spz"):
        cloud = spz.read_ply(str(ply_path))
        spz.write_spz(cloud, str(spz_path))
        return spz_path

    io_mod = getattr(spz, "io", None)
    if io_mod and hasattr(io_mod, "read_ply") and hasattr(io_mod, "write_spz"):
        cloud = io_mod.read_ply(str(ply_path))
        io_mod.write_spz(cloud, str(spz_path))
        return spz_path

    if hasattr(spz, "load_ply") and hasattr(spz, "save_spz"):
        cloud = spz.load_ply(str(ply_path))
        spz.save_spz(cloud, str(spz_path))
        return spz_path

    point_cloud_cls = getattr(spz, "PointCloud", None)
    if point_cloud_cls and hasattr(point_cloud_cls, "from_ply"):
        cloud = point_cloud_cls.from_ply(str(ply_path))
        if hasattr(cloud, "save"):
            cloud.save(str(spz_path))
            return spz_path
        if hasattr(cloud, "to_spz"):
            cloud.to_spz(str(spz_path))
            return spz_path

    raise RuntimeError("Unsupported spz API: could not find a PLY->SPZ conversion entrypoint")


def _assert_required_outputs(output_dir: Path) -> None:
    required = {
        "output.ply": output_dir / "output.ply",
        "output.spz": output_dir / "output.spz",
        "depth.png": output_dir / "depth.png",
    }
    missing = [name for name, path in required.items() if not path.exists()]
    if missing:
        raise FileNotFoundError(f"Pipeline missing required output artifacts: {', '.join(missing)}")


def run_pipeline(input_path: Path, output_dir: Path, checkpoint_arg: str | None = None) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        step = "resolve_checkpoint"
        checkpoint = _resolve_checkpoint_path(checkpoint_arg)

        step = "run_inference"
        cli_result = None
        try:
            cli_result = _run_sharp_cli_predict(input_path, output_dir, checkpoint)
        except Exception as cli_exc:
            print(f"sharp CLI path failed, falling back to python inference: {cli_exc}", file=sys.stderr)

        if cli_result is not None:
            ply_path, _ = cli_result
            step = "convert_ply_to_spz"
            _convert_ply_file_to_spz(ply_path, output_dir)
        else:
            step = "load_ml_sharp_model"
            _add_ml_sharp_to_path(BASE_DIR)
            model = _load_model(checkpoint)

            step = "python_inference"
            image_tensor, image_np = _load_image_tensor(input_path)
            prediction = _run_model_inference(model, image_tensor)

            step = "save_intermediate_ply"
            means, colors, scales, opacity, rotations, depth_map = _extract_gaussians(prediction, image_np)
            ply_path = output_dir / "output.ply"
            _write_ply(ply_path, means, colors, scales, opacity, rotations)
            _save_depth_png(output_dir / "depth.png", depth_map)

            step = "convert_ply_to_spz"
            _convert_ply_to_spz(output_dir, ply_path, means, colors, scales, opacity, rotations)

        step = "validate_outputs"
        _assert_required_outputs(output_dir)

        step = "emit_manifest"
        _write_manifest(
            output_dir / "manifest.json",
            {
                "status": "done",
                "spz_url": None,
                "depth_map": "depth.png",
                "ply": "output.ply",
                "spz": "output.spz",
            },
        )
    except Exception as exc:
        _write_failure_manifest(output_dir, step if "step" in locals() else "startup", exc)
        raise


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Apple ML Sharp inference and export PLY/SPZ artifacts")
    parser.add_argument("--input", required=True, help="Path to input image")
    parser.add_argument("--output", required=True, help="Directory for generated artifacts")
    parser.add_argument("--checkpoint", required=False, help="Optional checkpoint override path")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    input_path = Path(args.input).resolve()
    output_dir = Path(args.output).resolve()

    try:
        run_pipeline(input_path, output_dir, checkpoint_arg=args.checkpoint)
        return 0
    except Exception as exc:
        print(f"run_ml_sharp failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
