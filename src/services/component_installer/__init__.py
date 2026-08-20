"""Server-owned component installation primitives.

The package keeps browser input to opaque component/plan/selection IDs.  It
does not install anything at import or startup; callers must create a plan and
explicitly confirm it.
"""

from .archive import ArchiveSafetyError, safe_extract_archive
from .downloader import DownloadError, DownloadResult, TrustedDownloader
from .manager import ComponentInstaller, SelectionError
from .policy import INSTALL_JOB_STATES, InstallPlanError, plan_fingerprint, trusted_source

__all__ = [
    "ArchiveSafetyError",
    "ComponentInstaller",
    "DownloadError",
    "DownloadResult",
    "INSTALL_JOB_STATES",
    "InstallPlanError",
    "SelectionError",
    "TrustedDownloader",
    "plan_fingerprint",
    "safe_extract_archive",
    "trusted_source",
]
