# Testing Guide

This document explains how to run tests for msgraphfs.

## Installing test dependencies

```bash
pip install -e ".[test]"
# or
uv sync --extra test
```

## Running Tests

### Basic Tests (No Credentials Required)

To run the basic test suite that doesn't require real SharePoint credentials:

```bash
pytest
# or explicitly skip live tests
pytest -m "not live"
```

Tests that need a real SharePoint site ("live" tests) are automatically skipped when no credentials are provided, so the command above runs fine out of the box (you'll see the live tests reported as `skipped`).

These tests cover:
- Unit tests for OAuth2 functionality
- URL parsing tests
- fsspec integration tests
- Mock-based tests for filesystem operations

### Live Tests (Credentials Required)

The live tests exercise the real Microsoft Graph API, so they need their own Azure AD application and a SharePoint site/drive to run against. They authenticate through an interactive OAuth2 "authorization code" flow; once you've done this once, the tokens are cached in your OS keyring and refreshed automatically, so you normally only need to obtain an authorization code the very first time (or after revoking access).

#### 1. Register/reuse an Azure AD application

Follow the [Azure AD Setup](README.md#azure-ad-setup) steps in the README, with two additions that are only needed for the test suite (the client-credentials flow used by the library itself doesn't require them):

- Under "Authentication", add a "Web" platform with the redirect URI you'll use for testing (e.g. `http://localhost:8069`).
- Under "API permissions", also add the **Delegated** permissions `offline_access`, `openid`, `Files.ReadWrite.All`, and `Sites.ReadWrite.All`, and grant admin consent.

Note the Application (client) ID, Directory (tenant) ID, and a client secret, as described in the README.

#### 2. Identify the site and drive to test against

- `site_name`: the display name of the SharePoint site to use as a sandbox.
- `drive_id`: the ID of the document library/drive to run the tests against. The tests create and delete a temporary folder in it, so use a non-production drive. You can find the ID with the [Graph Explorer](https://developer.microsoft.com/graph/graph-explorer): sign in and call `GET https://graph.microsoft.com/v1.0/sites/{hostname}:/sites/{site_name}` to get the site ID, then `GET https://graph.microsoft.com/v1.0/sites/{site-id}/drives` to list its drives and copy the `id` of the one you want.

#### 3. Get an authorization code

Open the following URL in a browser (replacing `{tenant_id}`, `{client_id}`, and `{redirect_uri}`), sign in with an account that has access to the site, and accept the consent screen:

```text
https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/authorize?client_id={client_id}&response_type=code&redirect_uri={redirect_uri}&response_mode=query&scope=offline_access%20openid%20Files.ReadWrite.All%20Sites.ReadWrite.All
```

The browser is redirected to `{redirect_uri}?code=...&session_state=...` (that page doesn't need to actually load). The URL contains several query parameters — copy only the value of the `code` parameter (not `session_state` or anything after the following `&`): this is your `auth_code`. It is single-use and only valid for a few minutes, so use it right away.

#### 4. Run the tests

Pass the parameters as command line options:

```bash
pytest -m "live" \
  --client-id=<client_id> \
  --client-secret=<client_secret> \
  --tenant-id=<tenant_id> \
  --site-name=<site_name> \
  --drive-id=<drive_id> \
  --auth-code=<auth_code> \
  --auth-redirect-uri=<redirect_uri>
```

or, equivalently, as environment variables:

```bash
export MSGRAPHFS_CLIENT_ID=<client_id>
export MSGRAPHFS_CLIENT_SECRET=<client_secret>
export MSGRAPHFS_TENANT_ID=<tenant_id>
export MSGRAPHFS_SITE_NAME=<site_name>
export MSGRAPHFS_DRIVE_ID=<drive_id>
export MSGRAPHFS_AUTH_CODE=<auth_code>
pytest -m "live"
```

Command line options take precedence over environment variables.

| CLI option | Environment variable | Required | Description |
| --- | --- | --- | --- |
| `--client-id` | `MSGRAPHFS_CLIENT_ID` | yes | Azure AD application (client) ID |
| `--client-secret` | `MSGRAPHFS_CLIENT_SECRET` | yes | Azure AD application client secret |
| `--tenant-id` | `MSGRAPHFS_TENANT_ID` | yes | Azure AD directory (tenant) ID |
| `--site-name` | `MSGRAPHFS_SITE_NAME` | yes | SharePoint site to run the tests against |
| `--drive-id` | `MSGRAPHFS_DRIVE_ID` | yes | ID of the drive/library used as the test sandbox |
| `--auth-code` | `MSGRAPHFS_AUTH_CODE` | only when there is no valid cached/refreshable token | Authorization code obtained in step 3 |
| `--auth-redirect-uri` | `MSGRAPHFS_AUTH_REDIRECT_URI` | no (defaults to `http://localhost:8069`) | Redirect URI registered on the Azure AD app; must match the one used to obtain the authorization code |

Once a run succeeds, the tokens are stored in your OS keyring (service `msgraph-token-<tenant_id>`, user `<client_id>`) and refreshed automatically on later runs, so `--auth-code` / `MSGRAPHFS_AUTH_CODE` can be dropped afterwards unless the refresh token itself expires or is revoked.

### Running All Tests

To run both basic and live tests (if credentials are available):

```bash
pytest tests/
```

## Test Structure

- `tests/test_oauth2.py` - OAuth2 authentication tests (no credentials required)
- `tests/test_fsspec_integration.py` - fsspec integration tests (no credentials required)
- `tests/test_url_parsing.py` - URL parsing tests (no credentials required)
- `tests/test_read.py` - File reading tests (credentials required via fixtures)
- `tests/test_write.py` - File writing tests (credentials required via fixtures)
- `tests/test_live_url_features.py` - Live URL feature tests (marked with `@pytest.mark.live`)
- `tests/conftest.py` - Shared fixtures and CLI options (`--client-id`, `--client-secret`, `--tenant-id`, `--site-name`, `--drive-id`, `--auth-code`, `--auth-redirect-uri`)

## Continuous Integration

The GitHub Actions workflow automatically:
- Runs basic tests on all Python versions (3.9-3.12) for every PR/push
- Runs live tests only on the main branch and only if credentials are configured
- Skips live tests gracefully if credentials are not available

## Test Markers

- `@pytest.mark.live` - Tests that require real SharePoint credentials
- `@pytest.mark.credentials` - Tests that require credentials (reserved for future use)

## Configuration

Test configuration is defined in `pyproject.toml`:

```toml
[tool.pytest.ini_options]
markers = [
    "live: marks tests as requiring live credentials (deselect with '-m \"not live\"')",
    "credentials: marks tests as requiring credentials (deselect with '-m \"not credentials\"')",
]
```
