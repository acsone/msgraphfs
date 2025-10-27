import asyncio
import datetime
import logging
import mimetypes
import os
import re
import threading
import weakref
from urllib.parse import urlparse

import httpx
from authlib.integrations.httpx_client import AsyncOAuth2Client
from fsspec.asyn import (
    AbstractAsyncStreamedFile,
    AbstractBufferedFile,
    AsyncFileSystem,
    FSTimeoutError,
    sync,
    sync_wrapper,
)
from fsspec.utils import tokenize
from httpx import HTTPStatusError, Response
from httpx._types import URLTypes

HTTPX_RETRYABLE_ERRORS = (
    asyncio.TimeoutError,
    httpx.NetworkError,
    httpx.ProxyError,
    httpx.TimeoutException,
)

HTTPX_RETRYABLE_HTTP_STATUS_CODES = (500, 502, 503, 504)


_logger = logging.getLogger(__name__)


def get_running_loop():
    """Get the currently running event loop."""
    # this was removed from fsspec in https://github.com/fsspec/filesystem_spec/pull/1134
    if hasattr(asyncio, "get_running_loop"):
        return asyncio.get_running_loop()
    else:
        loop = asyncio._get_running_loop()
        if loop is None:
            raise RuntimeError("no running event loop")
        else:
            return loop


def parse_range_header(range_header):
    # Regular expression to match a range header like 'bytes=0-499'
    range_pattern = r"bytes=(\d+)?-(\d+)?"

    match = re.match(range_pattern, range_header)

    if match:
        start = match.group(1)
        start = int(start) if start else None  # Convert to int if not None
        end = match.group(2)  # Could be None if range is 'bytes=100-'
        end = int(end) if end else None  # Convert to int if not None
        return start, end
    else:
        raise ValueError("Invalid Range header format")


def parse_msgraph_url(url_path):  # noqa: C901
    """Parse a msgraph URL to extract site_name, drive_name, and path.

    Supports formats:
    - msgd://site_name/drive_name/path/to/file
    - sharepoint://site_name/drive_name/path/to/file
    - onedrive://drive_name/path/to/file
    - msgd://site_name/drive_name
    - msgd://site_name

    Args:
        url_path: The URL or path to parse

    Returns:
        tuple: (site_name, drive_name, path) where path defaults to "/"
    """
    if not url_path:
        return None, None, "/"

    # Handle URL format
    if "://" in url_path:
        parsed = urlparse(url_path)
        protocol = parsed.scheme.lower()
        path_parts = parsed.path.strip("/").split("/") if parsed.path.strip("/") else []

        if protocol in ["msgd", "sharepoint"]:
            # msgd:// and sharepoint:// format: protocol://site_name/drive_name/path
            site_name = parsed.netloc if parsed.netloc else None
            if not path_parts:
                return site_name, None, "/"
            elif len(path_parts) == 1:
                return site_name, path_parts[0], "/"
            else:
                return site_name, path_parts[0], "/" + "/".join(path_parts[1:])

        elif protocol == "onedrive":
            # onedrive:// format: onedrive://drive_name/path (no site, personal OneDrive)
            # The netloc becomes the drive name for OneDrive
            drive_name = parsed.netloc if parsed.netloc else None
            if not drive_name and path_parts:
                # If no netloc, first path part is drive name
                drive_name = path_parts[0]
                file_path = (
                    "/" + "/".join(path_parts[1:]) if len(path_parts) > 1 else "/"
                )
            else:
                # netloc is drive name, path is file path
                file_path = "/" + "/".join(path_parts) if path_parts else "/"
            return None, drive_name, file_path

        else:
            # Unknown protocol, treat like msgd://
            site_name = parsed.netloc if parsed.netloc else None
            if not path_parts:
                return site_name, None, "/"
            elif len(path_parts) == 1:
                return site_name, path_parts[0], "/"
            else:
                return site_name, path_parts[0], "/" + "/".join(path_parts[1:])
    else:
        # Handle path-only format
        path_parts = url_path.strip("/").split("/") if url_path.strip("/") else []
        site_name = None

        if not path_parts:
            return site_name, None, "/"
        elif len(path_parts) == 1:
            return site_name, path_parts[0], "/"
        else:
            return site_name, path_parts[0], "/" + "/".join(path_parts[1:])


def wrap_http_not_found_exceptions(func):
    """Wrap a function that calls an HTTP request to handle 404 errors."""

    async def wrapper(*args, **kwargs):
        try:
            return await func(*args, **kwargs)
        except HTTPStatusError as e:
            if e.response.status_code == 404:
                path = e.request.url.path
                if "root:" in path:
                    path = path.split("root:")[-1]
                    path = path[:-1] if path[-1] == ":" else path
                raise FileNotFoundError(f"File not found: {path}") from e
            raise e

    return wrapper


@wrap_http_not_found_exceptions
async def _http_call_with_retry(func, *, args=(), kwargs=None, retries) -> Response:
    kwargs = kwargs or {}
    retries = 1
    for i in range(retries):
        try:
            response = await func(*args, **kwargs)
            response.raise_for_status()
            return response
        except HTTPX_RETRYABLE_ERRORS as e:
            if i == retries - 1:
                raise e
            _logger.debug("Retryable error: %s", e)
            await asyncio.sleep(min(1.7**i * 0.1, 15))
            continue
        except HTTPStatusError as e:
            if e.response.status_code in HTTPX_RETRYABLE_HTTP_STATUS_CODES:
                if i == retries - 1:
                    raise e
                _logger.debug("Retryable HTTP status code: %s", e.response.status_code)
                await asyncio.sleep(min(1.7**i * 0.1, 15))
                continue
            if e.response.status_code != 404:
                _logger.error(
                    "HTTP error %s: %s", e.response.status_code, e.response.content
                )
            raise e


