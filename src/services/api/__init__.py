"""Local AI Hub API service (formerly Hub; compatibility shims remain).

V8 Wave 2 installs a process-local compatibility layer before API submodules
import Artifact Store callables. Historical uploads/artifacts remain readable;
new job-output publication is reservation-bound to V8 Output Authority.
"""

from src.services.artifact_access_v8 import install_v8_artifact_compatibility

install_v8_artifact_compatibility()

__all__ = []
