"""Pre-release checks: public-release safety scanning and archive verification.

These run before the repository is made public and before an artifact is handed to the
coordinator. They exist because the hackathon rules require a public repository with no
committed secrets, and because a build that silently drops a file is only discovered by whoever
installs it.
"""
