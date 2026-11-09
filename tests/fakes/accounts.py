"""The seeded Paperless users, shared by the fake (unit and tool tests) and the E2E seed.

Test-only credentials of the throwaway stack in tests/e2e/compose.yaml (the admin one is set
there); the fake uses the usernames and permissions only.
"""

ADMIN = ("admin", "e2e-admin-password")
READER = ("e2e-reader", "e2e-reader-password")
# The restricted user's global permissions: view the documents and taxonomy, and the UI settings
# every web UI user needs (they carry the current user and the AI flag).
READER_PERMISSIONS = [
    f"view_{model}"
    for model in (
        *("document", "tag", "correspondent", "documenttype", "storagepath", "customfield"),
        "uisettings",
    )
]
