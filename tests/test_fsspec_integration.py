#!/usr/bin/env python3
"""Integration tests for fsspec filesystem() usage with msgraphfs.

Tests the fsspec.filesystem() integration and URL-based access patterns.
"""

import fsspec
import pytest

from msgraphfs import MSGDriveFS


class TestFSSpecIntegration:
    """Test fsspec.filesystem() integration."""

    def test_fsspec_protocol_registration(self):
        """Test that msgd protocol is registered with fsspec."""
        available = fsspec.available_protocols()
        assert "msgd" in available

    def test_fsspec_filesystem_creation(self):
        """Test creating filesystem through fsspec.filesystem()."""
        fs = fsspec.filesystem(
            "msgd",
            client_id="test_client",
            tenant_id="test_tenant",
            client_secret="test_secret",
        )
        assert isinstance(fs, MSGDriveFS)
        assert fs.client_id == "test_client"
        assert fs.tenant_id == "test_tenant"
        assert fs.client_secret == "test_secret"

    def test_fsspec_filesystem_with_site_and_drive(self):
        """Test creating filesystem with specific site and drive parameters."""
        # When we provide site_name and drive_name, we get MSGDriveFS in single-site mode
        fs = fsspec.filesystem(
            "msgd",
            client_id="test_client",
            tenant_id="test_tenant",
            client_secret="test_secret",
            site_name="TestSite",
            drive_name="Documents",
        )
        assert isinstance(fs, MSGDriveFS)

    def test_msgdrivefs_direct_usage(self):
        """Test MSGDriveFS direct usage for both modes."""
        # With site_name and drive_name, should be in single-site mode
        fs = MSGDriveFS(
            client_id="test_client",
            tenant_id="test_tenant",
            client_secret="test_secret",
            site_name="TestSite",
            drive_name="Documents",
        )
        assert isinstance(fs, MSGDriveFS)
        assert fs.site_name == "TestSite"
        assert fs.drive_name == "Documents"
        assert fs._multi_site_mode is False

        # Without site_name and drive_name, should be in multi-site mode
        fs = MSGDriveFS(
            client_id="test_client",
            tenant_id="test_tenant",
            client_secret="test_secret",
        )
        assert isinstance(fs, MSGDriveFS)
        assert fs._multi_site_mode is True

    def test_fsspec_open_with_url(self):
        """Test opening files using fsspec.open() with msgd URLs."""
        # This should work but we can't test file operations without real credentials
        # We can test that the function call doesn't fail at the filesystem level
        try:
            with fsspec.open(
                "msgd://TestSite/Documents/test.txt",
                mode="r",
                client_id="test_client",
                tenant_id="test_tenant",
                client_secret="test_secret",
            ) as _:
                # This will fail at the authentication level, but that's expected
                pass
        except Exception as e:
            # Expected to fail with authentication/network errors, not filesystem errors
            assert "filesystem" not in str(e).lower()

    def test_fsspec_get_filesystem_class(self):
        """Test getting the filesystem class through fsspec."""
        cls = fsspec.get_filesystem_class("msgd")
        assert cls == MSGDriveFS

    def test_url_to_fs_parsing(self):
        """Test fsspec.url_to_fs() URL parsing."""
        fs, path = fsspec.url_to_fs(
            "msgd://TestSite/Documents/folder/file.txt",
            client_id="test_client",
            tenant_id="test_tenant",
            client_secret="test_secret",
        )
        assert isinstance(fs, MSGDriveFS)
        assert path == "TestSite/Documents/folder/file.txt"


