import base64
import os

from core.utils.config import Settings, settings as global_settings


def build_image_url(image_path: str, settings: Settings | None = None) -> str:
    """Build image_url payload value for VLM from explicit settings."""
    active_settings = settings or global_settings
    ext = os.path.splitext(image_path)[1].lower()
    mime_types = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".avif": "image/avif",
    }
    mime = mime_types.get(ext, "image/png")
    with open(image_path, "rb") as image_file:
        b64 = base64.b64encode(image_file.read()).decode("utf-8")

    if active_settings.use_data_url_for_vlm_images():
        return f"data:{mime};base64,{b64}"
    return b64