class AbstractMSGraphFS(AsyncFileSystem):
    """A filesystem that represents microsoft files exposed through the microsoft graph
    API.

    parameters:
    oauth2_client_params (dict): Parameters for the OAuth2 client to use for
        authentication. see https://docs.authlib.org/en/latest/client/api.html#authlib.integrations.httpx_client.AsyncOAuth2Client
    """

    retries = 5
    blocksize = 10 * 1024 * 1024  # 10 MB

    def __init__(
        self,
        oauth2_client_params: dict,
        asynchronous: bool = False,
        loop=None,
        **kwargs,
    ):
        from fsspec.asyn import get_loop

        super_kwargs = kwargs.copy()
        super_kwargs.pop("use_listings_cache", None)
        super_kwargs.pop("listings_expiry_time", None)
        super_kwargs.pop("max_paths", None)
        # passed to fsspec superclass... we don't support directory caching
        super().__init__(
            asynchronous=asynchronous, loop=loop or get_loop(), **super_kwargs
        )

        # Store initialization parameters for lazy initialization
        self._oauth2_client_params = oauth2_client_params
        self._client = None
        self._client_lock = threading.Lock() if not asynchronous else None
        self._client_pid = None  # Track which process created the client
        self.use_recycle_bin = kwargs.get("use_recycle_bin", False)

    @property
    def client(self) -> AsyncOAuth2Client:
        """Lazy-initialized, fork-safe OAuth2 client."""
        current_pid = os.getpid()

        # Check if we need to initialize or reinitialize after fork
        if self._client is None or self._client_pid != current_pid:
            if self.asynchronous:
                # For async mode, we can't use locks, but async is typically single-threaded
                self._init_client()
                self._client_pid = current_pid
            else:
                # Thread-safe lazy initialization for sync mode
                with self._client_lock:
                    # Double-check after acquiring lock
                    if self._client is None or self._client_pid != current_pid:
                        self._init_client()
                        self._client_pid = current_pid

        return self._client

    def _init_client(self):
        """Initialize the OAuth2 client."""
        # Close existing client if it exists
        if self._client is not None:
            try:
                # Try to close the old client gracefully
                self.close_http_session(self._client, getattr(self, "loop", None))
            except Exception:
                # Ignore errors during cleanup
                pass

        # Create new client
        self._client = AsyncOAuth2Client(
            **self._oauth2_client_params,
            follow_redirects=True,
        )

        # Register cleanup for non-async mode
        if not self.asynchronous:
            weakref.finalize(self, self.close_http_session, self._client, self.loop)

    def __del__(self):
        """Destructor to ensure HTTP client is properly closed."""
        try:
            if hasattr(self, "client") and self.client:
                self.close_http_session(self.client, getattr(self, "loop", None))
        except Exception:
            # Ignore all cleanup errors in destructor
            pass

    def _get_loop(self):
        """Get the current event loop, following adlfs pattern."""
        try:
            # Need to confirm there is an event loop running in
            # the thread. If not, create the fsspec loop
            # and set it. This is to handle issues with
            # Async Credentials from the Azure SDK
            loop = get_running_loop()
        except RuntimeError:
            from fsspec.asyn import get_loop

            loop = get_loop()
            asyncio.set_event_loop(loop)

        return loop

    @property
    def loop(self):
        """Get the event loop for this filesystem."""
        return self._get_loop()

    @staticmethod
    def close_http_session(
        client: AsyncOAuth2Client, loop: asyncio.AbstractEventLoop | None = None
    ):
        """Close the HTTP session safely."""
        # Only attempt cleanup if we have a loop and it's still active
        if loop is not None and not loop.is_closed():
            try:
                # Check if the loop is running
                if loop.is_running():
                    # Create a task to close the client
                    loop.create_task(client.aclose())
                    return
                else:
                    # If loop is not running, use sync with a short timeout
                    sync(loop, client.aclose, timeout=0.1)
                    return
            except (RuntimeError, FSTimeoutError, Exception):
                # Silently ignore cleanup errors - the process is shutting down
                pass

        # If we can't properly close, just ignore - this is cleanup code
        try:
            # Last resort: try to close synchronously if possible
            if hasattr(client, "_client") and hasattr(client._client, "close"):
                # Some HTTP clients have synchronous close methods
                client._client.close()
        except Exception:
            # Ignore all cleanup errors - we're shutting down anyway
            pass

    def _path_to_url(self, path, item_id=None, action=None) -> str:
        """This method must be implemented by subclasses to convert a path to a valid
        URL to call the Microsoft Graph API for the given path according to the target
        service.

        (OneDrive, SharePoint, etc.)
        """
        raise NotImplementedError

    async def _path_to_url_async(self, path, item_id=None, action=None) -> str:
        """Async version of _path_to_url.

        Must be implemented by subclasses.
        """
        raise NotImplementedError

    def _get_path(self, drive_item_info: dict) -> str:
        parent_path = drive_item_info["parentReference"].get("path")
        if not parent_path:
            return "/"
        # remove all the part before the "root:"
        parent_path = parent_path.split("root:")[1]
        if parent_path and not parent_path.startswith("/"):
            parent_path = "/" + parent_path
        return parent_path + "/" + drive_item_info["name"]

    def _drive_item_info_to_fsspec_info(self, drive_item_info: dict) -> dict:
        """Convert a drive item info to a fsspec info dictionary.

        see
        https://docs.microsoft.com/en-us/graph/api/resources/driveitem?view=graph-rest-1.0
        """
        _type = "other"
        if drive_item_info.get("folder"):
            _type = "directory"
        elif drive_item_info.get("file"):
            _type = "file"
        data = {
            "name": self._get_path(drive_item_info),
            "size": drive_item_info.get("size", 0),
            "type": _type,
            "item_info": drive_item_info,
            "time": datetime.datetime.fromisoformat(
                drive_item_info.get("createdDateTime", "1970-01-01T00:00:00Z").replace(
                    "Z", "+00:00"
                )
            ),
            "mtime": datetime.datetime.fromisoformat(
                drive_item_info.get(
                    "lastModifiedDateTime", "1970-01-01T00:00:00Z"
                ).replace("Z", "+00:00")
            ),
            "id": drive_item_info.get("id"),
        }

        # Add webUrl if available
        if "webUrl" in drive_item_info:
            data["weburl"] = drive_item_info["webUrl"]

        # Add mimetype for files
        if _type == "file":
            file_info = drive_item_info.get("file", {})
            data["mimetype"] = file_info.get("mimeType", "")

        # Add custom fields if available (typically from SharePoint lists)
        if "fields" in drive_item_info:
            data["fields"] = drive_item_info["fields"]

        # Add permissions if they were expanded/included in the response
        if "permissions" in drive_item_info:
            data["permissions"] = self._format_permissions(
                drive_item_info["permissions"]
            )

        return data

    def _format_permissions(self, permissions: list) -> dict:
        """Format permissions from Microsoft Graph API into a more readable structure.

        Args:
            permissions: List of permission objects from Graph API

        Returns:
            dict: Formatted permissions with users, groups, and access levels
        """
        if not permissions:
            return {
                "users": [],
                "groups": [],
                "links": [],
                "summary": {"total_permissions": 0},
            }

        users = []
        groups = []
        links = []

        for perm in permissions:
            perm_info = {
                "id": perm.get("id"),
                "roles": perm.get("roles", []),
                "expires": perm.get("expirationDateTime"),
                "has_password": perm.get("hasPassword", False),
            }

            # Handle different grantee types
            granted_to = perm.get("grantedTo")
            granted_to_identities = perm.get("grantedToIdentities", [])

            if granted_to:
                # Direct user/group permission
                if granted_to.get("user"):
                    user_info = granted_to["user"]
                    users.append(
                        {
                            **perm_info,
                            "type": "user",
                            "email": user_info.get("email"),
                            "display_name": user_info.get("displayName"),
                            "id": user_info.get("id"),
                        }
                    )
                elif granted_to.get("group"):
                    group_info = granted_to["group"]
                    groups.append(
                        {
                            **perm_info,
                            "type": "group",
                            "email": group_info.get("email"),
                            "display_name": group_info.get("displayName"),
                            "id": group_info.get("id"),
                        }
                    )

            # Handle multiple identities (e.g., for sharing links)
            for identity in granted_to_identities:
                if identity.get("user"):
                    user_info = identity["user"]
                    users.append(
                        {
                            **perm_info,
                            "type": "user",
                            "email": user_info.get("email"),
                            "display_name": user_info.get("displayName"),
                            "id": user_info.get("id"),
                        }
                    )
                elif identity.get("group"):
                    group_info = identity["group"]
                    groups.append(
                        {
                            **perm_info,
                            "type": "group",
                            "email": group_info.get("email"),
                            "display_name": group_info.get("displayName"),
                            "id": group_info.get("id"),
                        }
                    )

            # Handle sharing links
            link = perm.get("link")
            if link:
                links.append(
                    {
                        **perm_info,
                        "type": "link",
                        "link_type": link.get("type"),  # e.g., "view", "edit", "embed"
                        "scope": link.get("scope"),  # e.g., "anonymous", "organization"
                        "web_url": link.get("webUrl"),
                    }
                )

        return {
            "users": users,
            "groups": groups,
            "links": links,
            "summary": {
                "total_permissions": len(permissions),
                "user_count": len(users),
                "group_count": len(groups),
                "link_count": len(links),
            },
        }

    async def _get_item_id(self, path: str, throw_on_missing=False) -> str | None:
        """Get the item ID of a file or directory.

        Parameters:
        path (str): The path to the file or directory.

        Returns:
        str: The item ID of the file or directory if it exists, otherwise None.
        """
        url = await self._path_to_url_async(path)
        try:
            response = await self._msgraph_get(url, params={"select": "id"})
            return response.json()["id"]
        except FileNotFoundError:
            if throw_on_missing:
                raise
            return None

    get_item_id = sync_wrapper(_get_item_id)

    async def _get_item_reference(self, path: str, item_id: str | None = None) -> dict:
        """Return a dictionary with information about the item reference of the given
        path.

        This method is useful when you need to get an itemReference to
        use as an argument in other methods. see
        https://docs.microsoft.com/en-us/graph/api/resources/itemreference?view=graph-rest-1.0
        """
        url = await self._path_to_url_async(path, item_id=item_id)
        response = await self._msgraph_get(
            url,
            params={
                "select": "id,driveId,driveType,name,path,shareId,sharepointIds,siteId"
            },
        )
        return response.json()

    @staticmethod
    def _guess_type(path: str) -> str:
        return mimetypes.guess_type(path)[0] or "application/octet-stream"

    ################################################
    # Helper methods to call the Microsoft Graph API
    ################################################
    async def _call_msgraph(
        self, http_method: str, url: URLTypes, *args, **kwargs
    ) -> Response:
        """Call the Microsoft Graph API."""
        # Ensure token is available before making requests
        if self.client.token is None:
            await self.client.fetch_token()

        return await _http_call_with_retry(
            self.client.request,
            args=(http_method, url, *args),
            kwargs=kwargs,
            retries=self.retries,
        )

    call_msgraph = sync_wrapper(_call_msgraph)

    async def _msgraph_get(self, url: URLTypes, *args, **kwargs) -> Response:
        """Send a GET request to the Microsoft Graph API."""
        return await self._call_msgraph("GET", url, *args, **kwargs)

    msgraph_get = sync_wrapper(_msgraph_get)

    async def _msgraph_post(self, url: URLTypes, *args, **kwargs) -> Response:
        """Send a POST request to the Microsoft Graph API."""
        return await self._call_msgraph("POST", url, *args, **kwargs)

    msgraph_post = sync_wrapper(_msgraph_post)

    async def _msgraph_put(self, url: URLTypes, *args, **kwargs) -> Response:
        """Send a PUT request to the Microsoft Graph API."""
        return await self._call_msgraph("PUT", url, *args, **kwargs)

    msgraph_put = sync_wrapper(_msgraph_put)

    async def _msgraph_delete(self, url: URLTypes, *args, **kwargs) -> Response:
        """Send a DELETE request to the Microsoft Graph API."""
        return await self._call_msgraph("DELETE", url, *args, **kwargs)

    msgraph_delete = sync_wrapper(_msgraph_delete)

    async def _msgraph_patch(self, url: URLTypes, *args, **kwargs) -> Response:
        """Send a PATCH request to the Microsoft Graph API."""
        return await self._call_msgraph("PATCH", url, *args, **kwargs)

    msgraph_patch = sync_wrapper(_msgraph_patch)

    ################################################
    # Others methods
    ################################################

    async def _get_copy_status(self, url: str) -> dict[str:str]:
        """Get the status of a copy operation.

        The response will be a dictionary with the following keys
        "status": The status of the copy operation. Possible values are:
        "completed", "failed", "inProgress", "notStarted" "resource_id":
        The ID of the resource that was copied. "percent_complete": The
        percentage of the copy operation that has completed.
        """
        response = await httpx.AsyncClient().get(url)
        value = response.json()
        return {
            "status": value.get("status"),
            "resource_id": value.get("resourceId"),
            "percent_complete": value.get("percentageComplete"),
        }

    get_copy_status = sync_wrapper(_get_copy_status)

    async def _msggraph_item_copy(
        self, path1: str, path2: str, wait_completion=True, **kwargs
    ):
        """Copy a path to another.

        Parameters
        ----------
        path1 : str
            Source path
        path2 : str
            Destination path
        wait_completion : bool (=True)
            In microsoft graph API, in many cases the copy action is performed
            asynchronously. The response from the API will only indicate that the
            copy operation was accepted or rejected; If wait_completion is True,
            the method will return only after the copy operation is completed by
            monitoring the status of the copy operation.
            If wait_completion is False, the method will return immediately after the
            call to the Microsoft Graph API with the URL where the status of the copy
            operation can be monitored. You can use this URL to call the method get_copy_status
            to monitor the status of the copy operation. (or _get_copy_status method
            in the case of async running)

            Note: the status URL does not require authentication to be accessed. It can be
            accessed by anyone who has the URL since it's a temporary URL that is only valid
            for a short period of time. It's particularly useful when you want to monitor the
            status of the copy operation from a different process or machine (for exemple, in
            a web application).
        """
        source_item_id = await self._get_item_id(path1, throw_on_missing=True)
        url = await self._path_to_url_async(
            path1, item_id=source_item_id, action="copy"
        )
        path2 = self._strip_protocol(path2)
        parent_path, _file_name = path2.rsplit("/", 1)
        item_reference = await self._get_item_reference(parent_path)
        json = {
            "parentReference": item_reference,
            "name": _file_name,
        }
        response = await self._msgraph_post(url, json=json)
        headers = response.headers
        status_url = headers.get("Location")
        if not wait_completion:
            return status_url
        while True:
            status = await self._get_copy_status(status_url)
            if status["status"] == "completed":
                break
            if status["status"] == "failed":
                raise RuntimeError("Copy operation failed")
            await asyncio.sleep(1)

    async def __delete_item(self, path: str, item_id: str | None = None, **kwargs):
        item_id = item_id or await self._get_item_id(path, throw_on_missing=True)
        use_recycle_bin = kwargs.get("use_recycle_bin", self.use_recycle_bin)
        if use_recycle_bin:
            url = await self._path_to_url_async(path, item_id=item_id)
            await self._msgraph_delete(url)
        else:
            url = await self._path_to_url_async(
                path, item_id=item_id, action="permanentDelete"
            )
            await self._msgraph_post(url)
        self.invalidate_cache(path)

    #############################################################
    # Implement required async methods for the fsspec interface
    #############################################################
    async def _created(self, path: str) -> datetime.datetime:
        return (await self._info(path))["time"]

    created = sync_wrapper(_created)

    async def _modified(self, path) -> datetime.datetime:
        return (await self._info(path))["mtime"]

    modified = sync_wrapper(_modified)

    async def _exists(self, path: str, **kwargs) -> bool:
        return await self._get_item_id(path) is not None

    async def _info(
        self, path: str, item_id: str | None = None, expand: str | None = None, **kwargs
    ) -> dict:
        """Get information about a file or directory.

        Parameters
        ----------
        path : str
            Path to get information about
        item_id: str
            If given, the item_id will be used instead of the path to get
            information about the given path.
        expand: str
            A string used to expand the properties of the item. see
            https://docs.microsoft.com/en-us/graph/api/resources/driveitem?view=graph-rest-1.0
            For example, if you want to expand the properties to include the thumbnails,
            you can pass "thumbnails" as the value of the expand parameter.
        """

        url = await self._path_to_url_async(path, item_id=item_id)
        params = {}
        if expand:
            params = {"expand": expand}
        response = await self._msgraph_get(url, params=params)
        return self._drive_item_info_to_fsspec_info(response.json())

    async def _ls(
        self,
        path: str,
        detail: bool = True,
        item_id: str | None = None,
        expand: str | None = None,
        **kwargs,
    ) -> list[dict | str]:
        """List files in the given path.

        Parameters
        ----------
        path : str
            Path to list files in
        detail: bool
            if True, gives a list of dictionaries, where each is the same as
            the result of ``info(path)``. If False, gives a list of paths
            (str).
        item_id: str
            If given, the item_id will be used instead of the path to list
            the files in the given path.
        expand: str
            A string used to expand the properties of the item. see
            https://docs.microsoft.com/en-us/graph/api/resources/driveitem?view=graph-rest-1.0
            For example, if you want to expand the properties to include the thumbnails,
            you can pass "thumbnails" as the value of the expand parameter.
        kwargs: may have additional backend-specific options, such as version
            information
        """
        url = await self._path_to_url_async(path, item_id=item_id, action="children")
        params = None
        if expand and not detail:
            raise ValueError(
                "The expand parameter can only be used when detail is True"
            )
        if not detail:
            params = {"select": "name,parentReference"}
        if expand:
            params = {"expand": expand}
        response = await self._msgraph_get(url, params=params)
        result = response.json()
        items = result.get("value", [])
        while "@odata.nextLink" in result:
            response = await self._msgraph_get(result["@odata.nextLink"])
            result = response.json()
            items.extend(result.get("value", []))
        if not items:
            # maybe the path is a file
            try:
                item = await self._info(path, expand=expand, **kwargs)
                if item["type"] == "file":
                    items = [item["item_info"]]
            except FileNotFoundError:
                pass
        if detail:
            return [self._drive_item_info_to_fsspec_info(item) for item in items]
        else:
            return [self._get_path(item) for item in items]

    async def _cat_file(
        self,
        path: str,
        start: int = None,
        end: int = None,
        item_id: str | None = None,
        **kwargs,
    ):
        url = await self._path_to_url_async(path, item_id=item_id, action="content")
        headers = kwargs.get("headers", {})
        if start is not None or end is not None:
            range = await self._process_limits(path, start, end)
            # range is expressed as "bytes={start}-{end}"
            # extract start and end values from the range string
            # to know if we are at the end of the file
            rstart, rend = parse_range_header(range)
            if rend is not None:
                size = await self._size(path)
                if rend > size:
                    rend = size
                if rstart and rend and (rstart > rend or rstart == rend == size):
                    return b""
            headers["Range"] = range
        response = await self._msgraph_get(url, headers=headers)
        return response.content

    async def _pipe_file(self, path: str, value: bytes, **kwargs):
        async with await self.open_async(path, "wb") as f:
            await f.write(value)

    async def _get_file(self, rpath: str, lpath: str, **kwargs):
        headers = kwargs.get("headers", {})
        content = await self._cat_file(rpath, **kwargs, headers=headers)
        with open(lpath, "wb") as f:
            f.write(content)

    async def _put_file(self, lpath: str, rpath: str, **kwargs):
        with open(lpath, "rb") as f:
            data = f.read()
        await self._pipe_file(rpath, data, **kwargs)
        while rpath:
            self.invalidate_cache(rpath)
            rpath = self._parent(rpath)

    async def _rm_file(self, path: str, item_id: str | None = None, **kwargs):
        if not await self._isfile(path):
            raise FileNotFoundError(f"File not found: {path}")
        await self.__delete_item(path, item_id=item_id, **kwargs)

    async def _copy(
        self,
        path1,
        path2,
        recursive=False,
        on_error=None,
        maxdepth=None,
        batch_size=None,
        wait_completion=True,
        **kwargs,
    ):
        if recursive:
            return await self._msggraph_item_copy(
                path1, path2, wait_completion=wait_completion, **kwargs
            )
        return await super()._copy(
            path1,
            path2,
            recursive=recursive,
            on_error=on_error,
            maxdepth=maxdepth,
            batch_size=batch_size,
            wait_completion=wait_completion,
            **kwargs,
        )

    async def _cp_file(self, path1: str, path2: str, wait_completion=True, **kwargs):
        return await self._msggraph_item_copy(
            path1, path2, wait_completion=wait_completion, **kwargs
        )

    async def _isfile(self, path: str) -> bool:
        url = await self._path_to_url_async(path)
        try:
            response = await self._msgraph_get(url, params={"select": "file"})
        except FileNotFoundError:
            return False
        return response.json().get("file") is not None

    async def _isdir(self, path: str) -> bool:
        url = await self._path_to_url_async(path)
        try:
            response = await self._msgraph_get(url, params={"select": "folder"})
        except FileNotFoundError:
            return False
        return response.json().get("folder") is not None

    async def _size(self, path: str) -> int:
        url = await self._path_to_url_async(path)
        response = await self._msgraph_get(url, params={"select": "size"})
        return response.json().get("size", 0)

    async def _mkdir(self, path, create_parents=True, exist_ok=False, **kwargs) -> str:
        path = self._strip_protocol(path).rstrip("/")
        parent, child = path.rsplit("/", 1)
        parent_id = await self._get_item_id(parent)
        if not parent_id and not create_parents:
            raise FileNotFoundError(f"Parent directory does not exists: {parent}")
        if not parent_id:
            await self._mkdir(parent, create_parents=create_parents)
            parent_id = await self._get_item_id(parent)
        url = await self._path_to_url_async(path, item_id=parent_id, action="children")
        response = await self._msgraph_post(
            url,
            json={
                "name": child,
                "folder": {},
                "@microsoft.graph.conflictBehavior": "fail",
            },
        )
        return response.json()["id"]

    async def _makedirs(self, path: str, exist_ok: bool = False):
        try:
            await self._mkdir(path, create_parents=True)
        except HTTPStatusError as e:
            if e.response.status_code == 409:
                if not exist_ok:
                    raise FileExistsError(f"Directory already exists: {path}") from e
            else:
                raise e

    async def _rmdir(self, path: str, **kwargs):
        """Remove a directory if it's empty.

        Parameters
        ----------
        path : str
            Path of the directory to

        use_recycle_bin : bool
            If specified, the value will be used instead of the default value
            of the use_recycle_bin attribute of the class. If the value is True, the
            directory will be deleted and moved to the recycle bin. If False,
            the directory will be permanently deleted. Default is False.
        """
        if not await self._isdir(path):
            raise FileNotFoundError(f"Directory not found: {path}")
        if await self._ls(path):
            raise OSError(f"Directory not empty: {path}")
        item_id = await self._get_item_id(path, throw_on_missing=True)
        await self.__delete_item(path, item_id=item_id, **kwargs)

    rmdir = sync_wrapper(_rmdir)  # not into the list of async methods to auto wrap

    async def _rm(self, path, recursive=False, batch_size=None, **kwargs):
        paths = path
        if not isinstance(paths, list):
            paths = [path]
        for path in paths:
            if not recursive and await self._isdir(path) and await self._ls(path):
                raise OSError(f"Directory not empty: {path}")
            await self.__delete_item(path, **kwargs)

    async def _mv(self, path1, path2, **kwargs):
        source_item_id = await self._get_item_id(path1, throw_on_missing=True)
        url = await self._path_to_url_async(path1, item_id=source_item_id)
        path2 = self._strip_protocol(path2)
        destination_item_id = await self._get_item_id(path2)
        item_reference = None
        name = None
        if destination_item_id:
            item_reference = await self._get_item_reference(path2)
        else:
            parent_path, name = path2.rsplit("/", 1)
            item_reference = await self._get_item_reference(parent_path)
        json = {
            "parentReference": item_reference,
        }
        if name:
            json["name"] = name

        await self._msgraph_patch(url, json=json)
        self.invalidate_cache(path1)

    mv = sync_wrapper(_mv)

    def _open(
        self,
        path,
        mode="rb",
        block_size="default",
        cache_type="readahead",
        autocommit=True,
        size=None,
        cache_options=None,
        item_id=None,
        **kwargs,
    ):
        """Open a file for reading or writing.

        Parameters
        ----------
        path: string
            Path of file
        mode: string
            One of 'r', 'w', 'a', 'rb', 'wb', or 'ab'. These have the same meaning
            as they do for the built-in `open` function.
        block_size: int
            Size of data-node blocks if reading
        fill_cache: bool
            If seeking to new a part of the file beyond the current buffer,
            with this True, the buffer will be filled between the sections to
            best support random access. When reading only a few specific chunks
            out of a file, performance may be better if False.
        cache_type: {"readahead", "none", "mmap", "bytes"}, default "readahead"
            Caching policy in read mode. See the definitions in ``core``.
        cache_options : dict
            Additional options passed to the constructor for the cache specified
            by `cache_type`.
        item_id: str
            If given, the item_id will be used instead of the path to open the file.
        kwargs: dict-like
            Additional parameters used for s3 methods.  Typically used for
            ServerSideEncryption.
        """
        if ("r" in mode or "a" in mode) and not self.isfile(path):
            raise FileNotFoundError(f"File not found: {path}")
        if "a" in mode and not size:
            size = self.size(path)
        return MSGraphBufferedFile(
            fs=self,
            path=path,
            mode=mode,
            block_size=block_size,
            autocommit=autocommit,
            cache_type=cache_type,
            cache_options=cache_options,
            size=size,
            item_id=item_id or self.get_item_id(path),
            **kwargs,
        )

    async def open_async(self, path, mode="rb", **kwargs):
        if ("r" in mode or "a" in mode) and not await self._isfile(path):
            raise FileNotFoundError(f"File not found: {path}")
        if "b" not in mode or kwargs.get("compression"):
            raise ValueError
        size = None
        item_id = kwargs.get("item_id") or await self._get_item_id(
            path, throw_on_missing=False
        )
        if "rb" in mode or "a" in mode:
            # we must provice the size of the file to the constructor
            # to avoid the need to call the info method from within the constructor
            # since in case of async running, the _info method is a coroutine
            # and it's not allowed to call a coroutine from a constructor. If the
            # size is provided, the info method will not be called from the constructor
            info = await self._info(path)
            size = info["size"]
        return MSGraphStreamedFile(
            self, path, mode, size=size, item_id=item_id, **kwargs
        )

    async def _touch(self, path, truncate=True, item_id=None, **kwargs):
        # if the file exists, update the last modified date time
        # otherwise, create an empty file"""
        item_id = item_id or await self._get_item_id(path)
        if item_id and not truncate:
            if truncate:
                url = await self._path_to_url_async(
                    path, item_id=item_id, action="content"
                )
                await self._msgraph_put(
                    url,
                    content=b"",
                    headers={"Content-Type": "application/octet-stream"},
                )
            else:
                url = await self._path_to_url_async(path, item_id=item_id)
                await self._msgraph_patch(
                    url, json={"lastModifiedDateTime": datetime.now().isoformat()}
                )
        else:
            parent_path, file_name = path.rsplit("/", 1)
            parent_id = await self._get_item_id(parent_path, throw_on_missing=True)
            item_id = f"{parent_id}:/{file_name}:"
            url = await self._path_to_url_async(path, item_id=item_id, action="content")
            headers = {"Content-Type": self._guess_type(path)}
            await self._msgraph_put(url, content=b"", headers=headers)
        self.invalidate_cache(path)

    touch = sync_wrapper(_touch)

    async def _checksum(self, path, refresh=False):
        """Unique value for current version of file.

        If the checksum is the same from one moment to another, the contents
        are guaranteed to be the same. If the checksum changes, the contents
        *might* have changed.

        Parameters
        ----------
        path : string/bytes
            path of file to get checksum for
        refresh : bool (=False)
            if False, look in local cache for file details first
        """

        info = await self._info(path, refresh=refresh)

        if info["type"] != "directory":
            return int(info["ETag"].strip('"').split("-")[0], 16)
        else:
            return int(tokenize(info), 16)

    checksum = sync_wrapper(_checksum)

    ########################################################
    # Additional methods specific to the Microsoft Graph API
    ########################################################
    async def _get_content(self, path, item_id=None, params=None) -> bytes:
        """Get the item content.

        Can set format in params to precise the output format (useful to convert docx to pdf)

        Parameters:
            item_id (str): The ID of the item to get the content of.
            params (dict): Additional parameters to pass to the request.

        Returns:
            bytes: stream of content
        """
        params = params or {}
        url = await self._path_to_url_async(path, item_id=item_id, action="content")
        response = await self._msgraph_get(url, **params)
        return response.content

    get_content = sync_wrapper(_get_content)

    async def _preview(self, path, item_id: str | None = None) -> str:
        if not await self._isfile(path):
            raise FileNotFoundError(f"File not found: {path}")
        url = await self._path_to_url_async(path, item_id=item_id, action="preview")
        response = await self._msgraph_post(url)
        return response.json().get("getUrl", [])

    preview = sync_wrapper(_preview)

    async def _checkout(self, path: str, item_id: str | None = None):
        """Check out a file to prevent others from editing the document, and prevent
        your changes from being visible until the documented is checked in.

        Parameters
        ----------
        path : str
            Path of the file to check out
        item_id: str
            If given, the item_id will be used instead of the path to check
            out the file.
        """
        if not await self._isfile(path):
            raise FileNotFoundError(f"File not found: {path}")
        url = await self._path_to_url_async(path, item_id=item_id, action="checkout")
        await self._msgraph_post(url)

    checkout = sync_wrapper(_checkout)

    async def _checkin(self, path: str, comment: str, item_id: str | None = None):
        """Check in a checked out file, which makes the version of the document
        available to others.

        Parameters
        ----------
        path : str
            Path of the file to check in
        comment : str
            Comment to add to the check-in
        item_id: str
            If given, the item_id will be used instead of the path to check
            in the file.
        """
        if not await self._isfile(path):
            raise FileNotFoundError(f"File not found: {path}")
        url = await self._path_to_url_async(path, item_id=item_id, action="checkin")
        await self._msgraph_post(url, json={"comment": comment})

    checkin = sync_wrapper(_checkin)

    async def _get_versions(self, path: str, item_id: str | None = None) -> list[dict]:
        """Get the versions of a file.

        Parameters
        ----------
        path : str
            Path of the file to get the versions of
        item_id: str
            If given, the item_id will be used instead of the path to get
            the versions of the file.
        """
        if not await self._isfile(path):
            raise FileNotFoundError(f"File not found: {path}")
        url = await self._path_to_url_async(path, item_id=item_id, action="versions")
        response = await self._msgraph_get(url)
        result = response.json()
        items = result.get("value", [])
        while "@odata.nextLink" in result:
            response = await self._msgraph_get(result["@odata.nextLink"])
            result = response.json()
            items.extend(result.get("value", []))
        return items

    get_versions = sync_wrapper(_get_versions)

    async def _get_sharepoint_ids(self, path: str, item_id: str | None = None) -> dict:
        """Get the SharePoint IDs of a file or directory on a SharePoint site.

        Parameters
        ----------
        path : str
            Path of the file or directory to get the SharePoint IDs of
        item_id: str
            If given, the item_id will be used instead of the path to get
            the SharePoint IDs of the file or directory.
        """
        url = await self._path_to_url_async(path, item_id=item_id)
        response = await self._msgraph_get(url, params={"select": "sharepointIds"})
        return response.json().get("sharepointIds", {})

    get_sharepoint_ids = sync_wrapper(_get_sharepoint_ids)

    async def _set_properties(
        self, path: str, properties: dict, item_id: str | None = None
    ):
        """Set the properties of a file or directory on a SharePoint site.

        Parameters
        ----------
        path : str
            Path of the file or directory to set the properties of
        properties : dict
            Dictionary of properties to set. The keys are the property names and the values are the property values.
        item_id: str
            If given, the item_id will be used instead of the path to set
            the properties of the file or directory.
        """
        sharepoint_ids = await self._get_sharepoint_ids(path, item_id=item_id)
        if not sharepoint_ids:
            raise ValueError(
                f"Cannot set properties for the given path: {path}: not a SharePoint item"
            )
        sharepoint_item_id = sharepoint_ids.get("listItemId")
        site_id = sharepoint_ids.get("siteId")
        list_id = sharepoint_ids.get("listId")
        url = f"https://graph.microsoft.com/v1.0/sites/{site_id}/lists/{list_id}/items/{sharepoint_item_id}/fields"
        await self._msgraph_patch(url, json=properties)
        self.invalidate_cache(path)

    set_properties = sync_wrapper(_set_properties)

    async def _get_permissions(self, path: str, item_id: str | None = None) -> dict:
        """Get detailed permissions for a file or directory.

        This method fetches the permissions from the Microsoft Graph API and formats them
        into a more readable structure showing users, groups, and sharing links with their
        respective access levels.

        Parameters
        ----------
        path : str
            Path of the file or directory to get permissions for
        item_id: str
            If given, the item_id will be used instead of the path to get
            the permissions for the file or directory.

        Returns
        -------
        dict
            Formatted permissions with users, groups, links, and summary information

        Examples
        --------
        >>> permissions = fs.get_permissions("/documents/important.docx")
        >>> print(f"Total permissions: {permissions['summary']['total_permissions']}")
        >>> for user in permissions['users']:
        ...     print(f"User: {user['display_name']} - Roles: {user['roles']}")
        """
        url = await self._path_to_url_async(path, item_id=item_id, action="permissions")
        response = await self._msgraph_get(url)
        result = response.json()
        permissions = result.get("value", [])

        # Handle pagination
        while "@odata.nextLink" in result:
            response = await self._msgraph_get(result["@odata.nextLink"])
            result = response.json()
            permissions.extend(result.get("value", []))

        return self._format_permissions(permissions)

    get_permissions = sync_wrapper(_get_permissions)


