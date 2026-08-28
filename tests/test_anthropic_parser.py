from __future__ import annotations

import re
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
PARSER_PATH = ROOT / "parsers" / "it" / "anthropic" / "subscription.yaml"


INVOICE_SAMPLE = """\
Invoice
Invoice number SAMPLEABC-0001
Date of issue August 27, 2026
Date due August 27, 2026
Anthropic, PBC @anthropic
Claude Pro
Aug 27-Sep 27, 2026
Total €21.96
Amount due €21.96
"""


RECEIPT_SAMPLE = """\
Receipt
Invoice number SAMPLEABC-0001
Receipt number 2010-1111-2222
Date paid August 27, 2026
Anthropic, PBC @anthropic
€21.96 paid on August 27, 2026
Claude Pro
Aug 27-Sep 27, 2026
Total €21.96
Amount paid €21.96
Payment method Date Amount paid Receipt number
Mastercard - 1234 August 27, 2026 €21.96 2010-1111-2222
"""


def _parser() -> dict:
    return yaml.safe_load(PARSER_PATH.read_text(encoding="utf-8"))


def _extract(field: dict, text: str) -> dict[str, str] | None:
    for candidate in field.get("candidates", []):
        match = re.search(candidate["regex"], text)
        if match:
            return match.groupdict()
    return None


def test_anthropic_parser_is_english_and_experimental():
    parser = _parser()
    metadata = parser["metadata"]

    assert parser["id"] == "it.anthropic.subscription"
    assert parser["version"] == 3
    assert metadata["language"] == "en"
    assert metadata["provider"] == "Anthropic"
    assert metadata["status"] == "experimental"
    assert metadata["quality"] == "experimental"


def test_anthropic_real_imap_event_matches_without_email_body():
    parser = _parser()
    event = {
        "sender": "invoice+statements@mail.anthropic.com",
        "subject": "Your receipt from Anthropic, PBC #2010-2892-7311",
        "attachments": [
            ("Invoice-DAXGMYDD-0002.pdf", "application/pdf"),
            ("Receipt-2010-2892-7311.pdf", "application/pdf"),
        ],
    }

    prefilter = parser["prefilter"]["email"]
    assert event["sender"] in prefilter["from"]
    assert any(re.search(pattern, event["subject"]) for pattern in prefilter["subject_regex"])

    score = 0
    for rule in parser["detection"]["rules"]:
        matched = False
        if rule["source"] == "email.from":
            matched = event["sender"] == rule.get("equals")
        elif rule["source"] == "email.subject" and "regex" in rule:
            matched = re.search(rule["regex"], event["subject"]) is not None
        elif rule["source"] == "attachment.filename" and "regex" in rule:
            matched = any(re.search(rule["regex"], filename) for filename, _ in event["attachments"])
        elif rule["source"] == "attachment.mime_type" and "equals" in rule:
            matched = any(mime == rule["equals"] for _, mime in event["attachments"])
        if matched:
            score += rule["weight"]

    assert score >= parser["detection"]["threshold"]


def test_anthropic_invoice_sample_extracts_core_fields():
    fields = _parser()["fields"]

    assert _extract(fields["invoice_number"], INVOICE_SAMPLE) == {"value": "SAMPLEABC-0001"}
    assert _extract(fields["issue_date"], INVOICE_SAMPLE) == {"value": "August 27, 2026"}
    assert _extract(fields["due_date"], INVOICE_SAMPLE) == {"value": "August 27, 2026"}
    assert _extract(fields["amount"], INVOICE_SAMPLE) == {"value": "21.96"}
    assert _extract(fields["offer"], INVOICE_SAMPLE) == {"value": "Claude Pro"}


def test_anthropic_receipt_sample_extracts_payment_fields():
    fields = _parser()["fields"]

    assert _extract(fields["payment_date"], RECEIPT_SAMPLE) == {"value": "August 27, 2026"}
    assert _extract(fields["amount"], RECEIPT_SAMPLE) == {"value": "21.96"}
    assert _extract(fields["payment_method"], RECEIPT_SAMPLE) == {"value": "Mastercard - 1234"}
    assert _extract(fields["receipt_number"], RECEIPT_SAMPLE) == {"value": "2010-1111-2222"}


def test_anthropic_uses_separate_invoice_and_receipt_documents():
    documents = _parser()["documents"]["attachments"]

    assert [document["id"] for document in documents] == ["invoice_pdf", "receipt_pdf"]
    assert re.search(documents[0]["filename_regex"], "Invoice-DAXGMYDD-0002.pdf")
    assert not re.search(documents[0]["filename_regex"], "Receipt-2010-2892-7311.pdf")
    assert re.search(documents[1]["filename_regex"], "Receipt-2010-2892-7311.pdf")
