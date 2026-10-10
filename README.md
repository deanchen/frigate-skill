# Frigate skill for Codex

Search Frigate events and clips, use semantic search, compare detection and
embedding models, and manage cameras with verified API behavior.

The skill is in [`frigate/SKILL.md`](frigate/SKILL.md). It includes a standard-
library Python client with HTTPS/JWT or SSH loopback transport, selected config
and stats output, private media downloads, camera toggles, and evaluation tools.
API guidance was checked against Frigate `0.18.0-77a66e7`; refresh it when
upgrading. Model evaluation and camera changes are opt-in user tasks.

Install by copying the `frigate` directory to `~/.codex/skills/frigate`, or use
Codex's skill installer with this repository and the `frigate` path. Invoke
`$frigate` or ask Codex to search Frigate footage. Normal automatic discovery
is enabled. Python 3.9+ is required; no pip dependencies for the helpers.

```sh
python3 frigate/scripts/frigate_api.py --help
python3 frigate/scripts/frigate_api.py --ssh [username]@frigate-host status
python3 frigate/scripts/frigate_api.py --ssh [username]@frigate-host search --query "cat on the stairs" --label cat --limit 10
python3 -m unittest discover -s tests -v
```

For authenticated HTTPS, supply `--base-url` and `--token-file` (private JWT
file, mode 0600). SSH uses the existing account/agent and server-side Python
to contact the loopback API; no remote API exposure or Frigate changes are
needed. Use existing SSH configuration for authentication. Credentials, camera
footage, model binaries, embeddings and evaluation data stay outside this repo.

For a fresh JWT, `login --credentials-file /private/login.json --output
/private/frigate.jwt` accepts private JSON with `user` and `password`, and
extracts the login response cookie without printing it. Remove the credentials
file after login. Expired JWTs require login again.
