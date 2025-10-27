# Testing Guide

This document explains how to run tests for msgraphfs.

## Running Tests

### Basic Tests (No Credentials Required)

To run the basic test suite that doesn't require real SharePoint credentials:

```bash
uv run pytest
# or explicitly skip live tests
uv run pytest -m "not live"
```

These tests cover:
- Unit tests for OAuth2 functionality
- URL parsing tests
- fsspec integration tests
- Mock-based tests for filesystem operations

### Live Tests (Credentials Required)

To run tests that require real SharePoint credentials:

```bash
uv run pytest -m "live"
```

**Prerequisites:**
Set the following environment variables:
- `MSGRAPHFS_CLIENT_ID`: Your Azure AD application client ID
- `MSGRAPHFS_TENANT_ID`: Your Azure AD tenant ID
- `MSGRAPHFS_CLIENT_SECRET`: Your Azure AD application client secret

**Important:** Live tests will be automatically skipped if credentials are not provided.

### Running All Tests

To run both basic and live tests (if credentials are available):

```bash
uv run pytest tests/
```

## Test Structure

- `tests/test_oauth2.py` - OAuth2 authentication tests (no credentials required)
- `tests/test_fsspec_integration.py` - fsspec integration tests (no credentials required)
- `tests/test_url_parsing.py` - URL parsing tests (no credentials required)
- `tests/test_read.py` - File reading tests (credentials required via fixtures)
- `tests/test_write.py` - File writing tests (credentials required via fixtures)
- `tests/test_live_url_features.py` - Live URL feature tests (marked with `@pytest.mark.live`)

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
