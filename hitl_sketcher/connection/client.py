"""REST API client for backend communication.

Uses Python's urllib to avoid external dependencies (QGIS Python environment
may not have httpx/requests). All calls are synchronous — for long operations,
the backend returns immediately and the plugin polls for status.
"""

from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request
import urllib.error
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class BackendClient:
    """HTTP client for the HITL segmentation backend.

    All methods return parsed JSON dicts. Raises on HTTP errors.
    """

    def __init__(self, base_url: str = "http://localhost:8000"):
        self._validate_scheme(base_url)
        self.base_url = base_url.rstrip("/")
        self._api_key: Optional[str] = None

    def set_url(self, url: str) -> None:
        self._validate_scheme(url)
        self.base_url = url.rstrip("/")

    def set_api_key(self, key: Optional[str]) -> None:
        self._api_key = key if key else None

    def _auth_headers(self, extra: Optional[dict] = None) -> dict:
        h = {}
        if self._api_key:
            h["Authorization"] = f"Bearer {self._api_key}"
        if extra:
            h.update(extra)
        return h

    @staticmethod
    def _validate_scheme(url: str) -> None:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ("http", "https"):
            raise ValueError(f"Only http/https URLs are supported, got: {parsed.scheme!r}")

    # --- Low-level ---

    def _get(self, path: str) -> dict:
        url = f"{self.base_url}{path}"
        try:
            req = urllib.request.Request(url, headers=self._auth_headers())
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.URLError as e:
            logger.error("GET %s failed: %s", url, e)
            raise ConnectionError(f"Backend unavailable: {e}") from e

    def _post(self, path: str, data: dict = None) -> dict:
        url = f"{self.base_url}{path}"
        body = json.dumps(data or {}).encode()
        try:
            req = urllib.request.Request(
                url, data=body,
                headers=self._auth_headers({"Content-Type": "application/json"}),
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=300) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.URLError as e:
            logger.error("POST %s failed: %s", url, e)
            raise ConnectionError(f"Backend unavailable: {e}") from e

    def _delete(self, path: str) -> dict:
        url = f"{self.base_url}{path}"
        try:
            req = urllib.request.Request(url, headers=self._auth_headers(), method="DELETE")
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.URLError as e:
            logger.error("DELETE %s failed: %s", url, e)
            raise ConnectionError(f"Backend unavailable: {e}") from e

    def _upload_file(self, path: str, file_path: str, field_name: str = "file") -> dict:
        """Upload a file via multipart form data."""
        import mimetypes
        boundary = "----HITLBoundary"
        filename = Path(file_path).name
        content_type = mimetypes.guess_type(file_path)[0] or "application/octet-stream"

        with open(file_path, "rb") as f:
            file_data = f.read()

        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"\r\n'
            f"Content-Type: {content_type}\r\n\r\n"
        ).encode() + file_data + f"\r\n--{boundary}--\r\n".encode()

        url = f"{self.base_url}{path}"
        req = urllib.request.Request(
            url,
            data=body,
            headers=self._auth_headers({"Content-Type": f"multipart/form-data; boundary={boundary}"}),
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read().decode())

    def _download_file(self, path: str, output_path: str) -> str:
        """Download a file from the backend."""
        url = f"{self.base_url}{path}"
        req = urllib.request.Request(url, headers=self._auth_headers())
        with urllib.request.urlopen(req, timeout=120) as resp:
            with open(output_path, "wb") as f:
                f.write(resp.read())
        return output_path

    # --- Health ---

    def health_check(self) -> dict:
        return self._get("/health")

    # --- Projects ---

    def list_projects(self) -> List[dict]:
        result = self._get("/api/projects/list")
        return result.get("projects", [])

    def create_project(self, project_id: str, name: str, description: str = "") -> dict:
        return self._post("/api/projects/create", {
            "project_id": project_id,
            "name": name,
            "description": description,
        })

    def switch_project(self, project_id: str) -> dict:
        return self._post("/api/projects/switch", {"project_id": project_id})

    def get_active_project(self) -> dict:
        return self._get("/api/projects/active")

    def delete_project(self, project_id: str) -> dict:
        return self._delete(f"/api/projects/{project_id}")

    # --- Classes ---

    def get_classes(self) -> List[dict]:
        result = self._get("/api/labels/classes")
        return result.get("classes", [])

    def set_classes(self, classes: List[dict]) -> dict:
        return self._post("/api/labels/classes", {"classes": classes})

    # --- Regions ---

    def get_regions(self, crs: str = "EPSG:4326") -> List[dict]:
        result = self._get(f"/api/labels/regions?crs={crs}")
        return result.get("regions", [])

    def add_region(self, geometry_geojson: dict, crs: str = "EPSG:4326") -> dict:
        return self._post("/api/labels/regions", {
            "geometry_geojson": geometry_geojson,
            "crs": crs,
        })

    # --- Annotations ---

    def get_annotations(self, region_id: Optional[int] = None, crs: str = "EPSG:4326") -> List[dict]:
        path = f"/api/labels/annotations?crs={crs}"
        if region_id is not None:
            path += f"&region_id={region_id}"
        result = self._get(path)
        return result.get("annotations", [])

    def add_annotation(
        self,
        geometry_geojson: dict,
        class_id: int,
        region_id: int,
        crs: str = "EPSG:4326",
        source: str = "manual",
        iteration: int = 0,
    ) -> dict:
        return self._post("/api/labels/annotations", {
            "geometry_geojson": geometry_geojson,
            "class_id": class_id,
            "region_id": region_id,
            "crs": crs,
            "source": source,
            "iteration": iteration,
        })

    def delete_annotation(self, annotation_index: int) -> dict:
        """Delete a single annotation by index."""
        return self._delete(f"/api/labels/annotations/{annotation_index}")

    def delete_region(self, region_id: int) -> dict:
        """Delete a region and all its annotations."""
        return self._delete(f"/api/labels/regions/{region_id}")

    def delete_region_annotations(self, region_id: int) -> dict:
        """Delete all annotations in a region (keep the region)."""
        return self._delete(f"/api/labels/annotations/region/{region_id}")

    def upload_labels(self, gpkg_path: str) -> dict:
        return self._upload_file("/api/labels/upload", gpkg_path)

    def get_label_stats(self) -> dict:
        return self._get("/api/labels/stats")

    # --- Dataset ---

    def build_dataset(self, raster_path: str, target_crs: str = "") -> dict:
        return self._post("/api/dataset/build", {
            "raster_path": raster_path,
            "target_crs": target_crs,
        })

    # --- Training ---

    def start_training(self, raster_path: str, project_id: str = "default") -> dict:
        return self._post("/api/training/start", {
            "raster_path": raster_path,
            "project_id": project_id,
        })

    def stop_training(self) -> dict:
        return self._post("/api/training/stop")

    def get_training_status(self) -> dict:
        return self._get("/api/training/status")

    def get_training_metrics(self, run_id: Optional[str] = None) -> List[dict]:
        path = f"/api/training/metrics/{run_id}" if run_id else "/api/training/metrics"
        result = self._get(path)
        return result.get("metrics", [])

    # --- Raster Sources ---

    def register_xyz_source(self, name: str, url_template: str, default_zoom: int = 18) -> dict:
        """Register an XYZ tile source with the backend."""
        return self._post("/api/raster/register-xyz", {
            "name": name,
            "url_template": url_template,
            "default_zoom": default_zoom,
        })

    def list_raster_sources(self) -> List[dict]:
        """List all registered raster sources."""
        result = self._get("/api/raster/sources")
        return result.get("sources", [])

    # --- Review workflow ---

    def promote_inference(self, aoi_geojson: dict, job_id: str) -> dict:
        """Promote inference results to in-review annotations."""
        return self._post("/api/labels/promote-inference", {
            "aoi_geojson": aoi_geojson,
            "job_id": job_id,
        })

    def approve_region(self, region_id: int) -> dict:
        """Approve an in-review region and its annotations for training."""
        return self._post(f"/api/labels/regions/{region_id}/approve", {})

    # --- Inference ---

    def start_inference(
        self,
        aoi_bounds: List[float],
        project_id: str = "default",
        checkpoint_run_id: Optional[str] = None,
        xyz_url: Optional[str] = None,
        xyz_zoom: int = 18,
        raster_path: Optional[str] = None,
    ) -> dict:
        data: Dict[str, Any] = {
            "aoi_bounds": aoi_bounds,
            "project_id": project_id,
        }
        if xyz_url:
            data["xyz_url"] = xyz_url
            data["xyz_zoom"] = xyz_zoom
        elif raster_path:
            data["raster_path"] = raster_path
        if checkpoint_run_id:
            data["checkpoint_run_id"] = checkpoint_run_id
        return self._post("/api/inference/predict", data)

    def start_inference_upload(
        self,
        image_path: str,
        aoi_bounds: List[float],
        project_id: str = "default",
        checkpoint_run_id: Optional[str] = None,
    ) -> dict:
        """Upload a captured GeoTIFF and start inference on it.

        Sends the file plus form fields as multipart/form-data.
        """
        import mimetypes
        boundary = "----HITLBoundary"
        filename = Path(image_path).name
        content_type = mimetypes.guess_type(image_path)[0] or "application/octet-stream"

        with open(image_path, "rb") as f:
            file_data = f.read()

        # Build multipart body: file + form fields
        parts = []
        # File part
        parts.append(
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            f"Content-Type: {content_type}\r\n\r\n"
        )
        # Form field parts
        fields = {
            "aoi_bounds": json.dumps(aoi_bounds),
            "project_id": project_id,
        }
        if checkpoint_run_id:
            fields["checkpoint_run_id"] = checkpoint_run_id

        field_parts = b""
        for name, value in fields.items():
            field_parts += (
                f"\r\n--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
                f"{value}"
            ).encode()

        body = parts[0].encode() + file_data + field_parts + f"\r\n--{boundary}--\r\n".encode()

        url = f"{self.base_url}/api/inference/predict-upload"
        req = urllib.request.Request(
            url,
            data=body,
            headers=self._auth_headers({"Content-Type": f"multipart/form-data; boundary={boundary}"}),
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=300) as resp:
            return json.loads(resp.read().decode())

    def get_inference_status(self) -> dict:
        return self._get("/api/inference/status")

    def download_prediction(self, job_id: str, file_type: str, output_path: str) -> str:
        return self._download_file(f"/api/inference/result/{job_id}/{file_type}", output_path)

    # --- Models ---

    def list_models(self) -> List[dict]:
        result = self._get("/api/models/list")
        return result.get("checkpoints", [])

    def get_best_model(self) -> Optional[dict]:
        result = self._get("/api/models/best")
        return result.get("checkpoint")

    # --- SAM3 ---

    def sam_set_image(self, image_path: str) -> dict:
        """Upload image to SAM3 and start interactive session."""
        return self._upload_file("/api/sam/set-image", image_path)

    def sam_prompt(
        self,
        point_coords: Optional[List[List[float]]] = None,
        point_labels: Optional[List[int]] = None,
        box: Optional[List[float]] = None,
        reset_prompts: bool = False,
    ) -> dict:
        """Send point/box prompt to SAM3, get mask back."""
        data = {"reset_prompts": reset_prompts}
        if point_coords is not None:
            data["point_coords"] = point_coords
        if point_labels is not None:
            data["point_labels"] = point_labels
        if box is not None:
            data["box"] = box
        return self._post("/api/sam/prompt", data)

    def sam_accept(
        self,
        class_id: int,
        region_id: int,
        crs: str = "EPSG:4326",
        simplify_tolerance: float = 1.0,
    ) -> dict:
        """Accept current SAM3 mask and save as annotation.

        The backend reads the affine transform from the session's GeoTIFF,
        so the polygon is automatically geo-referenced.
        """
        data = {
            "class_id": class_id,
            "region_id": region_id,
            "crs": crs,
            "simplify_tolerance": simplify_tolerance,
        }
        return self._post("/api/sam/accept", data)

    def sam_session(self) -> dict:
        """Get current SAM3 session info."""
        return self._get("/api/sam/session")

    def sam_reset(self) -> dict:
        """Reset SAM3 session."""
        return self._post("/api/sam/reset")