class TestBackwardCompatibility:
    """Test that existing code patterns still work."""

    def test_direct_msgdrivefs_instantiation(self):
        """Test that direct MSGDriveFS instantiation still works."""
        fs = MSGDriveFS(
            client_id="test_client",
            tenant_id="test_tenant",
            client_secret="test_secret",
            site_name="TestSite",
            drive_name="Documents",
        )
        assert fs.site_name == "TestSite"
        assert fs.drive_name == "Documents"

    def test_msgdrivefs_with_oauth_params(self):
        """Test MSGDriveFS with oauth2_client_params (existing pattern)."""
        oauth_params = {
            "client_id": "test_client",
            "client_secret": "test_secret",
            "token_endpoint": "https://login.microsoftonline.com/test_tenant/oauth2/v2.0/token",
            "scope": "https://graph.microsoft.com/.default",
            "grant_type": "client_credentials",
        }
        fs = MSGDriveFS(
            oauth2_client_params=oauth_params,
            site_name="TestSite",
            drive_name="Documents",
        )
        assert fs.site_name == "TestSite"
        assert fs.drive_name == "Documents"

    def test_environment_variable_support(self):
        """Test that environment variable support is maintained."""
        import os

        # Save original values
        original_client_id = os.environ.get("MSGRAPHFS_CLIENT_ID")
        original_tenant_id = os.environ.get("MSGRAPHFS_TENANT_ID")
        original_client_secret = os.environ.get("MSGRAPHFS_CLIENT_SECRET")

        try:
            # Set test environment variables
            os.environ["MSGRAPHFS_CLIENT_ID"] = "env_client_id"
            os.environ["MSGRAPHFS_TENANT_ID"] = "env_tenant_id"
            os.environ["MSGRAPHFS_CLIENT_SECRET"] = "env_client_secret"

            # Test that environment variables are used
            fs = MSGDriveFS(site_name="TestSite", drive_name="Documents")
            assert fs.client_id == "env_client_id"
            assert fs.tenant_id == "env_tenant_id"
            assert fs.client_secret == "env_client_secret"

        finally:
            # Restore original values
            if original_client_id is not None:
                os.environ["MSGRAPHFS_CLIENT_ID"] = original_client_id
            else:
                os.environ.pop("MSGRAPHFS_CLIENT_ID", None)

            if original_tenant_id is not None:
                os.environ["MSGRAPHFS_TENANT_ID"] = original_tenant_id
            else:
                os.environ.pop("MSGRAPHFS_TENANT_ID", None)

            if original_client_secret is not None:
                os.environ["MSGRAPHFS_CLIENT_SECRET"] = original_client_secret
            else:
                os.environ.pop("MSGRAPHFS_CLIENT_SECRET", None)


class TestNewFeatures:
    """Test new URL-based features."""

    def test_url_path_initialization(self):
        """Test URL path initialization in MSGDriveFS."""
        fs = MSGDriveFS(
            client_id="test_client",
            tenant_id="test_tenant",
            client_secret="test_secret",
            url_path="msgd://TestSite/Documents",
        )
        assert fs.site_name == "TestSite"
        assert fs.drive_name == "Documents"

    def test_url_overrides_direct_params(self):
        """Test that URL path overrides direct site_name/drive_name."""
        fs = MSGDriveFS(
            client_id="test_client",
            tenant_id="test_tenant",
            client_secret="test_secret",
            site_name="OldSite",
            drive_name="OldDrive",
            url_path="msgd://TestSite/Documents",
        )
        assert fs.site_name == "TestSite"
        assert fs.drive_name == "Documents"

    def test_msgraphfilesystem_caching(self):
        """Test that MSGDriveFS caches drive filesystem instances in multi-site mode."""
        fs = MSGDriveFS(
            client_id="test_client",
            tenant_id="test_tenant",
            client_secret="test_secret",
        )

        # Get the same drive filesystem twice
        drive_fs1 = fs._get_drive_fs("TestSite", "Documents")
        drive_fs2 = fs._get_drive_fs("TestSite", "Documents")

        # Should be the same instance (cached)
        assert drive_fs1 is drive_fs2

        # Different site/drive should be different instance
        drive_fs3 = fs._get_drive_fs("TestSite", "Lists")
        assert drive_fs1 is not drive_fs3


if __name__ == "__main__":
    pytest.main([__file__])
