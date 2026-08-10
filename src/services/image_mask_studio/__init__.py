"""Non-destructive Image & Mask Studio contracts.

This package owns small, local declarative editing sessions only.  Pixels stay
in the Artifact Store and all browser-visible references remain opaque IDs.
"""

from .manager import ImageMaskStudioManager, StudioConflictError, image_mask_studio

__all__ = ["ImageMaskStudioManager", "StudioConflictError", "image_mask_studio"]
