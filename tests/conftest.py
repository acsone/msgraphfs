import json
import os
import time
import uuid
import warnings
from contextlib import asynccontextmanager, contextmanager
from functools import partial

import fsspec
import keyring
import pytest
import pytest_asyncio
import requests
from fsspec.implementations.dirfs import DirFileSystem

from msgraphfs import MSGDriveFS

# Test data fixtures are defined below instead of importing from content.py

LOGIN_URL = "https://login.microsoftonline.com"
SCOPES = ["offline_access", "openid", "Files.ReadWrite.All", "Sites.ReadWrite.All"]


# Test data fixtures
@pytest.fixture(scope="session")
def test_files():
    """Test file data for JSON files."""
    return {
        "test/accounts.1.json": (
            b'{"amount": 100, "name": "Alice"}\n'
            b'{"amount": 200, "name": "Bob"}\n'
            b'{"amount": 300, "name": "Charlie"}\n'
            b'{"amount": 400, "name": "Dennis"}\n'
        ),
        "test/accounts.2.json": (
            b'{"amount": 500, "name": "Alice"}\n'
            b'{"amount": 600, "name": "Bob"}\n'
            b'{"amount": 700, "name": "Charlie"}\n'
            b'{"amount": 800, "name": "Dennis"}\n'
        ),
    }


@pytest.fixture(scope="session")
def test_csv_files():
    """Test file data for CSV files."""
    return {
        "csv/2014-01-01.csv": (
            b"name,amount,id\nAlice,100,1\nBob,200,2\nCharlie,300,3\n"
        ),
        "csv/2014-01-02.csv": (b"name,amount,id\n"),
        "csv/2014-01-03.csv": (
            b"name,amount,id\nDennis,400,4\nEdith,500,5\nFrank,600,6\n"
        ),
    }


@pytest.fixture(scope="session")
def test_text_files():
    """Test file data for text files."""
    return {
        "nested/file1": b"hello\n",
        "nested/file2": b"world",
        "nested/nested2/file1": b"hello\n",
        "nested/nested2/file2": b"world",
    }


@pytest.fixture(scope="session")
def test_glob_files():
    """Test file data for glob pattern tests."""
    return {"file.dat": b"", "filexdat": b""}


@pytest.fixture(scope="session")
def all_test_data(test_files, test_csv_files, test_text_files, test_glob_files):
    """Combined test data for convenience."""
    return {
        "files": test_files,
        "csv_files": test_csv_files,
        "text_files": test_text_files,
        "glob_files": test_glob_files,
    }


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--client-id", action="store", default=None, help="SharePoint client ID"
    )
    parser.addoption(
        "--client-secret", action="store", default=None, help="SharePoint client secret"
    )
    parser.addoption(
        "--site-name", action="store", default=None, help="SharePoint site name"
    )
    parser.addoption(
        "--drive-id", action="store", default=None, help="SharePoint drive ID"
    )
    parser.addoption(
        "--tenant-id", action="store", default=None, help="SharePoint drive ID"
    )
    parser.addoption(
        "--auth-code",
        action="store",
        default=None,
        help="Authorization code for SharePoint authentication",
    )
    parser.addoption(
        "--auth-redirect-uri",
        action="store",
        default="http://localhost:8069",
        help="The redirect url to use to get retrieve the auth code from Microsoft Graph API",
    )


def _get_tokens_for_auth_code(
    client_id: str,
    client_secret: str,
    auth_code: str,
    tenant_id: str,
    auth_redirect_uri: str,
) -> dict:
    token_url = f"{LOGIN_URL}/{tenant_id}/oauth2/v2.0/token"
    data = {
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "authorization_code",
        "code": auth_code,
        "redirect_uri": auth_redirect_uri,
        "scope": " ".join(SCOPES),
    }

    response = requests.post(token_url, data=data)
    if response.status_code != 200:
        raise pytest.fail(f"Failed to get token: {response.text}")
    tokens = response.json()
    return tokens


def _store_tokens_in_keyring(
    client_id: str,
    tenant_id: str,
    tokens: dict,
) -> None:
    keyring_service = f"msgraph-token-{tenant_id}"
    keyring_user = client_id
    token_data = {
        "access_token": tokens.get("access_token"),
        "refresh_token": tokens.get("refresh_token"),
        "expires_at": int(time.time()) + int(tokens.get("expires_in", 0)),
    }
    keyring.set_password(keyring_service, keyring_user, json.dumps(token_data))


