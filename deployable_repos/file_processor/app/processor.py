"""File processing logic."""

from io import BytesIO
from pathlib import Path

from PIL import Image

from app import s3


# Supported image extensions for processing
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"}

# Default resize dimensions
DEFAULT_MAX_WIDTH = 800
DEFAULT_MAX_HEIGHT = 800


def is_image(filename: str) -> bool:
    """Check if a file is a supported image type."""
    ext = Path(filename).suffix.lower()
    return ext in IMAGE_EXTENSIONS


def resize_image(image_data: bytes, max_width: int, max_height: int) -> bytes:
    """Resize an image while maintaining aspect ratio."""
    with Image.open(BytesIO(image_data)) as img:
        # Preserve the original format
        original_format = img.format or "PNG"

        # Calculate new dimensions maintaining aspect ratio
        width, height = img.size
        ratio = min(max_width / width, max_height / height)

        # Only resize if the image is larger than the max dimensions
        if ratio < 1:
            new_width = int(width * ratio)
            new_height = int(height * ratio)
            img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)

        # Save to bytes
        output = BytesIO()
        img.save(output, format=original_format)
        return output.getvalue()


def process_file(file_id: str, max_width: int, max_height: int) -> dict:
    """Process a file by ID. For images, resizes them."""
    result = s3.get_file_for_processing(file_id=file_id)
    if result is None:
        return {"success": False, "error": "File not found"}

    content, filename = result

    if not is_image(filename=filename):
        return {
            "success": False,
            "error": f"Unsupported file type. Supported: {', '.join(IMAGE_EXTENSIONS)}",
        }

    # Process the image
    processed_content = resize_image(
        image_data=content,
        max_width=max_width,
        max_height=max_height,
    )

    # Generate processed filename
    path = Path(filename)
    processed_filename = f"{path.stem}_processed{path.suffix}"

    # Save the processed file
    save_result = s3.save_processed_file(
        file_id=file_id,
        filename=processed_filename,
        content=processed_content,
    )

    return {
        "success": True,
        "file_id": file_id,
        "original_filename": filename,
        "processed_filename": processed_filename,
        "location": save_result["location"],
    }
