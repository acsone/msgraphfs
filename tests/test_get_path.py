#!/usr/bin/env python3
"""Unit tests for AbstractMSGraphFS._get_path().

Regression test for the leading-slash mismatch between _get_path() and
fsspec's DirFileSystem (dirfs): dirfs normalizes its root prefix via
_strip_protocol(), which leaves a relative directory_path (e.g. "Odoo")
without a leading slash, while _get_path() used to always return an
absolute path (e.g. "/Odoo/file.txt"). dirfs._relpath() then asserts
path.startswith(prefix), which failed for every info() result once a
non-empty directory_path (dirfs) was in use.
"""

import pytest

from msgraphfs import MSGDriveFS


@pytest.fixture
def fs():
    return MSGDriveFS(
        client_id="test_client",
        tenant_id="test_tenant",
        client_secret="test_secret",
        site_name="TestSite",
        drive_name="Documents",
    )


class TestGetPath:
    """Test AbstractMSGraphFS._get_path()."""

    def test_nested_item_has_no_leading_slash(self, fs):
        drive_item_info = {
            "name": "file.txt",
            "parentReference": {"path": "/drives/abc123/root:/Odoo"},
        }
        assert fs._get_path(drive_item_info) == "Odoo/file.txt"

    def test_top_level_item_has_no_leading_slash(self, fs):
        drive_item_info = {
            "name": "file.txt",
            "parentReference": {"path": "/drives/abc123/root:"},
        }
        assert fs._get_path(drive_item_info) == "file.txt"

    def test_deeply_nested_item_has_no_leading_slash(self, fs):
        drive_item_info = {
            "name": "file.txt",
            "parentReference": {"path": "/drives/abc123/root:/Odoo/Sub/Folder"},
        }
        assert fs._get_path(drive_item_info) == "Odoo/Sub/Folder/file.txt"

    def test_matches_dirfs_relative_prefix(self, fs):
        """dirfs.path (via _strip_protocol) has no leading slash for a relative
        directory_path, so info() results must not either."""
        directory_path = fs._strip_protocol("Odoo")
        drive_item_info = {
            "name": "marker",
            "parentReference": {"path": "/drives/abc123/root:/Odoo"},
        }
        path = fs._get_path(drive_item_info)
        assert path.startswith(directory_path + "/")


if __name__ == "__main__":
    pytest.main([__file__])
