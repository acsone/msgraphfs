#!/usr/bin/env python3
"""Live tests for URL-based features using real SharePoint credentials.

These tests require valid SharePoint credentials to run successfully.
Run with: pytest -m live
Skip with: pytest -m "not live"
"""

# Test site and drive names (credentials should be provided via environment variables)
import os

import fsspec
import pytest

from msgraphfs import MSGDriveFS

TEST_SITE_NAME = os.getenv("MSGRAPHFS_TEST_SITE_NAME", "TestSite")
TEST_DRIVE_NAME = os.getenv("MSGRAPHFS_TEST_DRIVE_NAME", "Documents")


class TestLiveURLFeatures:
    """Live tests for URL-based features."""

    @pytest.mark.live
    def test_fsspec_filesystem_with_urls(self):
        """Test using fsspec.filesystem() with URL-based paths."""
        import os

        # Skip if no credentials available
        client_id = os.getenv("MSGRAPHFS_CLIENT_ID")
        tenant_id = os.getenv("MSGRAPHFS_TENANT_ID")
        client_secret = os.getenv("MSGRAPHFS_CLIENT_SECRET")

        if not all([client_id, tenant_id, client_secret]):
            pytest.skip("Live credentials not available")

        # Create filesystem using fsspec
        fs = fsspec.filesystem(
            "msgd",
            client_id=client_id,
            tenant_id=tenant_id,
            client_secret=client_secret,
        )

        # Test listing files using URL path
        files = fs.ls(f"msgd://{TEST_SITE_NAME}/{TEST_DRIVE_NAME}")
        assert isinstance(files, list)

    @pytest.mark.live
    def test_url_based_file_info(self):
        """Test getting file info using URL paths."""
        import os

        # Skip if no credentials available
        client_id = os.getenv("MSGRAPHFS_CLIENT_ID")
        tenant_id = os.getenv("MSGRAPHFS_TENANT_ID")
        client_secret = os.getenv("MSGRAPHFS_CLIENT_SECRET")

        if not all([client_id, tenant_id, client_secret]):
            pytest.skip("Live credentials not available")

        fs = fsspec.filesystem(
            "msgd",
            client_id=client_id,
            tenant_id=tenant_id,
            client_secret=client_secret,
        )

        # First get a list of files
        files = fs.ls(f"msgd://{TEST_SITE_NAME}/{TEST_DRIVE_NAME}", detail=True)
        if files:
            # Get info for the first file using URL path
            first_file = files[0]
            file_name = first_file["name"].split("/")[-1]
            file_url = f"msgd://{TEST_SITE_NAME}/{TEST_DRIVE_NAME}/{file_name}"

            info = fs.info(file_url)
            assert "name" in info
            assert "type" in info

    @pytest.mark.live
    def test_msgdrivefs_url_initialization(self):
        """Test MSGDriveFS initialization with URL path."""
        import os

        # Skip if no credentials available
        client_id = os.getenv("MSGRAPHFS_CLIENT_ID")
        tenant_id = os.getenv("MSGRAPHFS_TENANT_ID")
        client_secret = os.getenv("MSGRAPHFS_CLIENT_SECRET")

        if not all([client_id, tenant_id, client_secret]):
            pytest.skip("Live credentials not available")

        # Initialize using url_path parameter
        fs = MSGDriveFS(
            client_id=client_id,
            tenant_id=tenant_id,
            client_secret=client_secret,
            url_path=f"msgd://{TEST_SITE_NAME}/{TEST_DRIVE_NAME}",
        )

        files = fs.ls("/")
        assert isinstance(files, list)

    @pytest.mark.live
    def test_factory_function_with_credentials(self):
        """Test the factory function with real credentials."""
        import os

        # Skip if no credentials available
        client_id = os.getenv("MSGRAPHFS_CLIENT_ID")
        tenant_id = os.getenv("MSGRAPHFS_TENANT_ID")
        client_secret = os.getenv("MSGRAPHFS_CLIENT_SECRET")

        if not all([client_id, tenant_id, client_secret]):
            pytest.skip("Live credentials not available")

        # Test MSGDriveFS in single-site mode for specific site/drive
        fs = MSGDriveFS(
            client_id=client_id,
            tenant_id=tenant_id,
            client_secret=client_secret,
            site_name=TEST_SITE_NAME,
            drive_name=TEST_DRIVE_NAME,
        )

        assert isinstance(fs, MSGDriveFS)
        assert fs._multi_site_mode is False
        assert fs.site_name == TEST_SITE_NAME
        assert fs.drive_name == TEST_DRIVE_NAME

        files = fs.ls("/")
        assert isinstance(files, list)

        # Test MSGDriveFS in multi-site mode for multi-site access
        fs_multi = MSGDriveFS(
            client_id=client_id, tenant_id=tenant_id, client_secret=client_secret
        )

        assert isinstance(fs_multi, MSGDriveFS)
        assert fs_multi._multi_site_mode is True

        files = fs_multi.ls(f"msgd://{TEST_SITE_NAME}/{TEST_DRIVE_NAME}")
        assert isinstance(files, list)

    @pytest.mark.live
    def test_fsspec_open_with_url(self):
        """Test opening files using fsspec.open() with URL paths."""
        import os

        # Skip if no credentials available
        client_id = os.getenv("MSGRAPHFS_CLIENT_ID")
        tenant_id = os.getenv("MSGRAPHFS_TENANT_ID")
        client_secret = os.getenv("MSGRAPHFS_CLIENT_SECRET")

        if not all([client_id, tenant_id, client_secret]):
            pytest.skip("Live credentials not available")

        fs = fsspec.filesystem(
            "msgd",
            client_id=client_id,
            tenant_id=tenant_id,
            client_secret=client_secret,
        )

        # Get a list of files
        files = fs.ls(f"msgd://{TEST_SITE_NAME}/{TEST_DRIVE_NAME}", detail=True)
        text_files = [f for f in files if f.get("name", "").endswith(".txt")]

        if text_files:
            file_name = text_files[0]["name"].split("/")[-1]
            file_url = f"msgd://{TEST_SITE_NAME}/{TEST_DRIVE_NAME}/{file_name}"

            # Try to open and read the file
            with fsspec.open(
                file_url,
                mode="rb",
                client_id=client_id,
                tenant_id=tenant_id,
                client_secret=client_secret,
            ) as f:
                content = f.read(100)  # Read first 100 bytes
                assert isinstance(content, bytes)

    @pytest.mark.live
    def test_backward_compatibility_with_live_data(self):
        """Test that existing code patterns still work with real data."""
        import os

        # Skip if no credentials available
        client_id = os.getenv("MSGRAPHFS_CLIENT_ID")
        tenant_id = os.getenv("MSGRAPHFS_TENANT_ID")
        client_secret = os.getenv("MSGRAPHFS_CLIENT_SECRET")

        if not all([client_id, tenant_id, client_secret]):
            pytest.skip("Live credentials not available")

        # Test original MSGDriveFS pattern
        fs_original = MSGDriveFS(
            client_id=client_id,
            tenant_id=tenant_id,
            client_secret=client_secret,
            site_name=TEST_SITE_NAME,
            drive_name=TEST_DRIVE_NAME,
        )

        # Test new URL pattern
        fs_url = MSGDriveFS(
            client_id=client_id,
            tenant_id=tenant_id,
            client_secret=client_secret,
            url_path=f"msgd://{TEST_SITE_NAME}/{TEST_DRIVE_NAME}",
        )

        files_original = fs_original.ls("/")
        files_url = fs_url.ls("/")

        # Both should return the same data
        assert len(files_original) == len(files_url)

    @pytest.mark.live
    def test_url_path_overrides(self):
        """Test that URL path overrides direct parameters."""
        import os

        # Skip if no credentials available
        client_id = os.getenv("MSGRAPHFS_CLIENT_ID")
        tenant_id = os.getenv("MSGRAPHFS_TENANT_ID")
        client_secret = os.getenv("MSGRAPHFS_CLIENT_SECRET")

        if not all([client_id, tenant_id, client_secret]):
            pytest.skip("Live credentials not available")

        # Create filesystem with conflicting parameters
        fs = MSGDriveFS(
            client_id=client_id,
            tenant_id=tenant_id,
            client_secret=client_secret,
            site_name="WrongSite",
            drive_name="WrongDrive",
            url_path=f"msgd://{TEST_SITE_NAME}/{TEST_DRIVE_NAME}",
        )

        # URL should override the wrong parameters
        assert fs.site_name == TEST_SITE_NAME
        assert fs.drive_name == TEST_DRIVE_NAME

        files = fs.ls("/")
        assert isinstance(files, list)


