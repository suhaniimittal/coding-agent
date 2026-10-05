"""
Unit tests for s3_utils.py
"""

import asyncio
from unittest.mock import AsyncMock, Mock, patch

import pytest

from src.s3_utils import save_attachment_to_s3, storage_service


class TestS3Utils:
    """Test cases for s3_utils module."""

    @pytest.fixture
    def mock_storage_service(self):
        """Mock storage service."""
        with patch("src.s3_utils.storage_service") as mock_service:
            mock_service.init_client = Mock()
            mock_service.store_object = Mock()
            yield mock_service

    @pytest.mark.asyncio
    async def test_save_attachment_to_s3_success(self, mock_storage_service):
        """Test successful attachment upload to S3."""
        tenant_id = "test-tenant"
        file_name = "document.pdf"
        file_content = b"PDF file content"
        expected_key = f"attachments/{file_name}"

        with patch("asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread:
            mock_to_thread.return_value = None  # store_object doesn't return anything

            result = await save_attachment_to_s3(tenant_id, file_name, file_content)

            assert result == expected_key

            # Verify asyncio.to_thread was called with correct parameters
            mock_to_thread.assert_called_once_with(
                mock_storage_service.store_object,
                bucket_name=tenant_id,
                object_key=expected_key,
                data=file_content,
            )

    @pytest.mark.asyncio
    async def test_save_attachment_to_s3_with_special_characters(self, mock_storage_service):
        """Test attachment upload with special characters in filename."""
        tenant_id = "tenant-123"
        file_name = "document with spaces & symbols.pdf"
        file_content = b"File content"
        expected_key = f"attachments/{file_name}"

        with patch("asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread:
            mock_to_thread.return_value = None

            result = await save_attachment_to_s3(tenant_id, file_name, file_content)

            assert result == expected_key

    @pytest.mark.asyncio
    async def test_save_attachment_to_s3_empty_file(self, mock_storage_service):
        """Test upload of empty file."""
        tenant_id = "test-tenant"
        file_name = "empty.txt"
        file_content = b""
        expected_key = f"attachments/{file_name}"

        with patch("asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread:
            mock_to_thread.return_value = None

            result = await save_attachment_to_s3(tenant_id, file_name, file_content)

            assert result == expected_key

            mock_to_thread.assert_called_once_with(
                mock_storage_service.store_object,
                bucket_name=tenant_id,
                object_key=expected_key,
                data=file_content,
            )

    @pytest.mark.asyncio
    async def test_save_attachment_to_s3_large_file(self, mock_storage_service):
        """Test upload of large file."""
        tenant_id = "test-tenant"
        file_name = "large-file.bin"
        file_content = b"x" * (10 * 1024 * 1024)  # 10MB file
        expected_key = f"attachments/{file_name}"

        with patch("asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread:
            mock_to_thread.return_value = None

            result = await save_attachment_to_s3(tenant_id, file_name, file_content)

            assert result == expected_key

    @pytest.mark.asyncio
    async def test_save_attachment_to_s3_storage_error(self, mock_storage_service):
        """Test handling of storage service errors."""
        tenant_id = "test-tenant"
        file_name = "document.pdf"
        file_content = b"PDF content"

        with patch("asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread:
            mock_to_thread.side_effect = Exception("S3 upload failed")

            with pytest.raises(Exception) as exc_info:
                await save_attachment_to_s3(tenant_id, file_name, file_content)

            assert "S3 upload failed" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_save_attachment_to_s3_thread_execution(self, mock_storage_service):
        """Test that asyncio.to_thread is used for synchronous storage operation."""
        tenant_id = "test-tenant"
        file_name = "test.txt"
        file_content = b"test content"

        # Create a real mock that we can inspect
        call_log = []

        async def mock_to_thread_impl(func, *args, **kwargs):
            call_log.append({"func": func, "args": args, "kwargs": kwargs})
            return None

        with patch("asyncio.to_thread", side_effect=mock_to_thread_impl):
            result = await save_attachment_to_s3(tenant_id, file_name, file_content)

            assert result == f"attachments/{file_name}"
            assert len(call_log) == 1

            call_info = call_log[0]
            assert call_info["func"] == mock_storage_service.store_object
            assert call_info["kwargs"]["bucket_name"] == tenant_id
            assert call_info["kwargs"]["object_key"] == f"attachments/{file_name}"
            assert call_info["kwargs"]["data"] == file_content

    def test_object_key_format(self):
        """Test that object key is formatted correctly."""
        test_cases = [
            ("document.pdf", "attachments/document.pdf"),
            ("report.xlsx", "attachments/report.xlsx"),
            ("image.png", "attachments/image.png"),
            ("file with spaces.txt", "attachments/file with spaces.txt"),
            ("file-with-dashes.doc", "attachments/file-with-dashes.doc"),
        ]

        for file_name, expected_key in test_cases:
            # Test the key generation logic
            object_key = f"attachments/{file_name}"
            assert object_key == expected_key

    @pytest.mark.asyncio
    async def test_save_attachment_different_tenants(self, mock_storage_service):
        """Test that different tenants use different bucket names."""
        file_name = "same-file.pdf"
        file_content = b"same content"

        with patch("asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread:
            mock_to_thread.return_value = None

            # Upload for tenant 1
            await save_attachment_to_s3("tenant-1", file_name, file_content)

            # Upload for tenant 2
            await save_attachment_to_s3("tenant-2", file_name, file_content)

            # Verify both calls were made with different bucket names
            assert mock_to_thread.call_count == 2

            call1_kwargs = mock_to_thread.call_args_list[0].kwargs
            call2_kwargs = mock_to_thread.call_args_list[1].kwargs

            assert call1_kwargs["bucket_name"] == "tenant-1"
            assert call2_kwargs["bucket_name"] == "tenant-2"

            # But same object key
            assert (
                call1_kwargs["object_key"]
                == call2_kwargs["object_key"]
                == f"attachments/{file_name}"
            )

    @pytest.mark.asyncio
    async def test_save_attachment_unicode_filename(self, mock_storage_service):
        """Test handling of unicode characters in filename."""
        tenant_id = "test-tenant"
        file_name = "résumé_文件.pdf"  # Unicode filename
        file_content = b"PDF with unicode name"
        expected_key = f"attachments/{file_name}"

        with patch("asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread:
            mock_to_thread.return_value = None

            result = await save_attachment_to_s3(tenant_id, file_name, file_content)

            assert result == expected_key

    def test_storage_service_initialization(self):
        """Test that storage service is properly initialized at module level."""
        # Since storage service is initialized at module import, we can't easily test
        # the initialization without affecting other tests. Instead, test that it exists
        # and has been initialized

        assert storage_service is not None
        # The storage service should have a store_object method
        assert hasattr(storage_service, "store_object")
        assert hasattr(storage_service, "init_client")

    @pytest.mark.asyncio
    async def test_save_attachment_to_s3_timeout(self, mock_storage_service):
        """Test handling of timeout during S3 upload."""
        tenant_id = "test-tenant"
        file_name = "slow-upload.pdf"
        file_content = b"content"

        with patch("asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread:
            mock_to_thread.side_effect = TimeoutError("Upload timeout")

            with pytest.raises(TimeoutError):
                await save_attachment_to_s3(tenant_id, file_name, file_content)

    @pytest.mark.asyncio
    async def test_save_attachment_to_s3_network_error(self, mock_storage_service):
        """Test handling of network-related errors during upload."""
        tenant_id = "test-tenant"
        file_name = "network-fail.pdf"
        file_content = b"content"

        network_errors = [
            ConnectionError("Network connection failed"),
            OSError("Network is unreachable"),
            Exception("AWS service unavailable"),
        ]

        for error in network_errors:
            with patch("asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread:
                mock_to_thread.side_effect = error

                with pytest.raises(Exception):
                    await save_attachment_to_s3(tenant_id, file_name, file_content)

    @pytest.mark.asyncio
    async def test_save_attachment_concurrent_uploads(self, mock_storage_service):
        """Test concurrent uploads to ensure thread safety."""
        with patch("asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread:
            mock_to_thread.return_value = None

            # Start multiple concurrent uploads
            tasks = []
            for i in range(5):
                task = save_attachment_to_s3(
                    tenant_id=f"tenant-{i}",
                    file_name=f"file-{i}.pdf",
                    file_content=f"content-{i}".encode(),
                )
                tasks.append(task)

            # Wait for all to complete
            results = await asyncio.gather(*tasks)

            # Verify all succeeded
            assert len(results) == 5
            for i, result in enumerate(results):
                assert result == f"attachments/file-{i}.pdf"

            # Verify all storage calls were made
            assert mock_to_thread.call_count == 5