class MSGDriveFS(AbstractMSGraphFS):
    """A unified filesystem for SharePoint sites and drives.

    This class automatically handles both single-site/drive operations and multi-site operations
    based on the parameters provided during initialization:

    - Single-site mode: When site_name + drive_name or drive_id are provided
    - Multi-site mode: When neither site_name + drive_name nor drive_id are provided

    In multi-site mode, the filesystem can handle URL-based paths that specify
    the site and drive dynamically (e.g., "msgd://SiteA/DriveB/file.txt").

    Examples:
    ---------
    Single-site mode (traditional):
        fs = MSGDriveFS(
            client_id="your-client-id",
            tenant_id="your-tenant-id",
            client_secret="your-secret",
            site_name="TestSite",
            drive_name="Documents"
        )
        files = fs.ls("/folder/file.txt")  # operates on TestSite/Documents

    Single-site mode with URL initialization:
        fs = MSGDriveFS(
            client_id="your-client-id",
            tenant_id="your-tenant-id",
            client_secret="your-secret",
            url_path="msgd://TestSite/Documents"
        )
        files = fs.ls("/folder/file.txt")  # operates on TestSite/Documents

    Multi-site mode:
        fs = MSGDriveFS(
            client_id="your-client-id",
            tenant_id="your-tenant-id",
            client_secret="your-secret"
        )
        files = fs.ls("msgd://TestSite/Documents/folder/file.txt")  # dynamic routing

    Using with fsspec (recommended):
        import fsspec

        # Single-site via fsspec
        fs = fsspec.filesystem(
            "msgd",
            client_id="...",
            tenant_id="...",
            client_secret="...",
            site_name="TestSite",
            drive_name="Documents"
        )
        files = fs.ls("/folder/")

        # Multi-site via fsspec
        fs = fsspec.filesystem("msgd", client_id="...", tenant_id="...", client_secret="...")
        files = fs.ls("msgd://TestSite/Documents/folder/")

    Parameters:
    -----------
    drive_id : str, optional
        The ID of the SharePoint drive. If provided, enables single-site mode.
    client_id : str, optional
        OAuth2 client ID. Can also be set via MSGRAPHFS_CLIENT_ID or AZURE_CLIENT_ID environment variables.
    tenant_id : str, optional
        OAuth2 tenant ID. Can also be set via MSGRAPHFS_TENANT_ID or AZURE_TENANT_ID environment variables.
    client_secret : str, optional
        OAuth2 client secret. Can also be set via MSGRAPHFS_CLIENT_SECRET or AZURE_CLIENT_SECRET environment variables.
    site_name : str, optional
        The name of the SharePoint site. If provided with drive_name, enables single-site mode.
    drive_name : str, optional
        The name of the SharePoint drive/library (e.g., "Documents", "CustomLibrary").
        If provided with site_name, enables single-site mode.
    url_path : str, optional
        URL-style path specification (e.g., "msgd://TestSite/Documents").
        If provided, extracts site_name and drive_name from the URL.
        URL parameters override direct site_name/drive_name parameters.
    oauth2_client_params : dict, optional
        Parameters for the OAuth2 client. If not provided, will be built from client_id, tenant_id, client_secret.
    use_recycle_bin : bool, optional
        If True, deleted files are moved to recycle bin. Default is False.
    **kwargs : dict
        Additional arguments passed to the parent class.
    """

    protocol = ("msgd", "sharepoint", "onedrive")

    # Default OAuth2 scopes for Microsoft Graph API (client credentials flow)
    DEFAULT_SCOPES = ["https://graph.microsoft.com/.default"]

    def __init__(
        self,
        drive_id: str | None = None,
        client_id: str | None = None,
        tenant_id: str | None = None,
        client_secret: str | None = None,
        site_name: str | None = None,
        drive_name: str | None = None,
        oauth2_client_params: dict | None = None,
        asynchronous: bool = False,
        loop=None,
        url_path: str | None = None,
        **kwargs,
    ):
        # Get OAuth2 credentials from parameters or environment variables
        # Check MSGRAPHFS_* variables first, then fall back to standard AZURE_* variables
        self.client_id = (
            client_id
            or os.getenv("MSGRAPHFS_CLIENT_ID")
            or os.getenv("AZURE_CLIENT_ID")
        )
        self.tenant_id = (
            tenant_id
            or os.getenv("MSGRAPHFS_TENANT_ID")
            or os.getenv("AZURE_TENANT_ID")
        )
        self.client_secret = (
            client_secret
            or os.getenv("MSGRAPHFS_CLIENT_SECRET")
            or os.getenv("AZURE_CLIENT_SECRET")
        )

        # Parse URL path if provided to extract site_name and drive_name
        if url_path:
            parsed_site, parsed_drive, _ = parse_msgraph_url(url_path)
            # URL parameters override direct parameters
            site_name = parsed_site or site_name
            drive_name = parsed_drive or drive_name

        # Determine operation mode (single-site if site and drive provided, OR drive_id provided)
        self._multi_site_mode = not ((site_name and drive_name) or drive_id)

        # Set site_name and drive_name attributes for all modes
        self.site_name = site_name
        self.drive_name = drive_name

        if self._multi_site_mode:
            # Multi-site mode: cache for drive filesystem instances
            self._drive_cache = {}
            # Store credentials for creating drive instances
            self._stored_client_id = self.client_id
            self._stored_tenant_id = self.tenant_id
            self._stored_client_secret = self.client_secret
            self._stored_oauth2_client_params = oauth2_client_params
            self._stored_kwargs = kwargs.copy()

        # Build oauth2_client_params if not provided
        if oauth2_client_params is None:
            if not all([self.client_id, self.tenant_id, self.client_secret]):
                raise ValueError(
                    "Either oauth2_client_params must be provided, or all of "
                    "client_id, tenant_id, and client_secret must be provided "
                    "(either as parameters or environment variables MSGRAPHFS_CLIENT_ID/"
                    "AZURE_CLIENT_ID, MSGRAPHFS_TENANT_ID/AZURE_TENANT_ID, "
                    "MSGRAPHFS_CLIENT_SECRET/AZURE_CLIENT_SECRET)"
                )

            # Build OAuth2 client parameters with proper configuration
            oauth2_client_params = {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "token_endpoint": f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/token",
                "scope": " ".join(self.DEFAULT_SCOPES),
                "grant_type": "client_credentials",
            }
        else:
            # Extract credentials from provided params for later use
            self.client_id = oauth2_client_params.get("client_id")
            self.tenant_id = self._extract_tenant_from_token_endpoint(
                oauth2_client_params.get("token_endpoint", "")
            )
            self.client_secret = oauth2_client_params.get("client_secret")

        super().__init__(
            oauth2_client_params=oauth2_client_params,
            asynchronous=asynchronous,
            loop=loop,
            **kwargs,
        )

        self.site_name = site_name
        self.drive_name = drive_name
        self.drive_id = drive_id

        # We'll set the drive_url later if drive_id is determined
        if self.drive_id:
            self.drive_url = f"https://graph.microsoft.com/v1.0/drives/{self.drive_id}"
        else:
            self.drive_url = None

    def _parse_path_for_url_routing(self, path: str):
        """Parse a path to extract site_name, drive_name, and file path for URL
        routing."""
        site_name, drive_name, file_path = parse_msgraph_url(path)
        if not site_name:
            raise ValueError(f"Path must include site name: {path}")
        if not drive_name:
            raise ValueError(f"Path must include drive name: {path}")
        return site_name, drive_name, file_path

    def _extract_tenant_from_token_endpoint(self, token_endpoint: str) -> str | None:
        """Extract tenant_id from token endpoint URL."""
        import re

        match = re.search(r"/([a-f0-9-]+)/oauth2", token_endpoint)
        return match.group(1) if match else None

    def _parse_path_for_missing_components(self, path: str):
        """Parse a path to extract missing site_name, drive_name, and return the file
        path.

        Logic:
        - If both site_name and drive_name are set: path is the file path
        - If only site_name is set: path = drive_name/file_path
        - If only drive_id is set (legacy mode): use existing drive_id, skip path parsing
        - If drive_id is set (even with site_name): use drive_id directly, don't parse path
        - If neither is set: path = site_name/drive_name/file_path
        """
        # If we have both site and drive, no parsing needed
        if self.site_name and self.drive_name:
            return self.site_name, self.drive_name, path

        # If we have a drive_id in single-site mode, use it directly regardless of site_name presence
        # This handles both pure legacy mode (only drive_id) and test mode (site_name + drive_id)
        if not self._multi_site_mode and self.drive_id:
            # Use dummy values for site_name and drive_name since the rest of the system expects them
            # The actual API calls will use drive_id directly via drive_url
            site_placeholder = self.site_name or "legacy"
            return site_placeholder, "legacy", path

        # Parse the path to extract missing components
        if "://" in path:
            # Handle URL format
            parsed_site, parsed_drive, file_path = parse_msgraph_url(path)
            # For OneDrive URLs, use a default site name if none specified
            if parsed_site is None and "onedrive://" in path.lower():
                site_name = self.site_name or "me"  # Use "me" as default OneDrive site
            else:
                site_name = self.site_name or parsed_site
            drive_name = self.drive_name or parsed_drive
        else:
            # Handle plain path format
            if not self.site_name and not self.drive_name:
                # Need both site and drive from path: site/drive/file_path
                path_parts = path.strip("/").split("/", 2)
                if len(path_parts) < 2:
                    raise ValueError(
                        f"Path must include site and drive when none specified: {path}"
                    )
                site_name = path_parts[0]
                drive_name = path_parts[1]
                file_path = "/" + path_parts[2] if len(path_parts) > 2 else "/"
            elif self.site_name and not self.drive_name:
                # Need drive from path: drive/file_path
                path_parts = path.strip("/").split("/", 1)
                if len(path_parts) < 1:
                    raise ValueError(
                        f"Path must include drive name when not specified: {path}"
                    )
                site_name = self.site_name
                drive_name = path_parts[0]
                file_path = "/" + path_parts[1] if len(path_parts) > 1 else "/"
            else:
                # This shouldn't happen but handle gracefully
                site_name = self.site_name
                drive_name = self.drive_name
                file_path = path

        if not site_name or not drive_name:
            raise ValueError(f"Unable to determine site and drive from path: {path}")

        return site_name, drive_name, file_path

    def _get_drive_fs(self, site_name: str, drive_name: str) -> "MSGDriveFS":
        """Get or create a MSGDriveFS instance for the specified site and drive."""
        # If this instance already has the right site/drive, return self
        if self.site_name == site_name and self.drive_name == drive_name:
            return self

        # Legacy mode: if drive_name is "legacy" (from drive_id-only mode),
        # return self since we're using the existing drive_id configuration
        # This handles both pure legacy (site_name="legacy") and test mode (site_name=actual_site)
        if drive_name == "legacy":
            return self

        # If we have a drive cache, use it (always available in multi-site mode)
        if hasattr(self, "_drive_cache") and self._drive_cache is not None:
            cache_key = (site_name, drive_name)
            if cache_key not in self._drive_cache:
                self._drive_cache[cache_key] = MSGDriveFS(
                    client_id=self.client_id,
                    tenant_id=self.tenant_id,
                    client_secret=self.client_secret,
                    site_name=site_name,
                    drive_name=drive_name,
                    asynchronous=self.asynchronous,
                    loop=self.loop,
                )
            return self._drive_cache[cache_key]

        # No caching needed, create a new instance
        return MSGDriveFS(
            client_id=self.client_id,
            tenant_id=self.tenant_id,
            client_secret=self.client_secret,
            site_name=site_name,
            drive_name=drive_name,
            asynchronous=self.asynchronous,
            loop=self.loop,
        )

    # Delegation methods for multi-site operations (used when _multi_site_mode is True)
    async def _ls_multi_site(self, path: str, detail: bool = True, **kwargs):
        """List files in multi-site mode by delegating to appropriate drive
        filesystem."""
        site_name, drive_name, file_path = self._parse_path_for_url_routing(path)
        drive_fs = self._get_drive_fs(site_name, drive_name)
        return await drive_fs._ls(file_path, detail=detail, **kwargs)

    async def _info_multi_site(self, path: str, **kwargs):
        """Get file info in multi-site mode by delegating to appropriate drive
        filesystem."""
        site_name, drive_name, file_path = self._parse_path_for_url_routing(path)
        drive_fs = self._get_drive_fs(site_name, drive_name)
        return await drive_fs._info(file_path, **kwargs)

    async def _cat_file_multi_site(
        self, path: str, start: int | None = None, end: int | None = None, **kwargs
    ):
        """Read file content in multi-site mode by delegating to appropriate drive
        filesystem."""
        site_name, drive_name, file_path = self._parse_path_for_url_routing(path)
        drive_fs = self._get_drive_fs(site_name, drive_name)
        return await drive_fs._cat_file(file_path, start=start, end=end, **kwargs)

    def _open_multi_site(self, path: str, mode: str = "rb", **kwargs):
        """Open file in multi-site mode by delegating to appropriate drive
        filesystem."""
        site_name, drive_name, file_path = self._parse_path_for_url_routing(path)
        drive_fs = self._get_drive_fs(site_name, drive_name)
        return drive_fs._open(file_path, mode=mode, **kwargs)

    # Override existing methods to delegate to multi-site variants when needed
    async def _ls(self, path: str, detail: bool = True, **kwargs):
        """List files, delegating to multi-site logic if needed."""
        if self._multi_site_mode:
            return await self._ls_multi_site(path, detail=detail, **kwargs)
        return await super()._ls(path, detail=detail, **kwargs)

    async def _info(self, path: str, **kwargs):
        """Get file info, delegating to multi-site logic if needed."""
        if self._multi_site_mode:
            return await self._info_multi_site(path, **kwargs)
        return await super()._info(path, **kwargs)

    async def _cat_file(
        self, path: str, start: int | None = None, end: int | None = None, **kwargs
    ):
        """Read file content, delegating to multi-site logic if needed."""
        if self._multi_site_mode:
            return await self._cat_file_multi_site(path, start=start, end=end, **kwargs)
        return await super()._cat_file(path, start=start, end=end, **kwargs)

    def _open(self, path: str, mode: str = "rb", **kwargs):
        """Open file, delegating to multi-site logic if needed."""
        if self._multi_site_mode:
            return self._open_multi_site(path, mode=mode, **kwargs)
        return super()._open(path, mode=mode, **kwargs)

    async def _ensure_drive_id(self) -> str:
        """Ensure drive_id is available, discovering it if necessary."""
        if self.drive_id:
            return self.drive_id

        if not self.site_name:
            # Try to get the default drive for the current user
            try:
                response = await self._msgraph_get(
                    "https://graph.microsoft.com/v1.0/me/drive"
                )
                drive_info = response.json()
                self.drive_id = drive_info["id"]
                self.drive_url = (
                    f"https://graph.microsoft.com/v1.0/drives/{self.drive_id}"
                )
                return self.drive_id
            except Exception as e:
                raise ValueError(
                    "Unable to discover drive_id. Please provide either drive_id or site_name."
                ) from e
        else:
            # Get site_id from site_name, then get specific drive by name or default drive
            site_id = await self._get_site_id()

            if self.drive_name:
                # Find specific drive by name
                drive_id = await self._get_drive_id_by_name(site_id, self.drive_name)
                self.drive_id = drive_id
                self.drive_url = (
                    f"https://graph.microsoft.com/v1.0/drives/{self.drive_id}"
                )
                return self.drive_id
            else:
                # Get default drive
                response = await self._msgraph_get(
                    f"https://graph.microsoft.com/v1.0/sites/{site_id}/drive"
                )
                drive_info = response.json()
                self.drive_id = drive_info["id"]
                self.drive_url = (
                    f"https://graph.microsoft.com/v1.0/drives/{self.drive_id}"
                )
                return self.drive_id

    def _path_to_url(self, path, item_id=None, action=None) -> str:
        # For sync methods, we need to ensure drive_id is available
        if not self.drive_url:
            # Use sync wrapper to ensure drive_id
            self.ensure_drive_id()

        action = action and f"/{action}" if action else ""
        path = self._strip_protocol(path).rstrip("/")
        if path and not path.startswith("/"):
            path = "/" + path
        if path:
            path = f":{path}:"
        if item_id:
            return f"{self.drive_url}/items/{item_id}{action}"

        return f"{self.drive_url}/root{path}{action}"

    async def _path_to_url_async(self, path, item_id=None, action=None) -> str:
        """Async version of _path_to_url that ensures drive_id is available."""
        if not self.drive_url:
            await self._ensure_drive_id()
        return self._path_to_url(path, item_id, action)

    async def _get_site_id(self) -> str:
        if not self.site_name:
            raise ValueError("site_name is required to get site_id")
        url = f"https://graph.microsoft.com/v1.0/sites?search={self.site_name}"
        response = await self._msgraph_get(url)
        sites = response.json().get("value", [])
        if not sites:
            raise ValueError(f"No site found with name '{self.site_name}'")
        return sites[0]["id"]

    async def _get_drive_id_by_name(self, site_id: str, drive_name: str) -> str:
        """Get the drive ID for a specific drive name within a site."""
        url = f"https://graph.microsoft.com/v1.0/sites/{site_id}/drives"
        response = await self._msgraph_get(url)
        drives = response.json().get("value", [])

        for drive in drives:
            if drive.get("name") == drive_name:
                return drive["id"]

        # If not found, list available drives for error message
        available_drives = [d.get("name", "Unknown") for d in drives]
        raise ValueError(
            f"Drive '{drive_name}' not found in site '{self.site_name}'. "
            f"Available drives: {available_drives}"
        )

    async def _get_item_reference(self, path: str, item_id: str | None = None) -> dict:
        # Ensure drive_id is available
        if not self.drive_id:
            await self._ensure_drive_id()
        item_reference = await super()._get_item_reference(path, item_id=item_id)
        return {
            "driveId": self.drive_id,
            "id": item_reference["id"],
        }

    async def _get_recycle_bin_items(self) -> list[dict]:
        """Get the items in the recycle bin. (Beta!!)

        Returns:
            list[dict]: A list of dictionaries with information about the items in the recycle bin.

        see https://docs.microsoft.com/en-us/graph/api/resources/driveitem?view=graph-rest-1.0
        """
        site_id = await self._get_site_id()
        url = f"https://graph.microsoft.com/beta/sites/{site_id}/recycleBin/items"
        response = await self._msgraph_get(url)
        return response.json().get("value", [])

    get_recycle_bin_items = sync_wrapper(_get_recycle_bin_items)
    ensure_drive_id = sync_wrapper(_ensure_drive_id)
    get_drive_id_by_name = sync_wrapper(_get_drive_id_by_name)

    # Override filesystem operations to support path-based site/drive resolution
    async def _ls(self, path: str, detail: bool = True, **kwargs):
        """List files, supporting path-based site/drive resolution."""
        site_name, drive_name, file_path = self._parse_path_for_missing_components(path)
        drive_fs = self._get_drive_fs(site_name, drive_name)

        # If we got back ourselves, use normal behavior
        if drive_fs is self:
            return await super()._ls(file_path, detail=detail, **kwargs)
        else:
            # Delegate to the appropriate drive filesystem
            return await drive_fs._ls(file_path, detail=detail, **kwargs)

    async def _info(self, path: str, **kwargs):
        """Get file info, supporting path-based site/drive resolution."""
        site_name, drive_name, file_path = self._parse_path_for_missing_components(path)
        drive_fs = self._get_drive_fs(site_name, drive_name)

        if drive_fs is self:
            return await super()._info(file_path, **kwargs)
        else:
            return await drive_fs._info(file_path, **kwargs)

    async def _cat_file(
        self, path: str, start: int | None = None, end: int | None = None, **kwargs
    ):
        """Read file content, supporting path-based site/drive resolution."""
        site_name, drive_name, file_path = self._parse_path_for_missing_components(path)
        drive_fs = self._get_drive_fs(site_name, drive_name)

        if drive_fs is self:
            return await super()._cat_file(file_path, start=start, end=end, **kwargs)
        else:
            return await drive_fs._cat_file(file_path, start=start, end=end, **kwargs)

    def _open(self, path: str, mode: str = "rb", **kwargs):
        """Open file, supporting path-based site/drive resolution."""
        site_name, drive_name, file_path = self._parse_path_for_missing_components(path)
        drive_fs = self._get_drive_fs(site_name, drive_name)

        if drive_fs is self:
            return super()._open(file_path, mode=mode, **kwargs)
        else:
            return drive_fs._open(file_path, mode=mode, **kwargs)

    async def _exists(self, path: str, **kwargs):
        """Check if file exists, supporting path-based site/drive resolution."""
        site_name, drive_name, file_path = self._parse_path_for_missing_components(path)
        drive_fs = self._get_drive_fs(site_name, drive_name)

        if drive_fs is self:
            return await super()._exists(file_path, **kwargs)
        else:
            return await drive_fs._exists(file_path, **kwargs)

    async def _mkdir(
        self, path: str, create_parents: bool = True, exist_ok: bool = False, **kwargs
    ):
        """Create directory, supporting path-based site/drive resolution."""
        site_name, drive_name, file_path = self._parse_path_for_missing_components(path)
        drive_fs = self._get_drive_fs(site_name, drive_name)

        if drive_fs is self:
            return await super()._mkdir(
                file_path, create_parents=create_parents, exist_ok=exist_ok, **kwargs
            )
        else:
            return await drive_fs._mkdir(
                file_path, create_parents=create_parents, exist_ok=exist_ok, **kwargs
            )

    async def _rm(self, path: str, recursive: bool = False, **kwargs):
        """Remove file/directory, supporting path-based site/drive resolution."""
        site_name, drive_name, file_path = self._parse_path_for_missing_components(path)
        drive_fs = self._get_drive_fs(site_name, drive_name)

        if drive_fs is self:
            return await super()._rm(file_path, recursive=recursive, **kwargs)
        else:
            return await drive_fs._rm(file_path, recursive=recursive, **kwargs)


