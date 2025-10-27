import fsspec

from .core import (
    MSGDriveFS,
    MSGraphBufferedFile,
    MSGraphStreamedFile,
    parse_msgraph_url,
)

# Register MSGDriveFS for all supported protocols
# Use clobber=True to allow re-registration
fsspec.register_implementation("msgd", MSGDriveFS, clobber=True)
fsspec.register_implementation("sharepoint", MSGDriveFS, clobber=True)
fsspec.register_implementation("onedrive", MSGDriveFS, clobber=True)
