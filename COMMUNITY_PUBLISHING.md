# Community publishing and verification

Billy Parser is designed for an unattended community pipeline. Normal parser submissions and feedback do not require maintainer review.

## Workflows

- `.github/workflows/community-submission.yml` processes `billy-parser-submission:v2` issues.
- `.github/workflows/community-feedback.yml` processes `billy-parser-feedback:v1` issues.
- `.github/workflows/build-catalog.yml` rebuilds generated catalogs for direct repository changes.
- `.github/workflows/validate.yml` runs source validation and tests.

Community write workflows share the concurrency group `billy-parser-community-writes` with `cancel-in-progress: false` so parser, feedback and generated catalog writes are serialized.

## Submission flow

1. Billy creates an issue containing `<!-- billy-parser-submission:v2 -->`.
2. `scripts/publish_submission.py` extracts the JSON envelope and YAML strictly as data.
3. The JSON is checked against `schema/submission.schema.json`.
4. The YAML is checked against `schema/parser.schema.json` and the repository semantic/regex/privacy validator.
5. Envelope identity must match the YAML (`parser_id`, version, country, provider and bill type).
6. The repository path is derived from the validated parser id; user-provided paths are never used.
7. `metadata.status` and compatibility `metadata.quality` are forced to `experimental`.
8. A new parser is created, or an existing parser is replaced only when the submitted version is higher.
9. Tests run and Catalog v2 plus legacy `parser.json` are regenerated.
10. The workflow attempts a direct push. If branch protection rejects it, it creates a bot branch/PR and enables auto-merge after CI.
11. The issue receives `parser-submission` plus `community-accepted` or `community-rejected`, gets a result comment and is closed.

A newer version of a previously verified parser intentionally returns to `experimental`. Feedback files for older versions remain historical and are not transferred.

## Feedback flow

1. Billy creates an issue containing `<!-- billy-parser-feedback:v1 -->`.
2. `scripts/process_feedback.py` validates the JSON against `schema/feedback.schema.json`.
3. The SHA-256 fingerprint, parser id and current parser version are verified.
4. One fingerprint has one vote for one parser/version. Sending a new result with the same fingerprint replaces the previous vote.
5. Votes are stored under `feedback/<country>/<provider>/<type>/v<version>.json`.
6. Aggregate counts are recalculated and automatic verification policy is evaluated.
7. If required, the parser YAML changes between `experimental` and community-verified `verified`.
8. Tests and catalog generation run, changes are committed, and the issue is labelled/commented/closed.

The feedback store can contain fingerprints; generated catalogs never do. `build_catalog.py` reads feedback and exports only `working`, `partial`, `failed` and `contributors` counts.

## Automatic verification policy

The thresholds live only in `scripts/community_policy.py`:

```text
MIN_DISTINCT_VOTES = 5
MIN_WORKING_VOTES = 5
MIN_WORKING_RATIO = 0.80
```

Examples:

```text
5 working, 0 partial, 0 failed -> verified
5 working, 1 partial, 0 failed -> verified
5 working, 0 partial, 2 failed -> experimental
```

Community-promoted verification is recalculated if later votes change the ratio. Existing trusted parsers that were not promoted through community feedback are not automatically demoted.

## Security model

Issue bodies are untrusted input. The Python processors never evaluate or execute issue content. They do not accept arbitrary paths and do not interpolate issue bodies into shell commands.

The pipeline rejects invalid JSON/YAML, unsupported parser schema, malformed ids/countries/versions, unsafe paths, invalid or dangerous regexes, sensitive static values and stale/same-version parser replacements.

Billy sends no invoice, PDF, email body, amount, POD/PDR, customer name, address or original community id.

## Repository settings

Enable read/write workflow permissions for `GITHUB_TOKEN`. If `main` rejects direct bot pushes, enable GitHub auto-merge and ensure branch rules allow the bot PR to merge after CI without a human review requirement.

No repository secret is required by Billy clients.