class AsyncStreamedFileMixin:
    """Mixin for streamed file-like objects using async iterators."""

    def _init__mixin(self, **kwargs):
        self.path = self.fs._strip_protocol(self.path)
        block_size = kwargs.get("block_size", "default")
        if block_size == "default":
            block_size = None
        self.blocksize = block_size if block_size is not None else self.fs.blocksize
        if "w" in self.mode or "a" in self.mode:
            # block_size must bet a multiple of 320 KiB
            if self.blocksize % (320 * 1024) != 0:
                raise ValueError("block_size must be a multiple of 320 KiB")
        self._item_id = kwargs.get("item_id")
        self._append_mode = "a" in self.mode and self.item_id is not None
        if self._append_mode:
            self.loc = kwargs.get("size", 0)
        self._reset_session_info()

    @property
    async def item_id(self):
        if self._item_id is None:
            self._item_id = await self.fs._get_item_id(self.path)
        return self._item_id

    async def _get_item_id(self):
        return await self.item_id

    get_item_id = sync_wrapper(_get_item_id)

    async def _create_upload_session(self) -> tuple[str, datetime.datetime]:
        """Create a new upload session for the file.

        Returns:
            tuple[str, datetime.datetime]: The URL of the upload session and the expiration date time.

        see https://learn.microsoft.com/en-us/graph/api/driveitem-createuploadsession?view=graph-rest-1.0
        """
        item_id = await self.item_id
        if not item_id:
            parent_path, file_name = self.path.rsplit("/", 1)
            parent_id = await self.fs._get_item_id(parent_path)
            item_id = f"{parent_id}:/{file_name}:"
        url = self.fs._path_to_url(
            self.path, item_id=item_id, action="createUploadSession"
        )
        response = await self.fs._msgraph_post(
            url,
            json={
                "@microsoft.graph.conflictBehavior": "replace",
                # We don't know the size of the file. Explicit commit is required.
                "deferCommit": True,
            },
        )
        json = response.json()
        expiration_dt = datetime.datetime.fromisoformat(json["expirationDateTime"])
        return json["uploadUrl"], expiration_dt

    @property
    def _is_upload_session_expired(self) -> bool:
        """Check if the current upload session is expired."""
        if not self._upload_expiration_dt:
            return True
        now = datetime.datetime.now(datetime.UTC)
        return now > self._upload_expiration_dt

    def _reset_session_info(self):
        """Reset the upload session information."""
        self._upload_session_url = None
        self._upload_expiration_dt = None
        self._chunk_start_pos = 0
        self._remaining_bytes = None
        self._write_called = False

    async def _upload_content_at_once(self, data):
        headers = self.kwargs.get("headers", {})
        if "content-type" not in headers:
            headers["content-type"] = self.fs._guess_type(self.path)
        item_id = await self.item_id
        if not item_id:
            parent_path, file_name = self.path.rsplit("/", 1)
            parent_id = await self.fs._get_item_id(parent_path, throw_on_missing=True)
            item_id = f"{parent_id}:/{file_name}:"
        url = self.fs._path_to_url(self.path, item_id=item_id, action="content")
        await self.fs._msgraph_put(url, content=data, headers=headers)
        self.fs.invalidate_cache(self.path)

    async def _abort_upload_session(self):
        """Abort the current upload session."""
        if self._upload_session_url and not self._is_upload_session_expired:
            await self.fs._msgraph_delete(self._upload_session_url)
        self._reset_session_info()

    async def _commit_upload_session(self):
        """Commit the current upload session."""
        if self._upload_session_url and self._is_upload_session_expired:
            raise RuntimeError("The upload session has expired.")
        if self._upload_session_url:
            await self.fs._msgraph_post(self._upload_session_url)
        self._reset_session_info()

    async def _commit(self):
        _logger.debug("Commit %s" % self)
        # Avoid resetting a file that has been opened in append mode
        # and has not been written to.
        append_no_write = self._append_mode and not self._write_called
        if self.tell() == 0:
            if self.buffer is not None:
                _logger.debug("Empty file committed %s" % self)
                await self._abort_upload_session()
                await self.fs._touch(self.path, **self.kwargs)
        elif not self._upload_session_url:
            if self.buffer is not None:
                if not append_no_write:
                    _logger.debug("One-shot upload of %s" % self)
                    self.buffer.seek(0)
                    data = self.buffer.read()
                    await self._upload_content_at_once(data)
            else:
                raise RuntimeError

        if append_no_write:
            # if not written, we must abort the upload session otherwise the file
            # will be truncated
            await self._abort_upload_session()
        else:
            await self._commit_upload_session()

        # complex cache invalidation, since file's appearance can cause several
        # directories
        parts = self.path.split("/")
        path = parts[0]
        for p in parts[1:]:
            if path in self.fs.dircache and not [
                True for f in self.fs.dircache[path] if f["name"] == path + "/" + p
            ]:
                self.fs.invalidate_cache(path)
            path = path + "/" + p
        pass

    commit = sync_wrapper(_commit)

    async def _discard(self):
        await self._abort_upload_session()

    discard = sync_wrapper(_discard)

    async def _init_write_append_mode(self):
        """Add the initial content of the file to the buffer."""
        if self._append_mode and not self._write_called:
            # If the file is opened in append mode, we must get the current content
            # of the file and add it to the buffer.
            content = await self.fs._cat_file(self.path, item_id=self._item_id)
            self.buffer.write(content)
            self.loc = len(content)

    ########################################################
    ## AbstractBufferedFile methods to implement or override
    ########################################################

    async def _upload_chunk(self, final=False):
        """Write one part of a multi-block file upload.

        Parameters
        ==========
        final: bool
            This is the last block, so should complete file, if
            self.autocommit is True.
        """
        if self.autocommit and final and self.tell() < self.blocksize:
            # only happens when closing small file, use on-shot PUT
            chunk_to_write = False
        else:
            self.buffer.seek(0)
            if self._remaining_bytes:
                chunk_to_write = self._remaining_bytes + self.buffer.read(
                    self.blocksize - len(self._remaining_bytes)
                )
                self._remaining_bytes = None
            else:
                chunk_to_write = self.buffer.read(self.blocksize)
        # we must write into chunk of the same block size. We therefore need to
        # buffer the remaining bytes if the buffer is not a multiple of the block size
        while chunk_to_write:
            chunk_size = len(chunk_to_write)
            if chunk_size < self.blocksize and not final:
                self._remaining_bytes = chunk_to_write
                break

            headers = {
                "Content-Length": str(chunk_size),
                "Content-Range": f"bytes {self._chunk_start_pos}-{self._chunk_start_pos + chunk_size - 1}/*",
            }
            response = await self.fs._msgraph_put(
                self._upload_session_url,
                content=chunk_to_write,
                headers=headers,
            )
            self._upload_expiration_dt = datetime.datetime.fromisoformat(
                response.json()["expirationDateTime"]
            )
            self._chunk_start_pos += chunk_size
            chunk_to_write = self.buffer.read(self.blocksize)

        if self.autocommit and final:
            await self._commit()
        return not final

    async def _initiate_upload(self):
        if self.autocommit and self.tell() < self.blocksize:
            # only happens when closing small file, use on-shot PUT
            return
        # If the file to be uploaded is larger than the block size, then we need to
        # create an upload session to upload the file in chunks.
        self._chunk_start_pos = 0
        (
            self._upload_session_url,
            self._upload_expiration_dt,
        ) = await self._create_upload_session()

    async def _fetch_range(self, start, end) -> bytes:
        """Get the specified set of bytes from remote."""
        item_id = await self.fs._get_item_id(self.path)
        return await self.fs._cat_file(self.path, start=start, end=end, item_id=item_id)

    @property
    def loop(self):
        return self.fs.loop


