# Billy Parser

Community-maintained declarative parser definitions for [Billy](https://github.com/robin994/billy).

Billy downloads `parser.json` to discover available parsers and downloads a parser YAML only when a user installs it. Parsers cannot execute Python, JavaScript or shell commands and cannot perform network or filesystem operations.

## Parser lifecycle

Every parser lives in the normal country/provider/type tree and carries a `metadata.status` value:

- `experimental` — parser available for community testing;
- `verified` — parser confirmed by users on real bills;
- `outdated` — parser is obsolete or no longer matches the provider format.

There is no separate experimental directory. Promotion only changes `metadata.status`; the parser path and ID remain stable. `metadata.quality` is kept temporarily for compatibility with existing Billy clients, but it is no longer the lifecycle source of truth.

## Publish from Billy

Billy can generate a GitHub submission for a locally saved custom parser. The browser opens a pre-filled GitHub issue; after the user submits it, GitHub Actions validates the YAML and, if valid, publishes it automatically to `main` and rebuilds `parser.json`.

No invoice, email body or attachment is uploaded by Billy. Only the parser YAML is included in the submission.

### Ownership rules

- New parser: published as `experimental`, owner is taken from the GitHub issue author.
- Update: only the original GitHub owner can update that experimental parser.
- Version must increase.
- Experimental submissions cannot modify parsers whose status is `verified` or `outdated`.
- The workflow injects `metadata.status: experimental`, compatibility `metadata.quality: experimental`, and `metadata.submitted_by`; values supplied by the issue body are never trusted for promotion or ownership.

## Automatic validation

The publisher rejects malformed schema, unsafe/invalid regexes, duplicate IDs, path mismatches, static values in sensitive fields, unauthorized updates and oversized submissions.

Repository layout:

```text
parsers/<country>/<provider>/<type>.yaml
schema/parser.schema.json
scripts/build_catalog.py
scripts/publish_submission.py
parser.json
```

For normal pull-request development:

```bash
python -m pip install -r requirements-dev.txt
python scripts/build_catalog.py --check
pytest -q
```

## Repository setup required

For automatic community publishing, GitHub Actions must be allowed to write repository contents and issues. If `main` is protected, the `github-actions[bot]` identity must be allowed to bypass the rule for these workflow pushes; otherwise the submission will validate but cannot be published automatically.
