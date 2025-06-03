# MSGraphFS

This python package is a [fsspec](https://filesystem-spec.readthedocs.io/) based filesystem-like interface to drives exposed through the Microsoft graph API (OneDrive, Sharepoint, etc).

see:
https://learn.microsoft.com/en-us/graph/api/resources/onedrive?view=graph-rest-1.0

## Usage

To use the Microsoft Drive filesystem (for exemple a sharepoint documents libraty), you need to create a new instance of the
`msgraphfs.MSGDriveFS` class. You can also use the `msgd` protocol to lookup the
class using `fsspec.get_filesystem_class`.

```python
import msgraphfs

fs = msgraphfs.MSGDriveFS(
    client_id="YOUR_CLIENT_ID",
    drive_id="YOUR_DRIVE_ID",
    oauth2_client_params = {...})

fs.ls("/")

with fs.open("/path/to/file.txt") as f:
    print(f.read())
```

```python

import fsspec

fs = fsspec.get_filesystem_class("msgd")(
    client_id="YOUR_CLIENT
    drive_id="YOUR_DRIVE_ID",
    oauth2_client_params = {...})

fs.ls("/")

```

### Specific functionalities

- `ls`, `info` : Both methods can take an `expand` additional argument. This
  argument is a string that will be passed as the `expand` query parameter to
  the microsoft graph API call used to get the file information. This can be
  used to get additional information about the file, such as the `thumbnails` or
  the `permissions` or ...

- `checkin`, `checkout` : These methods are used to checkin/checkout a file.
  They take the path of the file to checkin/checkout as argument. The `checking`
  method also take an additional `comment` argument.

- `get_versions` : This method returns the list of versions of a file. It takes
  the path of the file as argument.

- `preview` : This method returns a url to preview the file. It takes the
  path of the file as argument.

- `get_content` : This method returns the content of a file. It takes the path
  or the item_id of the file as argument. You can also give the `format` argument
  to specify the expected format of the content. It can be useful when converting a word document to a pdf.

In addition to the methods above, some methods can take an additional argument, `item_id`. This argument is the id of the drive item provided by the Microsoft
Graph API. It can be used to avoid making an additional API call to
get the item id or to store a reference to a drive item independently of the
path. (If the drive item is moved, the path will changed but the item id won't).

## Installation

```bash
pip install msgraphfs
```

### Get your drive id

To get the drive id of your drive, you can use the microsoft graph explorer:
https://developer.microsoft.com/en-us/graph/graph-explorer

The first step is to get the site id of your site. You can do this by making a
`GET` request to the following url:

```bash
https://graph.microsoft.com/v1.0/sites/{url}
```

where `{url}` is the url of your site without the protocol. For example, if your
site is `https://mycompany.sharepoint.com/sites/mysite`, you should use
`mycompany.sharepoint.com/sites/mysite` as the url.

In the response, you will find the `id` of the site.


Now, you can get your drive id by making a `GET` request to the following url:

```bash
  https://graph.microsoft.com/v1.0/sites/{site_id}/drives/
```

where `{site_id}` is the id of the site you got in the previous step.

## Development

To develop this package, you can clone the repository and install the
dependencies using pip:

```bash
git clone your-repo-url (a fork of https://github.com/acsone/msgraphfs)
pip install -e .
```

This will install the package in editable mode, so you can make changes to the
code and test them without having to reinstall the package every time.

To run the tests, you will need to install the test dependencies. You can achieve this by running:

```bash
pip install -e .[test]
```

Testing the package requires you to have access to a Microsoft Drive (OneDrive, Sharepoint, etc) and to have the `client_id`, `client_secret`, `tenant_id`, `dirve_id`, `site_name` and the user's
access token.

### How to get an access token required for testing

The first step is to get your user's access token.


### Prerequisites

- A registered Azure AD application with:
  - `client_id` and `client_secret`
  - Delegated permissions granted (e.g., `Files.ReadWrite.All`, `Sites.ReadWrite.All`)
  - A redirect URI configured (e.g., `http://localhost:5000/callback`)


#### 1. Build the OAuth2 authorization URL

Open the following URL in your browser (replace values as needed):

```bash
https://login.microsoftonline.com/<TENANT_ID>/oauth2/v2.0/authorize?
client_id=<CLIENT_ID>
&response_type=code
&redirect_uri=http://localhost:5000/callback
&response_mode=query
&scope=offline_access%20User.Read%20Files.ReadWrite.All%20Sites.ReadWrite.All
```

You will be asked to log in with your Microsoft account and to grant the requested permissions.

#### 2. Copy the Authorization Code

Once logged in, you'll be redirected to:

```bash
http://localhost:5000/callback?code=<AUTHORIZATION_CODE>
```

Copy the value of `code` from the URL.


### Launch the test suite

To run the test suite, you just need to run the pytest command in the root directory with the following arguments:

* --auth-code: The authorization code you got in the previous step. (It's only required if you launch the tests for the first time or if your refresh token is expired and you need to get a new access token)
* --client-id: The client id of your Azure AD application.
* --client-secret: The client secret of your Azure AD application.
* --tenant-id: The tenant id of your Azure AD application.
* --drive-id: The drive id of the drive you want to access.
* --site-name: The name of the site you want to access. (Only required for tests related to the access to the recycling bin)

```bash
pytest --auth-code <AUTH_CODE> \
       --client-id <CLIENT_ID> \
       --client-secret <CLIENT_SECRET> \
       --tenant-id <TENANT_ID> \
       --drive-id <DRIVE_ID> \
       --site-name <SITE_NAME> \
       tests
```

Alternatively, you can set the environment variables `MSGRAPHFS_AUTH_CODE`, `MSGRAPHFS_CLIENT_ID`, `MSGRAPHFS_CLIENT_SECRET`, `MSGRAPHFS_TENANT_ID`, `MSGRAPHFS_DRIVE_ID` and `MSGRAPHFS_SITE_NAME` to avoid passing the arguments to pytest.

When the auth-code is provided and we need to get the access token (IOW when it's the first time you run the tests or when your refresh token is expired), the package will automatically get the access token and store it
in a encrypted file into the keyring of your system. The call to the token endpoint requires a `redirect_uri` parameter. This one should match one of the redirect URIs you configured in your Azure AD application.
By default, it is set to `http://localhost:8069/microsoft_account/authentication`, but you can change it by setting the environment variable `MSGRAPHFS_AUTH_REDIRECT_URI` or by passing the `--auth-redirect-uri` argument to pytest.

### Pre-commit hooks

To ensure code quality, this package uses pre-commit hooks. You can install them by running:

```bash
pre-commit install
```
This will set up the pre-commit hooks to run automatically before each commit. You can also run them manually by executing:

```bash
pre-commit run --all-files
```
