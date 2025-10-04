Filesystem interface to Microsoft Graph API (SharePoint, OneDrive)
------------------------------------------------------------

[![PyPI version shields.io](https://img.shields.io/pypi/v/msgraphfs.svg)](https://pypi.python.org/pypi/msgraphfs/)

Quickstart
----------

This package can be installed using:

`pip install msgraphfs`

or

`uv add msgraphfs`

The `msgd://`, `sharepoint://`, and `onedrive://` protocols are included in fsspec's known_implementations registry, allowing seamless integration with fsspec-compatible libraries.

To use the filesystem with specific site and drive:

```python
import pandas as pd

storage_options = {
    'client_id': 'your-client-id',
    'tenant_id': 'your-tenant-id',
    'client_secret': 'your-client-secret',
    'site_name': 'YourSiteName',
    'drive_name': 'Documents'
}

df = pd.read_csv('msgd://folder/data.csv', storage_options=storage_options)
```

To use multi-site mode where site and drive are specified in the URL:

```python
import pandas as pd

storage_options = {
    'client_id': 'your-client-id',
    'tenant_id': 'your-tenant-id',
    'client_secret': 'your-client-secret'
}

df = pd.read_csv('msgd://YourSite/Documents/folder/data.csv', storage_options=storage_options)
df = pd.read_parquet('sharepoint://AnotherSite/Reports/data.parquet', storage_options=storage_options)
```

Accepted protocol / uri formats include:
- `msgd://site/drive/path/file` (multi-site mode)
- `sharepoint://site/drive/path/file` (multi-site mode)
- `onedrive://drive/path/file` (OneDrive personal)
- `msgd://path/file` (single-site mode when site_name and drive_name specified in storage_options)

To read files, you can optionally set the `MSGRAPHFS_CLIENT_ID`, `MSGRAPHFS_TENANT_ID`, and `MSGRAPHFS_CLIENT_SECRET` environment variables, then storage_options will be read from the environment:

```python
import pandas as pd

# With environment variables set, you can omit credentials from storage_options
storage_options = {'site_name': 'YourSite', 'drive_name': 'Documents'}
df = pd.read_csv('msgd://folder/data.csv', storage_options=storage_options)
```

Details
-------

The package provides a pythonic filesystem implementation for Microsoft Graph API drives (SharePoint and OneDrive), facilitating interactions between Microsoft 365 services and data processing libraries like Pandas, Dask, and others. This is implemented using the [fsspec](https://filesystem-spec.readthedocs.io/) base class and Microsoft Graph Python SDK.

Operations work with Azure AD application credentials using the client credentials flow, suitable for server-to-server authentication scenarios.

The filesystem automatically handles OAuth2 token management, site and drive discovery, and provides fork-safe lazy initialization perfect for multi-process environments like Apache Airflow.

### Setting credentials

The `storage_options` can be instantiated with the following authentication parameters:

**Required for authentication:**
- `client_id`: Azure AD application (client) ID
- `tenant_id`: Azure AD directory (tenant) ID
- `client_secret`: Azure AD application client secret

**Optional filesystem parameters:**
- `site_name`: SharePoint site name (for single-site mode or site discovery)
- `drive_name`: Drive/library name (e.g., "Documents", "CustomLibrary")
- `drive_id`: Specific drive ID (bypasses site/drive discovery)
- `oauth2_client_params`: Pre-built OAuth2 parameters dict
- `use_recycle_bin`: Enable recycle bin operations (default: False)

For more details on all available parameters, see the [MSGDriveFS documentation](https://github.com/your-repo/msgraphfs).

The following environment variables can be set and will be automatically detected:
- `MSGRAPHFS_CLIENT_ID` (or `AZURE_CLIENT_ID` as fallback)
- `MSGRAPHFS_TENANT_ID` (or `AZURE_TENANT_ID` as fallback)
- `MSGRAPHFS_CLIENT_SECRET` (or `AZURE_CLIENT_SECRET` as fallback)

### Usage modes

The filesystem can be used in different modes based on the `storage_options` provided:

1. **Single-site mode**: Specify `site_name` and `drive_name` in storage_options, then use relative paths in URLs:
   ```python
   storage_options = {
       'client_id': CLIENT_ID,
       'tenant_id': TENANT_ID,
       'client_secret': CLIENT_SECRET,
       'site_name': 'YourSite',
       'drive_name': 'Documents'
   }
   df = pd.read_csv('msgd://folder/file.csv', storage_options=storage_options)
   ```

2. **Multi-site mode**: Omit `site_name` and `drive_name` from storage_options, specify them in the URL:
   ```python
   storage_options = {
       'client_id': CLIENT_ID,
       'tenant_id': TENANT_ID,
       'client_secret': CLIENT_SECRET
   }
   df = pd.read_csv('msgd://YourSite/Documents/folder/file.csv', storage_options=storage_options)
   ```

3. **Direct drive access**: Use `drive_id` to bypass site discovery:
   ```python
   storage_options = {
       'client_id': CLIENT_ID,
       'tenant_id': TENANT_ID,
       'client_secret': CLIENT_SECRET,
       'drive_id': 'specific-drive-id'
   }
   df = pd.read_csv('msgd://folder/file.csv', storage_options=storage_options)
   ```

### Advanced features

#### File operations with metadata
```python
import fsspec

fs = fsspec.filesystem('msgd', **storage_options)

# List files with detailed metadata
files = fs.ls('/folder', detail=True)

# Get file information with permissions
info = fs.info('/document.pdf', expand='permissions')

# Read file with version control
with fs.open('/document.docx', mode='r') as f:
    content = f.read()
```

#### Permission management
```python
# Get detailed permissions for files and folders
permissions = fs.get_permissions('/sensitive-folder')
print(f"Total permissions: {permissions['summary']['total_permissions']}")
```

#### Integration with data processing libraries
```python
import dask.dataframe as dd

# Read multiple CSV files using Dask
ddf = dd.read_csv('msgd://YourSite/Data/*.csv', storage_options=storage_options)

# Read Parquet files
ddf = dd.read_parquet('sharepoint://Reports/Analytics/data.parquet', storage_options=storage_options)
```

### Azure AD Setup

To use this filesystem, you need to register an Azure AD application:

1. Go to the [Azure Portal](https://portal.azure.com)
2. Register a new application under "Azure Active Directory" > "App registrations"
3. Configure API permissions (Application permissions). Choose based on your needs:
   - For read-only access: `Sites.Read.All`
   - For read-write access: `Sites.ReadWrite.All`
   - Optional for enhanced functionality: `Files.Read.All` or `Files.ReadWrite.All`
4. Grant admin consent for your organization
5. Create a client secret
6. Note the Application (client) ID, Directory (tenant) ID, and client secret

The filesystem uses the OAuth2 client credentials flow with the default scope (`https://graph.microsoft.com/.default`), which automatically includes all application permissions granted to your Azure AD application.
