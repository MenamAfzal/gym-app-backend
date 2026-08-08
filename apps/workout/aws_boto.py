"""
Legacy AWS boto module for workout app.
AWS logic has been removed and replaced with local file storage in local_storage.py.
All functions now delegate directly to local file storage.
"""

from .local_storage import (
    save_file_locally,
    delete_local_file,
    delete_local_file_threaded,
    upload_video_local_threaded,
    delete_s3_file,
    delete_s3_file_threaded,
    upload_video_to_s3_threaded,
    upload_image_to_s3,
    food_loger_s3,
    upload_generic_image_to_s3,
)

__all__ = [
    "save_file_locally",
    "delete_local_file",
    "delete_local_file_threaded",
    "upload_video_local_threaded",
    "delete_s3_file",
    "delete_s3_file_threaded",
    "upload_video_to_s3_threaded",
    "upload_image_to_s3",
    "food_loger_s3",
    "upload_generic_image_to_s3",
]