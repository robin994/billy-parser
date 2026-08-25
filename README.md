# Billy Parser

Community-maintained parser definitions for [Billy](https://github.com/robin994/billy).

Billy downloads only `parser.json` to discover available parsers. Parser YAML files are downloaded only when a user explicitly installs one. Parsing is declarative: parsers cannot execute Python, JavaScript, shell commands, network requests, or access the filesystem.

## Repository layout

```text
parsers/<country>/<provider>/<parser>.yaml
schema/parser.schema.json
scripts/build_catalog.py
templates/parser.template.yaml
tests/fixtures/<provider>/{email,invoice}.txt
parser.json
```

## Quick start: add a parser

1. Copy the template (or an existing parser) and keep the parser ID globally unique:

   ```bash
   cp templates/parser.template.yaml parsers/<country>/<provider>/<bill_type>.yaml
   ```

2. Fill in `metadata`, `prefilter`, `detection`, `documents`, `fields` (see the
   detailed guide below, and the heavily commented `templates/parser.template.yaml`).
3. Add synthetic/anonymized fixtures under `tests/fixtures/<provider>/email.txt`
   and `tests/fixtures/<provider>/invoice.txt` so reviewers can check your
   regexes against realistic text. **Never commit real invoices or personal
   emails** — invent plausible names, addresses, amounts and IDs instead.
4. Validate:

   ```bash
   python -m pip install -r requirements-dev.txt
   python scripts/build_catalog.py --check
   ```

5. Open a pull request.

GitHub Actions validates every parser and regenerates `parser.json` on `main`.
You never need to hand-edit or commit `parser.json` yourself.

## Parser model

Parsing is fully declarative (no code execution, no network access, no
filesystem access) and runs in three phases, in order:

1. **`prefilter`** — a privacy-first, metadata-only check (sender address,
   subject) evaluated *before* Billy fetches the email body or any
   attachment. Its only job is to decide whether fetching the rest is worth it.
2. **`detection`** — weighted rules evaluated once the body/attachments are
   available, to confirm the provider/bill type and pick this parser over others.
3. **`fields`** — regex-based extraction of structured data (amounts, dates,
   invoice numbers, …) from the email body and/or extracted attachment text.

See `parsers/it/eon/electricity.yaml` and `parsers/it/tim/internet.yaml` for
real-world reference implementations, and `schema/parser.schema.json` for the
authoritative schema.

## Create a new parser — detailed guide

### 1. Choose an id and file path

- Id format: `<country>.<provider>.<bill_type>` (lowercase, segments joined
  by `.`, `_` or `-`), e.g. `it.acme.gas`. Must be globally unique across the
  whole repository — `scripts/build_catalog.py` rejects duplicates.
- File path: `parsers/<country>/<provider>/<bill_type>.yaml`, e.g.
  `parsers/it/acme/gas.yaml`. The directory structure is just a convention
  for humans; only the `id` field is authoritative.
- `version` is an integer you bump whenever you change the parser's
  behavior (new/changed regex, new fields, …), so Billy knows to treat it as
  an update.

### 2. `metadata`

Descriptive fields shown in Billy's UI and used for filtering in
`parser.json`: `name`, `country` (ISO 3166-1 alpha-2, e.g. `IT`), `language`
(ISO 639-1, e.g. `it` or `it_IT`), `provider`, `bill_type` (free-form slug:
`electricity`, `gas`, `internet`, `mobile`, …), optional `homepage`, and
`min_billy_version` (semver, the minimum Billy version able to run this
parser).

### 3. `prefilter`

Runs before Billy fetches the email body or attachments — it only sees
metadata the mail provider already exposes. At least one of these is
required:

- `email.from`: list of exact sender addresses.
- `email.subject_contains`: list of substrings to look for in the subject.
- `email.subject_regex`: list of regexes, for when substrings are too rigid.

Keep it broad but cheap; it should never produce false negatives (better to
over-fetch than to miss a real bill).

### 4. `detection`

A list of weighted `rules`, each with:

- `source`: one of `email.from`, `email.subject`, `email.body`,
  `attachment.filename`, `attachment.mime_type`.
- Exactly one matcher: `equals`, `contains`, or `regex`.
- `weight`: `1`–`100`.

Billy sums the weights of every rule that matches and selects the parser
once the total reaches `threshold` (`1`–`1000`). Design rules so a genuine
match clearly clears the threshold, but no single incidental coincidence
(e.g. `attachment.mime_type equals application/pdf`) does so on its own —
that's why such generic rules should carry a low weight.

### 5. `documents`

Declares what field extraction is allowed to read from:

- `email.enabled`: whether the email body itself is searchable.
- `attachments[]`: each needs a lowercase `id` (referenced later as
  `fields.*.candidates[].source`), `extractor` (`pdf_text` or `text`),
  optional `mime_types` / `filename_regex` to pick the right attachment, and
  `required` (fail the parse if missing).

### 6. `fields`

Each field is either:

- a static **`value`** (e.g. `provider`, `bill_type`, `currency`,
  `consumption_unit` — constants that don't need extraction), or
- a list of **`candidates`**, tried in order until one matches. Each
  candidate has a `source` (`email` or a declared attachment id) and a
  `regex` with a named group `(?P<value>...)`. Order candidates from most to
  least specific/reliable.

Optional `transform` normalizes the raw match:

- `type: text` — trims/returns the string as-is.
- `type: decimal` — parses a number; set `locale` (e.g. `it_IT`) so
  `1.234,56` and `1,234.56` are both handled correctly.
- `type: date` — parses a date; set `locale` for month-name parsing, and
  optionally `formats` (explicit format strings tried in order).
- `type: date_range` — splits a single match into a start/end pair; requires
  named groups matching `start_group`/`end_group`, and use `outputs:
  [period_start, period_end]` on the field to fan the match out into two
  separate output fields. `infer_missing_year: true` helps when only the end
  date carries the year (e.g. `"01 luglio - 31 luglio 2026"`).

Mark a field `required: true` when the parser should fail (rather than
silently omit data) if no candidate matches.

### 7. `verification` (optional)

For high-stakes fields (amount, due date, invoice number) that are expected
to appear both in the email and the attachment, list them here with at
least two `sources` so Billy can cross-check them and flag mismatches
instead of silently trusting a single source.

### 8. Fixtures and validation

- Add anonymized fixtures under `tests/fixtures/<provider>/email.txt` and
  `tests/fixtures/<provider>/invoice.txt` mirroring the real structure your
  regexes target — this makes review much easier and documents intent.
- Run `python scripts/build_catalog.py --check` to validate the schema,
  semantic constraints (no undeclared document sources, no duplicate
  attachment ids, all regexes compile), and duplicate ids across the repo.
- Run `pytest` to execute the repository's own test suite.

### Reference

- Full template with inline explanations for every field:
  [`templates/parser.template.yaml`](templates/parser.template.yaml).
- Real-world examples: [`parsers/it/eon/electricity.yaml`](parsers/it/eon/electricity.yaml),
  [`parsers/it/tim/internet.yaml`](parsers/it/tim/internet.yaml).
- Authoritative schema: [`schema/parser.schema.json`](schema/parser.schema.json).