class MSGraphBufferedFile(AsyncStreamedFileMixin, AbstractBufferedFile):
    """A file-like object representing a file in a SharePoint drive.

    Parameters
    ----------
    fs: MSGDriveFS
        The filesystem this file is part of.
    path: str
        The path to the file.
    mode: str
        The mode to open the file in.
        One of 'rb', 'wb', 'ab'. These have the same meaning
        as they do for the built-in `open` function.
    block_size: int
        Buffer size for reading or writing, 'default' for class default
    autocommit: bool
            Whether to write to final destination; may only impact what
            happens when file is being closed.
    cache_type: {"readahead", "none", "mmap", "bytes"}, default "readahead"
        Caching policy in read mode. See the definitions in ``core``.
    cache_options : dict
        Additional options passed to the constructor for the cache specified
        by `cache_type`.
    size: int
        If given and in read mode, suppressed having to look up the file size
    kwargs:
        Gets stored as self.kwargs
    """

    def __init__(
        self,
        fs: MSGDriveFS,
        path: str,
        mode: str = "rb",
        block_size: int | None = None,
        autocommit: bool = True,
        cache_type: str = "readahead",
        cache_options: dict | None = None,
        size: int | None = None,
        **kwargs,
    ):
        AbstractBufferedFile.__init__(
            self,
            fs,
            path,
            mode,
            block_size,
            autocommit,
            cache_type,
            cache_options,
            size,
            **kwargs,
        )
        kwargs_mixin = kwargs.copy()
        kwargs_mixin.update(
            {
                "fs": fs,
                "path": path,
                "mode": mode,
                "block_size": block_size,
                "autocommit": autocommit,
                "cache_type": cache_type,
                "cache_options": cache_options,
                "size": size,
            }
        )

        AsyncStreamedFileMixin._init__mixin(self, **kwargs_mixin)

    def write(self, data):
        if not self._write_called:
            self._init_write_append_mode()
        self._write_called = True
        return super().write(data)

    _init_write_append_mode = sync_wrapper(
        AsyncStreamedFileMixin._init_write_append_mode
    )

    ########################################################
    ## AbstractBufferedFile methods to implement or override
    ########################################################
    _upload_chunk = sync_wrapper(AsyncStreamedFileMixin._upload_chunk)
    _initiate_upload = sync_wrapper(AsyncStreamedFileMixin._initiate_upload)
    _fetch_range = sync_wrapper(AsyncStreamedFileMixin._fetch_range)


