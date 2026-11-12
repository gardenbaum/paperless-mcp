"""The seeded Paperless users, shared by the fake (unit and tool tests) and the E2E seed.

Test-only credentials of the throwaway stack in tests/e2e/compose.yaml (the admin one is set
there); the fake uses the usernames and permissions only.
"""

ADMIN = ("admin", "e2e-admin-password")
READER = ("e2e-reader", "e2e-reader-password")
BLIND = ("e2e-blind", "e2e-blind-password")
# The restricted user's global permissions: view the documents and taxonomy, and the UI settings
# every web UI user needs (they carry the current user and the AI flag).
READER_PERMISSIONS = [
    f"view_{model}"
    for model in (
        *("document", "tag", "correspondent", "documenttype", "storagepath", "customfield"),
        "uisettings",
    )
]
# The blind user holds only the UI settings permission: it can sign in and load the web UI's
# settings but lacks every model permission, notably `view_document` (document search is a 403).
BLIND_PERMISSIONS = ["view_uisettings"]
# The seeded documents' archive serial numbers. Deliberately 2 < 10 in the opposite order of
# their string forms, and with the public document first, so only a numeric sort passes.
PUBLIC_ASN, PRIVATE_ASN = 2, 10
# The private document's text. It repeats "2026" (which the public document also holds) so the
# word outranks the private document's unique terms in Paperless's more-like-this query, which
# keeps only the 12 best-scoring terms; without it, extra indexed fields such as the ASN push the
# one shared term out and live Paperless finds no similar document.
PRIVATE_CONTENT = "E2E private admin only note 2026 2026 2026 2026"
