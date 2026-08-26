# Community experimental publishing

The workflow `.github/workflows/publish-experimental.yml` turns a GitHub issue containing the Billy submission marker into an automatically published **experimental** parser.

## One-time GitHub repository settings

1. Open **Settings → Actions → General → Workflow permissions**.
2. Enable **Read and write permissions** for `GITHUB_TOKEN` if the repository policy currently restricts it.
3. If `main` is protected by a branch rule/ruleset, allow GitHub Actions to bypass that rule for this repository, or exempt the `github-actions[bot]` workflow identity from the direct-push restriction.
4. Create the optional label `parser-submission` if you want submitted issues visually grouped. The workflow itself does not depend on the label.

No repository secret, PAT, OAuth client secret or GitHub App private key is required.

## Flow

1. Billy exports a locally stored custom parser.
2. Billy opens `robin994/billy-parser/issues/new` with the YAML prefilled and the marker `billy-parser-submission:v1`.
3. The contributor submits the issue using their normal GitHub session.
4. GitHub Actions validates the issue author and YAML.
5. A new parser is forced to `metadata.status: experimental`; `metadata.quality: experimental` is also written for compatibility and `metadata.submitted_by` is set from the issue author.
6. Promotion changes only `metadata.status` to `verified`; obsolete parsers become `outdated`. The parser stays in the same country/provider/type path.
6. The parser is committed to `main`.
7. `parser.json` is rebuilt with `source_commit` pinned to the immutable parser commit SHA.
8. The bot comments on and closes the issue.

If validation fails, the bot comments with the reason. Editing the issue automatically retries validation.

## Protected operations

The automatic channel cannot:

- update `tested` or `verified` parsers;
- update another contributor's experimental parser;
- lower/reuse an existing version;
- choose its own owner;
- promote itself above experimental;
- store static values for sensitive fields such as invoice/customer/POD/PDR/account identifiers.
