#!/usr/bin/env python3
"""Unit tests for URL parsing functionality in msgraphfs.

Tests the parse_msgraph_url and format_msgraph_url functions, as well as
URL-based filesystem initialization.
"""

import pytest

from msgraphfs import MSGDriveFS, parse_msgraph_url


class TestURLParsing:
    """Test URL parsing functions."""

    def test_parse_full_url(self):
        """Test parsing complete URL with site, drive, and path."""
        site, drive, path = parse_msgraph_url(
            "msgd://TestSite/Documents/folder/file.txt"
        )
        assert site == "TestSite"
        assert drive == "Documents"
        assert path == "/folder/file.txt"

    def test_parse_site_and_drive_only(self):
        """Test parsing URL with just site and drive."""
        site, drive, path = parse_msgraph_url("msgd://TestSite/Documents")
        assert site == "TestSite"
        assert drive == "Documents"
        assert path == "/"

    def test_parse_site_only(self):
        """Test parsing URL with just site."""
        site, drive, path = parse_msgraph_url("msgd://TestSite")
        assert site == "TestSite"
        assert drive is None
        assert path == "/"

    def test_parse_empty_url(self):
        """Test parsing empty or None URL."""
        site, drive, path = parse_msgraph_url("")
        assert site is None
        assert drive is None
        assert path == "/"

        site, drive, path = parse_msgraph_url(None)
        assert site is None
        assert drive is None
        assert path == "/"

    def test_parse_path_only(self):
        """Test parsing path without protocol."""
        site, drive, path = parse_msgraph_url("Documents/folder/file.txt")
        assert site is None
        assert drive == "Documents"
        assert path == "/folder/file.txt"

    def test_parse_nested_path(self):
        """Test parsing deeply nested paths."""
        site, drive, path = parse_msgraph_url(
            "msgd://TestSite/Documents/level1/level2/level3/file.txt"
        )
        assert site == "TestSite"
        assert drive == "Documents"
        assert path == "/level1/level2/level3/file.txt"

    def test_parse_url_with_special_characters(self):
        """Test parsing URLs with special characters."""
        site, drive, path = parse_msgraph_url(
            "msgd://Project-Q_Site/Custom%20Library/test%20file.txt"
        )
        assert site == "Project-Q_Site"
        assert drive == "Custom%20Library"
        assert path == "/test%20file.txt"


class TestFilesystemURLInitialization:
    """Test filesystem initialization with URL paths."""

    def test_msgdrivefs_url_initialization(self):
        """Test MSGDriveFS initialization with url_path parameter."""
        # Mock credentials for testing
        fs = MSGDriveFS(
            client_id="test_client",
            tenant_id="test_tenant",
            client_secret="test_secret",
            url_path="msgd://TestSite/Documents",
        )
        assert fs.site_name == "TestSite"
        assert fs.drive_name == "Documents"

    def test_msgdrivefs_url_overrides_params(self):
        """Test that URL path overrides direct parameters."""
        fs = MSGDriveFS(
            client_id="test_client",
            tenant_id="test_tenant",
            client_secret="test_secret",
            site_name="OldSite",
            drive_name="OldDrive",
            url_path="msgd://TestSite/Documents",
        )
        # URL should override the direct parameters
        assert fs.site_name == "TestSite"
        assert fs.drive_name == "Documents"

    def test_msgdrivefs_params_without_url(self):
        """Test that direct parameters work when no URL is provided."""
        fs = MSGDriveFS(
            client_id="test_client",
            tenant_id="test_tenant",
            client_secret="test_secret",
            site_name="TestSite",
            drive_name="Documents",
        )
        assert fs.site_name == "TestSite"
        assert fs.drive_name == "Documents"

    def test_msgdrivefs_multi_site_initialization(self):
        """Test MSGDriveFS initialization in multi-site mode."""
        fs = MSGDriveFS(
            client_id="test_client",
            tenant_id="test_tenant",
            client_secret="test_secret",
        )
        assert fs.client_id == "test_client"
        assert fs.tenant_id == "test_tenant"
        assert fs.client_secret == "test_secret"
        assert fs._multi_site_mode is True

    def test_msgdrivefs_path_parsing(self):
        """Test MSGDriveFS path parsing in multi-site mode."""
        fs = MSGDriveFS(
            client_id="test_client",
            tenant_id="test_tenant",
            client_secret="test_secret",
        )

        site, drive, path = fs._parse_path_for_url_routing(
            "msgd://TestSite/Documents/file.txt"
        )
        assert site == "TestSite"
        assert drive == "Documents"
        assert path == "/file.txt"

    def test_msgdrivefs_path_parsing_errors(self):
        """Test MSGDriveFS path parsing error cases in multi-site mode."""
        fs = MSGDriveFS(
            client_id="test_client",
            tenant_id="test_tenant",
            client_secret="test_secret",
        )

        # Missing site name should raise ValueError
        with pytest.raises(ValueError, match="Path must include site name"):
            fs._parse_path_for_url_routing("/Documents/file.txt")

        # Missing drive name should raise ValueError
        with pytest.raises(ValueError, match="Path must include drive name"):
            fs._parse_path_for_url_routing("msgd://TestSite")


if __name__ == "__main__":
    pytest.main([__file__])
