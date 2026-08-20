"""Server-owned component installation primitives.

The package keeps browser input to opaque component/plan/selection IDs.  It
does not install anything at import or startup; callers must create a plan and
explicitly confirm it.
"""

from .archive import ArchiveSafetyError, safe_extract_archive
from .downloader import DownloadError, DownloadResult, TrustedDownloader
from .manager import ComponentInstaller, SelectionError
from .bundle import ComponentBundleService
from .reuse_executor import ExistingInstallReuseExecutor
from .policy import INSTALL_JOB_STATES, InstallPlanError, plan_fingerprint, trusted_source

__all__ = [
    "ArchiveSafetyError",
    "ComponentInstaller",
    "ComponentBundleService",
    "DownloadError",
    "DownloadResult",
    "INSTALL_JOB_STATES",
    "InstallPlanError",
    "SelectionError",
    "ExistingInstallReuseExecutor",
    "TrustedDownloader",
    "plan_fingerprint",
    "safe_extract_archive",
    "trusted_source",
]
