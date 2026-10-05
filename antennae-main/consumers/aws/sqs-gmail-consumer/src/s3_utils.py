# src/s3_utils.py

import asyncio

from common_lib.storage.storage_client import StorageService  # <--- NEW IMPORT
from common_lib.utils.logger import setup_logger

logger = setup_logger(__name__)


# Pre-initialize the StorageService instance (it's essentially stateless and handles client init)
storage_service = StorageService()
# Initialize the client immediately since the instance is created globally
storage_service.init_client()


async def save_attachment_to_s3(
    tenant_id: str,
    file_name: str,
    file_content: bytes,
) -> str:
    """
    Saves file content to S3 using common_lib.storage.storage_client.StorageService.

    Args:
        tenant_id: Used as the S3 bucket name/prefix.
        file_name: The desired name for the file.
        file_content: The binary content of the file.

    Returns:
        str: The S3 object key (path) of the uploaded file.
    """
    try:
        logger.info(f"Uploading file '{file_name}' to S3 for tenant: {tenant_id}")

        # 1. Define the bucket and object key
        bucket_name = tenant_id
        # Use a simple key structure, e.g., attachments/file_name
        object_key = f"attachments/{file_name}"

        # 2. Use asyncio.to_thread to run the synchronous storage operation
        # This is best practice in async functions (like your save_attachment_to_s3)
        # when calling synchronous blocking I/O methods (like storage.store_object).
        await asyncio.to_thread(
            storage_service.store_object,
            bucket_name=bucket_name,
            object_key=object_key,
            data=file_content,
            # If you need to specify a Content-Type, you can add it here,
            # e.g., content_type="application/pdf"
        )

        logger.info(f"File uploaded successfully. S3 Key: {object_key}")
        return object_key

    except Exception as e:
        logger.error(f"Failed to upload attachment to S3: {e}", exc_info=True)
        # Re-raise the exception to fail the consumer process
        raise