def _refresh_tokens(
    client_id: str,
    client_secret: str,
    refresh_token: str,
    tenant_id: str,
) -> dict:
    token_url = f"{LOGIN_URL}/{tenant_id}/oauth2/v2.0/token"
    data = {
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "scope": " ".join(SCOPES),
    }
    response = requests.post(token_url, data=data)
    if response.status_code != 200:
        warnings.warn(f"Failed to refresh token: {response.text}", stacklevel=2)
        return {}
    tokens = response.json()
    _store_tokens_in_keyring(client_id, tenant_id, tokens)
    return _load_tokens_from_keyring(
        client_id=client_id,
        tenant_id=tenant_id,
    )


def _load_tokens_from_keyring(
    client_id: str,
    tenant_id: str,
) -> dict:
    keyring_service = f"msgraph-token-{tenant_id}"
    keyring_user = client_id
    token_data = keyring.get_password(keyring_service, keyring_user)
    if token_data:
        return json.loads(token_data)
    return {}


def _get_and_check_tokens(
    client_id: str,
    client_secret: str,
    tenant_id: str,
) -> dict:
    tokens = _load_tokens_from_keyring(client_id, tenant_id)
    if not tokens:
        return {}
    if "access_token" not in tokens or "refresh_token" not in tokens:
        warnings.warn("Tokens are missing access_token or refresh_token.", stacklevel=2)
        return {}
    if "expires_at" not in tokens or tokens["expires_at"] < time.time():
        # Try to refresh the tokens
        warnings.warn("Tokens are expired or missing expires_at.", stacklevel=2)
        tokens = _refresh_tokens(
            client_id=client_id,
            client_secret=client_secret,
            refresh_token=tokens.get("refresh_token"),
            tenant_id=tenant_id,
        )
    return tokens


def _create_fs(request, fs_type, asynchronous=False) -> fsspec.AbstractFileSystem:
    if fs_type == "msgdrive":
        client_id = request.config.getoption("--client-id") or os.getenv(
            "MSGRAPHFS_CLIENT_ID"
        )
        client_secret = request.config.getoption("--client-secret") or os.getenv(
            "MSGRAPHFS_CLIENT_SECRET"
        )
        site_name = request.config.getoption("--site-name") or os.getenv(
            "MSGRAPHFS_SITE_NAME"
        )
        drive_id = request.config.getoption("--drive-id") or os.getenv(
            "MSGRAPHFS_DRIVE_ID"
        )
        tenant_id = request.config.getoption("--tenant-id") or os.getenv(
            "MSGRAPHFS_TENANT_ID"
        )

        if (
            not client_id
            or not client_secret
            or not site_name
            or not drive_id
            or not tenant_id
        ):
            pytest.skip(
                "Skipping test - missing credentials. Please provide: --client-id, --client-secret, "
                "--site-name, --drive-id, --tenant-id or their environment variables."
            )
        auth_code = request.config.getoption("--auth-code") or os.getenv(
            "MSGRAPHFS_AUTH_CODE"
        )
        auth_redirect_uri = request.config.getoption(
            "--auth-redirect-uri"
        ) or os.getenv(
            "MSGRAPHFS_AUTH_REDIRECT_URI",
            "http://localhost:8069/microsoft_account/authentication",
        )
        tokens = _get_and_check_tokens(
            client_id=client_id,
            client_secret=client_secret,
            tenant_id=tenant_id,
        )
        if not tokens and not auth_code:
            raise Exception(
                "No valid tokens found in keyring. Please provide an auth code"
            )
        if not tokens:
            tokens = _get_tokens_for_auth_code(
                client_id=client_id,
                client_secret=client_secret,
                auth_code=auth_code,
                tenant_id=tenant_id,
                auth_redirect_uri=auth_redirect_uri,
            )
            _store_tokens_in_keyring(
                client_id=client_id,
                tenant_id=tenant_id,
                tokens=tokens,
            )
            # Reload tokens from keyring after storing
            # Since some attributes like expires_at are added
            # after the initial request
            tokens = _load_tokens_from_keyring(
                client_id=client_id,
                tenant_id=tenant_id,
            )

        oauth2_client_params = {
            "scope": " ".join(SCOPES),
            "client_id": client_id,
            "client_secret": client_secret,
            "token": tokens,
            "token_endpoint": f"{LOGIN_URL}/{tenant_id}/oauth2/v2.0/token",
            "timeout": 10.0,
        }

        def refresh_tokens_response(resp, initial_token):
            tokens = resp.json()
            _store_tokens_in_keyring(
                client_id=client_id,
                tenant_id=tenant_id,
                tokens=tokens,
            )
            initial_token.update(
                _load_tokens_from_keyring(
                    client_id=client_id,
                    tenant_id=tenant_id,
                )
            )
            return resp

        # Initialize SharePointFS with the configuration
        kwargs = {
            "site_name": site_name,
            "drive_id": drive_id,
            "oauth2_client_params": oauth2_client_params,
            "asynchronous": asynchronous,
        }
        if asynchronous:
            kwargs["cache_type"] = "none"
        MSGDriveFS.clear_instance_cache()
        sp_fs = MSGDriveFS(**kwargs)

        sp_fs.client.register_compliance_hook(
            "refresh_token_response",
            partial(refresh_tokens_response, initial_token=tokens),
        )

        # Yield the initialized object
        return sp_fs


