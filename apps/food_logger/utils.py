import os
import io
import uuid
import base64
import logging
import mimetypes
import urllib.request
from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.core.files.uploadedfile import UploadedFile

logger = logging.getLogger(__name__)

RECIPE_IMAGE_SUBFOLDER = 'recipe_images'


def save_recipe_image(image_input, request=None, subfolder=RECIPE_IMAGE_SUBFOLDER):
    """
    Saves a recipe image onto the backend filesystem under MEDIA_ROOT/<subfolder>
    and returns its media path (e.g. '/media/recipe_images/<uuid>.<ext>').

    Supports:
    1. UploadedFile (e.g. from request.FILES)
    2. Base64 data URI string (e.g. 'data:image/jpeg;base64,...')
    3. Remote HTTP/HTTPS URL (downloads and stores the image on the backend)
    4. Local media path (preserves existing valid media path if file exists)
    """
    if not image_input:
        return None

    upload_dir = os.path.join(settings.MEDIA_ROOT, subfolder)
    os.makedirs(upload_dir, exist_ok=True)
    media_prefix = f"/media/{subfolder}/"

    # Case 1: UploadedFile (Django in-memory or temporary file)
    if isinstance(image_input, UploadedFile) or (hasattr(image_input, 'read') and hasattr(image_input, 'name')):
        original_name = getattr(image_input, 'name', 'recipe.jpg')
        ext = os.path.splitext(original_name)[1].lower()
        if not ext or ext not in ['.jpg', '.jpeg', '.png', '.webp', '.gif']:
            ext = '.jpg'

        filename = f"{uuid.uuid4().hex}{ext}"
        fs = FileSystemStorage(location=upload_dir, base_url=media_prefix)
        if hasattr(image_input, 'seek'):
            image_input.seek(0)
        saved_name = fs.save(filename, image_input)
        return fs.url(saved_name)

    # If not a string at this point, cannot process
    if not isinstance(image_input, str):
        return None

    image_str = image_input.strip()
    if not image_str:
        return None

    # Case 2: Base64 data URI (data:image/...;base64,...)
    if image_str.startswith('data:image/') and ';base64,' in image_str:
        try:
            header, base64_data = image_str.split(';base64,', 1)
            mime_type = header.replace('data:', '')
            ext = mimetypes.guess_extension(mime_type) or '.jpg'
            if ext == '.jpe':
                ext = '.jpg'

            img_bytes = base64.b64decode(base64_data)
            filename = f"{uuid.uuid4().hex}{ext}"
            file_path = os.path.join(upload_dir, filename)
            with open(file_path, 'wb') as f:
                f.write(img_bytes)

            return f"{media_prefix}{filename}"
        except Exception as e:
            logger.error(f"Error saving base64 recipe image: {e}")
            return None

    # Case 3: Already points to this backend's media directory
    if media_prefix in image_str:
        filename = image_str.split(media_prefix, 1)[1].split('?')[0]
        file_path = os.path.join(upload_dir, filename)
        if os.path.exists(file_path):
            return f"{media_prefix}{filename}"

    # Case 4: Remote HTTP / HTTPS URL - Download and save locally
    if image_str.startswith(('http://', 'https://')):
        try:
            req = urllib.request.Request(
                image_str,
                headers={'User-Agent': 'Mozilla/5.0 (GymApp Backend Image Fetcher)'}
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                content_type = resp.headers.get_content_type()
                img_bytes = resp.read()

            ext = mimetypes.guess_extension(content_type) if content_type else None
            if not ext or ext == '.jpe':
                clean_url = image_str.split('?')[0]
                ext = os.path.splitext(clean_url)[1].lower()
            if not ext or ext not in ['.jpg', '.jpeg', '.png', '.webp', '.gif']:
                ext = '.jpg'

            filename = f"{uuid.uuid4().hex}{ext}"
            file_path = os.path.join(upload_dir, filename)
            with open(file_path, 'wb') as f:
                f.write(img_bytes)

            return f"{media_prefix}{filename}"
        except Exception as e:
            logger.warning(f"Could not download remote recipe image '{image_str}': {e}. Falling back to original URL.")
            # Gracefully preserve URL if download fails (e.g. network unreachable or invalid URL)
            return image_str

    # Case 5: Bare filename that already exists in upload_dir
    bare_filename = os.path.basename(image_str)
    candidate_path = os.path.join(upload_dir, bare_filename)
    if os.path.exists(candidate_path):
        return f"{media_prefix}{bare_filename}"

    # If it's a relative path like /media/something_else
    if image_str.startswith('/media/'):
        return image_str

    return image_str


def get_full_recipe_image_url(image_val, request=None, subfolder=RECIPE_IMAGE_SUBFOLDER):
    """
    Constructs a fully qualified absolute URL for displaying a recipe image on the frontend.
    """
    if not image_val:
        return None

    image_str = str(image_val).strip()
    if not image_str:
        return None

    # Already an absolute HTTP/HTTPS URL
    if image_str.startswith(('http://', 'https://')):
        return image_str

    # Ensure it starts with /media/
    if not image_str.startswith('/'):
        image_str = f"/media/{subfolder}/{image_str}"

    if request:
        return request.build_absolute_uri(image_str)

    base_url = getattr(settings, 'BASE_URL', 'http://16.171.26.53')
    return f"{base_url.rstrip('/')}{image_str}"


def save_local_recipe_image(file_obj, obj):
    """
    Backward-compatible helper for saving recipe image onto model instance.
    """
    url = save_recipe_image(file_obj)
    if url and hasattr(obj, 'image'):
        obj.image = url
        obj.save(update_fields=['image'] if hasattr(obj, '_state') and obj.pk else None)
    return url


# Backward compatibility alias
food_loger_s3 = save_local_recipe_image
