import os
import mimetypes
import logging

logger = logging.getLogger(__name__)

def handle_file_response(file):
    """
    Detect file type of an uploaded file and return the appropriate type string.
    
    Args:
        file: The uploaded file object
        
    Returns:
        str: "Image", "Video", or "Unsupported"
    """
    try:
        # Log file details for debugging
        logger.info(f"Processing file: {file.name}, size: {file.size}, content type: {file.content_type}")
        
        # Get file extension and detect mime type
        ext = os.path.splitext(file.name)[1].lower()
        mime_type = file.content_type or mimetypes.guess_type(file.name)[0]
        
        logger.info(f"File extension: {ext}, mime type: {mime_type}")
        
        # Image types
        if mime_type and mime_type.startswith('image/'):
            return "Image"
        elif ext in ['.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.tiff']:
            return "Image"
            
        # Video types
        if mime_type and mime_type.startswith('video/'):
            return "Video"
        elif ext in ['.mp4', '.mov', '.avi', '.wmv', '.flv', '.webm', '.mkv']:
            return "Video"
            
        # If we reach here, the file type is not supported
        logger.warning(f"Unsupported file type: {mime_type or ext}")
        return "Unsupported"
        
    except Exception as e:
        logger.error(f"Error detecting file type: {str(e)}")
        return "Unsupported"