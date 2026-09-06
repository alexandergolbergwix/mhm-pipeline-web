# Publication credentials

> Up: [Wikidata Studio](README.md)

Publication uses the signed-in account's saved `wikidata` credential for production.
It uses the saved `wikidata_test` credential for test.wikidata.org.
The dry-run decrypts only that account's target-specific credential through the request KEK.
The service never uses the test credential for production.
An unreadable saved credential fails with a Settings instruction.
If no saved credential exists for the target, the existing server token remains a fallback.

Publish and Resume require the signed-in account again.
The route validates the run and Publication before it resolves the credential.
It encrypts an execution grant under the existing server master key.
The grant contains the credential reference, wiki, Publication ID, Execution ID, expiry, and token.
The grant expires after 24 hours.
The private job parameter `_publication_credential` contains only the encrypted grant.
Public job responses exclude this parameter.
No KEK, session cookie, or plaintext token enters job parameters, plans, or audit rows.

The worker checks the job creator, account, wiki, Publication, Execution, and expiry before it opens the credential.
An invalid or expired grant fails closed.
The user can Resume from a signed-in session to issue a fresh grant.
Old jobs without a grant retain the server-token path.
The saved API key remains encrypted under the user's KEK; the grant is a separate authorized worker copy.

Tests: `backend/tests/test_publication_saved_credentials.py` checks target separation, account binding, encryption, expiry, and invalid grants.
`backend/tests/test_publication_router.py` checks dry-run and worker execution for both targets through a test Wikidata boundary.
The route test also checks that job responses hide the encrypted grant.
