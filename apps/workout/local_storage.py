import os
import uuid
import threading
import shutil
from pathlib import Path
from django.conf import settings
from django.core.files.storage import default_storage
from django.core.files.base import ContentFile

def get_media_url(relative_path):
    media_url = getattr(settings, 'MEDIA_URL', '/media/')
    if not media_url.endswith('/'):
        media_url += '/'
    relative_path = str(relative_path).lstrip('/')
    return f"{media_url}{relative_path}"

def save_file_locally(file_obj_or_bytes, folder="videos", filename=None):
    if isinstance(file_obj_or_bytes, (str, Path)):
        source_path = Path(file_obj_or_bytes)
        if not filename:
            filename = source_path.name
        filename = filename.replace(" ", "_")
        unique_name = f"{uuid.uuid4().hex}_{filename}"
        subpath = os.path.join(folder, unique_name)
        dest_path = Path(settings.MEDIA_ROOT) / subpath
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, dest_path)
        return subpath, get_media_url(subpath)

    if hasattr(file_obj_or_bytes, 'name') and not filename:
        filename = file_obj_or_bytes.name
    if not filename:
        filename = "file.bin"
    
    filename = os.path.basename(filename).replace(" ", "_")
    unique_name = f"{uuid.uuid4().hex}_{filename}"
    subpath = os.path.join(folder, unique_name)

    if hasattr(file_obj_or_bytes, 'read'):
        content = file_obj_or_bytes.read()
    else:
        content = file_obj_or_bytes

    saved_path = default_storage.save(subpath, ContentFile(content))
    return saved_path, get_media_url(saved_path)

def delete_local_file(file_url_or_path):
    if not file_url_or_path:
        return False
    try:
        path_str = str(file_url_or_path)
        media_url = getattr(settings, 'MEDIA_URL', '/media/')
        
        if media_url in path_str:
            relative_path = path_str.split(media_url, 1)[1]
        elif path_str.startswith('/media/'):
            relative_path = path_str.replace('/media/', '', 1)
        else:
            relative_path = path_str

        relative_path = relative_path.lstrip('/')

        if default_storage.exists(relative_path):
            default_storage.delete(relative_path)
            print(f"Deleted local file: {relative_path}")
            return True

        abs_path = Path(settings.MEDIA_ROOT) / relative_path
        if abs_path.exists():
            abs_path.unlink()
            print(f"Deleted local file from disk: {abs_path}")
            return True

    except Exception as e:
        print(f"Failed to delete local file ({file_url_or_path}): {e}")
    return False

def delete_local_file_threaded(file_url_or_path):
    thread = threading.Thread(target=delete_local_file, args=(file_url_or_path,), daemon=True)
    thread.start()

def upload_video_local_threaded(file_obj, exercise, expected_s3_key=None):
    def _upload():
        try:
            rel_path, file_url = save_file_locally(file_obj, folder="videos")
            exercise.video_url = file_url
            exercise.video_file = rel_path
            exercise.upload_status = "uploaded"
            exercise.save(update_fields=["video_url", "video_file", "upload_status"])
            print(f"Local video stored successfully: {file_url}")
        except Exception as e:
            exercise.upload_status = "failed"
            exercise.save(update_fields=["upload_status"])
            print(f"Local video save failed: {e}")

    thread = threading.Thread(target=_upload, daemon=True)
    thread.start()

delete_s3_file = delete_local_file
delete_s3_file_threaded = delete_local_file_threaded
upload_video_to_s3_threaded = upload_video_local_threaded

def upload_image_to_s3(file_obj, beverage_instance, async_upload=True):
    def _upload():
        try:
            _, file_url = save_file_locally(file_obj, folder="photos")
            if beverage_instance:
                beverage_instance.image = file_url
                beverage_instance.save(update_fields=["image"])
            return file_url
        except Exception as e:
            print(f"Local image save failed: {e}")
            return None

    if async_upload:
        thread = threading.Thread(target=_upload, daemon=True)
        thread.start()
        return "Uploading in background..."
    else:
        return _upload()

def food_loger_s3(file_obj, beverage_instance, async_upload=True):
    return upload_image_to_s3(file_obj, beverage_instance, async_upload=async_upload)

def upload_generic_image_to_s3(file_obj, folder="general"):
    _, file_url = save_file_locally(file_obj, folder=folder)
    return file_url
