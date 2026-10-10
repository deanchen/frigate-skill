# Frigate skill maintenance

The `frigate/` directory is the installable skill. Keep personal hostnames,
accounts, camera inventory, storage paths and activity examples outside this
public distribution, in private deployment-owned operations notes. Prefer API discovery
against the installed version to assumed routes from another release.

Helpers use Python 3.9+ standard library. Run `python3 -m unittest discover -s
tests -v` and the Codex skill-creator `quick_validate.py frigate` after behavior
or instruction changes. Meaningful tests should cover protocol/credential
boundaries and metric correctness. Never include real footage, tokens, camera
credentials, cookies, private keys or runtime databases in commits/test fixtures.

Scope changes to the requested Frigate task. Preserve live configs and media;
API test mutations require the user's task authorization. Offline evaluations
must not replace or reindex the production embedding database implicitly.