class MSGraphStreamedFile(AsyncStreamedFileMixin, AbstractAsyncStreamedFile):
    """A file-like object representing a file in a SharePoint drive.

    Parameters
    ----------
    fs: MSGDriveFS
        The filesystem this file is part of.
    path: str
        The path to the file.
    mode: str
        The mode to open the file in.
        One of 'rb', 'wb', 'ab'. These have the same meaning
        as they do for the built-in `open` function.
    block_size: int
        Buffer size for reading or writing, 'default' for class default
    autocommit: bool
            Whether to write to final destination; may only impact what
            happens when file is being closed.
    cache_type: {"readahead", "none", "mmap", "bytes"}, default "readahead"
        Caching policy in read mode. See the definitions in ``core``.
    cache_options : dict
        Additional options passed to the constructor for the cache specified
        by `cache_type`.
    size: int
        If given and in read mode, suppressed having to look up the file size
    kwargs:
        Gets stored as self.kwargs
    """

    def __init__(
        self,
        fs: MSGDriveFS,
        path: str,
        mode: str = "rb",
        block_size: int | None = None,
        autocommit: bool = True,
        cache_type: str = "readahead",
        cache_options: dict | None = None,
        size: int | None = None,
        **kwargs,
    ):
        AbstractAsyncStreamedFile.__init__(
            self,
            fs,
            path,
            mode,
            block_size,
            autocommit,
            cache_type,
            cache_options,
            size,
            **kwargs,
        )
        kwargs_mixin = kwargs.copy()
        kwargs_mixin.update(
            {
                "fs": fs,
                "path": path,
                "mode": mode,
                "block_size": block_size,
                "autocommit": autocommit,
                "cache_type": cache_type,
                "cache_options": cache_options,
                "size": size,
            }
        )

        AsyncStreamedFileMixin._init__mixin(self, **kwargs_mixin)

    async def write(self, data):
        if not self._write_called:
            await self._init_write_append_mode()
        self._write_called = True
        return await super().write(data)

    async def readinto(self, b):
        """Mirrors builtin file's readinto method.

        https://docs.python.org/3/library/io.html#io.RawIOBase.readinto
        """
        out = memoryview(b).cast("B")
        data = await self.read(out.nbytes)
        out[: len(data)] = data
        return len(data)
