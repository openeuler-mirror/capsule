import base64
import io

from PIL import Image

from core.utils.config import Settings, settings as global_settings


_VLM_IMAGE_MIME_TYPES = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "WEBP": "image/webp",
    "GIF": "image/gif",
    "AVIF": "image/avif",
}


def _read_validated_image(image_path: str) -> tuple[bytes, str]:
    """Read an image only after Pillow can decode and identify its payload."""
    with open(image_path, "rb") as image_file:
        payload = image_file.read()
    if not payload:
        raise ValueError(f"Image file is empty: {image_path}")

    try:
        with Image.open(io.BytesIO(payload)) as image:
            image_format = (image.format or "").upper()
            width, height = image.size
            image.verify()
    except Exception as error:
        raise ValueError(f"Image file cannot be decoded: {image_path}") from error

    if width < 2 or height < 2:
        raise ValueError(
            f"Image is too small for VLM input ({width}x{height}): {image_path}"
        )

    mime = _VLM_IMAGE_MIME_TYPES.get(image_format)
    if not mime:
        raise ValueError(
            f"Unsupported VLM image format {image_format or 'unknown'}: {image_path}"
        )
    return payload, mime


def is_valid_vlm_image_file(image_path: str) -> bool:
    """Return whether a local file is a decodable, useful VLM image."""
    try:
        _read_validated_image(image_path)
        return True
    except (OSError, ValueError):
        return False


def build_image_url(image_path: str, settings: Settings | None = None) -> str:
    """Build image_url payload value for VLM from explicit settings."""
    active_settings = settings or global_settings
    payload, mime = _read_validated_image(image_path)
    b64 = base64.b64encode(payload).decode("utf-8")

    if active_settings.use_data_url_for_vlm_images():
        return f"data:{mime};base64,{b64}"
    return b64