@pytest.mark.live
class TestLivePerformanceAndCaching:
    """Test performance and caching with live data."""

    def test_msgdrivefs_caching_performance(self):
        """Test that MSGDriveFS caching improves performance in multi-site mode."""
        import os

        # Skip if no credentials available
        client_id = os.getenv("MSGRAPHFS_CLIENT_ID")
        tenant_id = os.getenv("MSGRAPHFS_TENANT_ID")
        client_secret = os.getenv("MSGRAPHFS_CLIENT_SECRET")

        if not all([client_id, tenant_id, client_secret]):
            pytest.skip("Live credentials not available")

        fs = MSGDriveFS(
            client_id=client_id, tenant_id=tenant_id, client_secret=client_secret
        )

        import time

        # First access - should create the drive filesystem
        start_time = time.time()
        drive_fs1 = fs._get_drive_fs(TEST_SITE_NAME, TEST_DRIVE_NAME)
        first_access_time = time.time() - start_time

        # Second access - should use cached instance
        start_time = time.time()
        drive_fs2 = fs._get_drive_fs(TEST_SITE_NAME, TEST_DRIVE_NAME)
        second_access_time = time.time() - start_time

        # Should be the same instance and second access should be faster
        assert drive_fs1 is drive_fs2
        assert second_access_time < first_access_time

    def test_multiple_site_access(self):
        """Test accessing multiple sites through MSGDriveFS in multi-site mode."""
        import os

        # Skip if no credentials available
        client_id = os.getenv("MSGRAPHFS_CLIENT_ID")
        tenant_id = os.getenv("MSGRAPHFS_TENANT_ID")
        client_secret = os.getenv("MSGRAPHFS_CLIENT_SECRET")

        if not all([client_id, tenant_id, client_secret]):
            pytest.skip("Live credentials not available")

        fs = MSGDriveFS(
            client_id=client_id, tenant_id=tenant_id, client_secret=client_secret
        )

        # Access the test site
        fs.ls(f"msgd://{TEST_SITE_NAME}/{TEST_DRIVE_NAME}")

        # Could test additional sites if available
        # For now, just verify the functionality exists
        assert hasattr(fs, "_drive_cache")
        assert len(fs._drive_cache) >= 1
