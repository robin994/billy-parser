# Billy Parser

Community-maintained declarative parser definitions for [Billy](https://github.com/robin994/billy).

Billy discovers parsers through generated country catalogs and downloads a parser YAML only when a user installs or updates it. Parsers are declarative: they cannot execute Python, JavaScript or shell commands and cannot perform network or filesystem operations.

## Community lifecycle

```text
Custom -> Shared -> Experimental -> Community feedback -> Verified
```

There is no mandatory maintainer review in this flow. Billy opens machine-readable GitHub issues, GitHub Actions validates them, and valid submissions/feedback are committed automatically. If branch protection blocks a direct push, the workflow creates a bot branch and enables auto-merge after CI without requesting human review.

Every parser lives in the normal country/provider/type tree and carries `metadata.status`:

- `experimental` - available for community testing;
- `verified` - currently meets the community verification policy or is an existing trusted parser;
- `outdated` - obsolete or no longer matching the provider format.

`metadata.status` is the lifecycle source of truth. `metadata.quality` is written only for backward compatibility.

A new version of an existing parser is always returned to `experimental`. Feedback is version-scoped, so votes from v1 never count toward v2.

## Automatic verification

Feedback results are `working`, `partial`, or `failed`. The current policy is centralized in `scripts/community_policy.py`:

- at least 5 distinct voters;
- at least 5 `working` votes;
- `working / total >= 0.80`.

`partial` and `failed` both count in the denominator. Community-promoted parsers are recalculated as feedback changes; if their ratio later drops below the policy, that version returns to `experimental`. Existing trusted parsers such as E.ON/TIM are not demoted by absence of community feedback.

## Privacy

Billy never sends invoice PDFs, email contents, amounts, POD/PDR, customer names, addresses, or the original community identifier.

Feedback contains only an already-anonymized SHA-256 installation fingerprint. It is used solely to deduplicate one vote per installation for one parser/version. Fingerprints are stored only in `feedback/` and are never copied into public catalog entries. Catalogs expose aggregate counts only.

## Catalog v2

The scalable catalog is split by country:

```text
catalog/index.json
catalog/it.json
catalog/fr.json
...
```

`catalog/index.json` contains only available country shards and parser counts. Billy reads the index and downloads only the shard for its country. It does not crawl GitHub and does not download parser YAML files during discovery.

Each parser entry can expose aggregate feedback:

```json
{
  "id": "it.heracomm.energy",
  "version": 1,
  "status": "experimental",
  "feedback": {
    "working": 3,
    "partial": 1,
    "failed": 0,
    "contributors": 4
  }
}
```

The root `parser.json` is still generated for legacy Billy clients.

## Submission v2

Billy creates issues containing exactly one marker:

```text
<!-- billy-parser-submission:v2 -->
```

followed by the submission JSON and a fenced YAML parser. The pipeline validates both schemas, parser identity/version/country/provider/type consistency, safe paths, regexes, parser schema compatibility and semantic/privacy rules.

Valid new parsers are created automatically. Existing parsers can be replaced only by a strictly higher version. The workflow forces:

```yaml
metadata:
  status: experimental
  quality: experimental
```

No separate experimental directory exists.

## Feedback v1

Billy creates issues containing exactly one marker:

```text
<!-- billy-parser-feedback:v1 -->
```

The workflow validates the feedback JSON, verifies the parser/current version, deduplicates by SHA-256 fingerprint, updates aggregate state, applies automatic promotion/demotion when appropriate, rebuilds catalogs, comments the result and closes the issue.

Feedback storage is versioned independently:

```text
feedback/<country>/<provider>/<type>/v<version>.json
```

## Repository layout

```text
parsers/<country>/<provider>/<type>.yaml
feedback/<country>/<provider>/<type>/v<version>.json
catalog/index.json
catalog/<country>.json
schema/parser.schema.json
schema/submission.schema.json
schema/feedback.schema.json
scripts/build_catalog.py
scripts/publish_submission.py
scripts/process_feedback.py
scripts/community_policy.py
parser.json
```

## Development

```bash
python -m pip install -r requirements-dev.txt
python scripts/build_catalog.py --check
pytest -q
```

## GitHub repository setup

Actions needs `contents: write` and `issues: write`. `pull-requests: write` is used only for the branch-protection fallback.

For a fully unattended pipeline, either allow `github-actions[bot]` to push to `main`, or enable repository auto-merge and configure branch protection so the bot PR can merge after CI without required human reviews. No client secret, PAT, OAuth secret or private key is stored in Billy.
