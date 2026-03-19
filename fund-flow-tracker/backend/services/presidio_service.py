"""
presidio_service.py
-------------------
PII masking using Microsoft Presidio.
Masks names, account numbers, phone numbers, emails.
Returns masked text + a vault for de-masking after PDF generation.
"""

import re
import hashlib
from typing import Dict, Tuple

try:
    from presidio_analyzer import AnalyzerEngine
    from presidio_anonymizer import AnonymizerEngine
    from presidio_anonymizer.entities import OperatorConfig

    _analyzer = AnalyzerEngine()
    _anonymizer = AnonymizerEngine()
    _PRESIDIO_AVAILABLE = True
except ImportError:
    _PRESIDIO_AVAILABLE = False


# ── Public API ────────────────────────────────────────────────────────────────

def mask_account_id(account_id: str) -> str:
    """
    Returns a deterministic, stable masked token for an account ID.
    Format: ACC_XXXX (last 4 hex chars of SHA256).
    """
    digest = hashlib.sha256(account_id.encode()).hexdigest()
    return f"ACC_{digest[:8].upper()}"


def mask_text(raw_text: str) -> Tuple[str, Dict[str, str]]:
    """
    Masks PII in raw_text. Returns (masked_text, vault).
    vault maps TOKEN → original_value for later de-masking.

    If Presidio is not installed, falls back to simple regex masking.
    """
    if _PRESIDIO_AVAILABLE:
        return _presidio_mask(raw_text)
    else:
        return _regex_mask(raw_text)


def unmask_text(masked_text: str, vault: Dict[str, str]) -> str:
    """Replace all tokens in masked_text with original values from vault."""
    result = masked_text
    for token, original in vault.items():
        result = result.replace(token, original)
    return result


# ── Presidio implementation ───────────────────────────────────────────────────

def _presidio_mask(raw_text: str) -> Tuple[str, Dict[str, str]]:
    results = _analyzer.analyze(
        text=raw_text,
        entities=["PERSON", "PHONE_NUMBER", "EMAIL_ADDRESS", "CREDIT_CARD", "IN_PAN"],
        language="en",
    )

    # Sort by start position descending so we replace from right to left
    results.sort(key=lambda r: r.start, reverse=True)

    vault: Dict[str, str] = {}
    counters: Dict[str, int] = {}
    masked = raw_text

    for r in results:
        entity_type = r.entity_type
        original = masked[r.start : r.end]
        counters[entity_type] = counters.get(entity_type, 0) + 1
        token = f"TOKEN_{entity_type}_{counters[entity_type]}"
        vault[token] = original
        masked = masked[: r.start] + token + masked[r.end :]

    return masked, vault


# ── Regex fallback ────────────────────────────────────────────────────────────

def _regex_mask(raw_text: str) -> Tuple[str, Dict[str, str]]:
    vault: Dict[str, str] = {}
    text = raw_text
    counter = [0]

    def _replace(pattern: str, label: str, t: str) -> str:
        def _sub(m):
            counter[0] += 1
            token = f"TOKEN_{label}_{counter[0]}"
            vault[token] = m.group(0)
            return token
        return re.sub(pattern, _sub, t)

    # Account-number-like strings (10–18 digits)
    text = _replace(r"\b\d{10,18}\b", "ACC", text)
    # PAN card
    text = _replace(r"\b[A-Z]{5}\d{4}[A-Z]\b", "PAN", text)
    # Phone numbers
    text = _replace(r"\b[6-9]\d{9}\b", "PHONE", text)
    # Email
    text = _replace(r"\b[\w.+-]+@[\w-]+\.[a-zA-Z]{2,}\b", "EMAIL", text)

    return text, vault
