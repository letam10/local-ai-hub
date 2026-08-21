# Runtime installation

Runtimes are separate from models and environments. A runtime plan lists
required leaves, dependency profile, source identity, download/staging bytes
and compatibility. Only reviewed portable archives or locked environment
profiles can become `AUTO_INSTALL_READY`; current FFmpeg is the reference
portable archive path. Python environments are staged under the managed root;
system Python, CUDA and NVIDIA drivers are never changed.

Presence is `INSTALLED_UNVERIFIED` until a bounded version/import/smoke receipt
matches the current leaf fingerprint.