FS_TYPES = ["msgdrive"]


@pytest.fixture(scope="module", params=FS_TYPES)
def fs(request):
    # we use a fixture to be able to lanch the tests suite with different
    # filesystems supported by the microsoft graph api
    yield _create_fs(request, request.param, asynchronous=False)


@pytest.fixture(scope="module", params=FS_TYPES)
def afs(request):
    # we use a fixture to be able to lanch the tests suite with different
    # filesystems supported by the microsoft graph api
    yield _create_fs(request, request.param, asynchronous=True)


class MsGraphTempFS(DirFileSystem):
    def _relpath(self, path):
        path = super()._relpath(path)
        # we override the DirFileSystem method since all
        # paths returned are relative to the root. We want
        # to return path as if they are absolute
        if isinstance(path, str):
            if not path.startswith("/"):
                path = "/" + path
        return path

    def _join(self, path):
        # we override the DirFileSystem method since all the path are absolute
        if isinstance(path, str):
            if path.startswith("/"):
                path = path[1:]
        return super()._join(path)

    # add missing mapper to async methods
    async def _rmdir(self, path):
        return await self.fs._rmdir(self._join(path))

    async def _touch(self, path, **kwargs):
        return await self.fs._touch(self._join(path), **kwargs)

    async def _move(self, path1, path2, **kwargs):
        return await self.fs._mv(self._join(path1), self._join(path2), **kwargs)

    async def _open_async(self, path, mode, **kwargs):
        return await self.fs.open_async(self._join(path), mode, **kwargs)

    async def _modified(self, path):
        return await self.fs._modified(self._join(path))

    async def _created(self, path):
        return await self.fs._created(self._join(path))

    async def _checkout(self, path, item_id=None):
        return await self.fs._checkout(self._join(path), item_id)

    def checkout(self, path, item_id=None):
        return self.fs.checkout(self._join(path), item_id)

    async def _checkin(self, path, comment=None, item_id=None):
        return await self.fs._checkin(self._join(path), comment, item_id)

    def checkin(self, path, comment=None, item_id=None):
        return self.fs.checkin(self._join(path), comment, item_id)

    async def _get_versions(self, path):
        return await self.fs._get_versions(self._join(path))

    def get_versions(self, path):
        return self.fs.get_versions(self._join(path))

    async def _set_properties(self, path, properties, item_id=None):
        return await self.fs._set_properties(self._join(path), properties, item_id)

    def set_properties(self, path, properties, item_id=None):
        return self.fs.set_properties(self._join(path), properties, item_id)


@contextmanager
def _temp_dir(storagefs):
    # create a temporary directory
    temp_dir_name = f"/{str(uuid.uuid4())}"
    storagefs.mkdir(temp_dir_name)
    try:
        yield temp_dir_name
    finally:
        # cleanup
        storagefs.rm(temp_dir_name, recursive=True)


@asynccontextmanager
async def _a_temp_dir(storagefs):
    # create a temporary directory in async fs
    temp_dir_name = f"/{str(uuid.uuid4())}"
    await storagefs._mkdir(temp_dir_name)
    try:
        yield temp_dir_name
    finally:
        # cleanup
        await storagefs._rm(temp_dir_name, recursive=True)


