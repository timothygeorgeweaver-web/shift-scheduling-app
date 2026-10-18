"""Compatibility entry point for the reviewed account-access migration."""

from scripts.apply_reviewed_migration import install as install_reviewed, require


def install(database, rehearse=False):
    return install_reviewed(
        "0007_account_access_management", database, rehearse=rehearse
    )
