# Billy Parser

Community-maintained parser definitions for [Billy](https://github.com/robin994/billy).

Billy downloads only `parser.json` to discover available parsers. Parser YAML files are downloaded only when a user explicitly installs one. Parsing is declarative: parsers cannot execute Python, JavaScript, shell commands, network requests, or access the filesystem.

## Repository layout

```text
parsers/<country>/<provider>/<parser>.yaml
schema/parser.schema.json
scripts/build_catalog.py
parser.json
```

## Add a parser

1. Copy an existing parser and keep the parser ID globally unique.
2. Use synthetic or fully anonymized fixtures only. Never commit real invoices or personal emails.
3. Run:

```bash
python -m pip install -r requirements-dev.txt
python scripts/build_catalog.py --check
```

4. Open a pull request.

GitHub Actions validates every parser and regenerates `parser.json` on `main`.

## Parser model

A parser has three phases:

- `prefilter`: privacy-first metadata filtering before Billy fetches an email body or attachment.
- `detection`: weighted rules that identify the provider/type.
- `fields`: extraction from normalized email text and/or extracted PDF text.

See `parsers/it/eon/electricity.yaml` for the reference implementation.