@pytest.fixture(scope="module")
def sample_fs(fs, all_test_data):
    """A temporary filesystem with sample files and directories created from test data
    fixtures.

    We use the fsspec dir filesystem to interact with the filesystem to
    test so we can use a temporary directory into the tested filesystem
    as root to avoid polluting the real filesystem and ensure isolation
    between tests.
    """
    with _temp_dir(fs) as temp_dir_name:
        sfs = MsGraphTempFS(path=temp_dir_name, fs=fs)
        for flist in [
            all_test_data["files"],
            all_test_data["csv_files"],
            all_test_data["text_files"],
            all_test_data["glob_files"],
        ]:
            for path, data in flist.items():
                root, _filename = os.path.split(path)
                if root:
                    sfs.makedirs(root, exist_ok=True)
                with sfs.open(path, "wb") as f:
                    f.write(data)
        sfs.makedirs("/emptydir")
        yield sfs


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def sample_afs(afs, all_test_data):
    """A temporary async filesystem with sample files and directories created from test
    data fixtures.

    We use the fsspec dir filesystem to interact with the filesystem to
    test so we can use a temporary directory into the tested filesystem
    as root to avoid polluting the real filesystem and ensure isolation
    between tests.
    """
    async with _a_temp_dir(afs) as temp_dir_name:
        sfs = MsGraphTempFS(path=temp_dir_name, asynchronous=True, fs=afs)
        for flist in [
            all_test_data["files"],
            all_test_data["csv_files"],
            all_test_data["text_files"],
            all_test_data["glob_files"],
        ]:
            for path, data in flist.items():
                root, _filename = os.path.split(path)
                if root:
                    await sfs._makedirs(root, exist_ok=True)
                async with await sfs._open_async(path, "wb") as stream_file:
                    await stream_file.write(data)
        await sfs._makedirs("/emptydir")
        yield sfs


@pytest.fixture(scope="function")
def temp_fs(fs):
    """A temporary empty filesystem.

    We use the fsspec dir filesystem to interact with the filesystem to
    test so we can use a temporary directory into the tested filesystem
    as root to avoid polluting the real filesystem and ensure isolation
    between tests.
    """
    with _temp_dir(fs) as temp_dir_name:
        yield MsGraphTempFS(path=temp_dir_name, fs=fs)


@pytest_asyncio.fixture(scope="function", params=FS_TYPES, loop_scope="function")
async def function_afs(request):
    # we use a fixture to be able to lanch the tests suite with different
    # filesystems supported by the microsoft graph api
    yield _create_fs(request, request.param, asynchronous=True)


@pytest_asyncio.fixture(scope="function", loop_scope="function")
async def temp_afs(function_afs):
    """A temporary empty async filesystem.

    We use the fsspec dir filesystem to interact with the filesystem to
    test so we can use a temporary directory into the tested filesystem
    as root to avoid polluting the real filesystem and ensure isolation
    between tests.
    """
    afs = function_afs
    async with _a_temp_dir(afs) as temp_dir_name:
        yield MsGraphTempFS(path=temp_dir_name, asynchronous=True, fs=afs)


@pytest.fixture(scope="function")
def temp_nested_fs(fs, test_text_files):
    """A temporary empty filesystem with nested directories.

    We use the fsspec dir filesystem to interact with the filesystem to
    test so we can use a temporary directory into the tested filesystem
    as root to avoid polluting the real filesystem and ensure isolation
    between tests.
    """
    with _temp_dir(fs) as temp_dir_name:
        sfs = MsGraphTempFS(path=temp_dir_name, fs=fs)
        for path, data in test_text_files.items():
            root, _filename = os.path.split(path)
            if root:
                sfs.makedirs(root, exist_ok=True)
            with sfs.open(path, "wb") as f:
                f.write(data)
            sfs.touch("/emptyfile")
        yield sfs


@pytest_asyncio.fixture(scope="function", loop_scope="function")
async def temp_nested_afs(function_afs, test_text_files):
    """A temporary empty async filesystem with nested directories.

    We use the fsspec dir filesystem to interact with the filesystem to
    test so we can use a temporary directory into the tested filesystem
    as root to avoid polluting the real filesystem and ensure isolation
    between tests.
    """
    afs = function_afs
    # is created within an other loop scope
    async with _a_temp_dir(afs) as temp_dir_name:
        sfs = MsGraphTempFS(path=temp_dir_name, asynchronous=True, fs=afs)
        for path, data in test_text_files.items():
            root, _filename = os.path.split(path)
            if root:
                await sfs._makedirs(root, exist_ok=True)
            async with await sfs._open_async(path, "wb") as stream_file:
                await stream_file.write(data)
            await sfs.fs._touch(sfs._join("/emptyfile"))
        yield sfs
