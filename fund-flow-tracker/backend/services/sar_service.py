"""
sar_service.py
--------------
Two parts:
  1. Gemini 2.0 Flash narrative generator (5-section SAR text)
  2. ReportLab PDF builder (letterhead, graph image, narrative, evidence table, audit trail)
"""

import os
import io
import base64
import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import google.generativeai as genai
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, Image as RLImage,
)
from PIL import Image as PILImage
from supabase import create_client, Client
from dotenv import load_dotenv

from services.presidio_service import mask_text, unmask_text

load_dotenv()
logger = logging.getLogger(__name__)

genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

SUPA_URL = os.getenv("SUPABASE_URL")
SUPA_KEY = os.getenv("SUPABASE_KEY")
supabase: Client = create_client(SUPA_URL, SUPA_KEY)

UTC = timezone.utc

# ── Fallback SAR PDF content ──────────────────────────────────────────────────

_FALLBACK_NARRATIVE = """
Executive Summary:
A suspicious activity pattern was detected by the automated Fund Flow Tracking System.
The full AI narrative could not be generated within the time limit; this is a fallback report.

Suspicious Activity Description:
The system identified anomalous transaction behaviour inconsistent with the account KYC profile.
Further investigation is recommended.

Transaction Analysis:
Please refer to the transaction evidence table below for details.

Risk Indicators:
- Automated detection threshold exceeded
- Pattern inconsistent with declared profile

Recommended Action:
Partial Freeze — Pending Officer Review
"""

# ── Part 1 — Gemini SAR Narrative ────────────────────────────────────────────

async def generate_narrative(alert: Dict[str, Any], masked_txn_text: str) -> str:
    """
    Calls Gemini 2.0 Flash to write a formal 5-section SAR narrative.
    Falls back to static text on timeout.
    """
    flag_type = alert.get("flag_type", "UNKNOWN")
    risk_score = alert.get("risk_score", 0)
    suspicious_amount = alert.get("suspicious_amount", 0)
    branches = alert.get("branches_involved", [])
    channels = alert.get("channels_used", [])
    time_window = alert.get("time_span") or alert.get("time_window", "")
    product_chain = alert.get("product_chain", "N/A")
    engine1_score = alert.get("engine1_score", 0)
    engine2_score = alert.get("engine2_score", 0)
    ml_addition = alert.get("ml_addition", 0)

    prompt = f"""
You are writing an official Suspicious Activity Report (SAR) for the Financial Intelligence Unit of India (FIU-IND).
Use formal regulatory language. Do NOT use markdown. Write plain text with clearly labelled sections.

Alert Information:
- Alert Type: {flag_type}
- Total Suspicious Amount: ₹{suspicious_amount:,.2f}
- Risk Score: {risk_score}/100 (Engine 1: {engine1_score}/40, Engine 2: {engine2_score}/60, AI Addition: +{ml_addition})
- Branches Involved: {', '.join(branches) if branches else 'Multiple'}
- Channels Used: {', '.join(channels) if channels else 'Multiple'}
- Time Period: {time_window}
- Product Chain: {product_chain}

Transaction Evidence (Masked):
{masked_txn_text}

Write the SAR with exactly these 5 sections in order:

1. EXECUTIVE SUMMARY
(2 sentences maximum — state the alert type and total suspicious amount)

2. SUSPICIOUS ACTIVITY DESCRIPTION
(Formal regulatory language — explain the fraud pattern detected and why it is suspicious)

3. TRANSACTION ANALYSIS
(Reference the masked token IDs, specific amounts, dates, branches, and channels from the evidence above)

4. RISK INDICATORS
(Bullet list — every red flag: branch diversity, dormant flags, product switching, unusual hours, foreign transfers, velocity)

5. RECOMMENDED ACTION
(One of: Monitor / Partial Freeze / Full Freeze / Refer to Enforcement Directorate)
"""

    try:
        model = genai.GenerativeModel(
            model_name="gemini-2.0-flash",
            generation_config={"temperature": 0.3},  # No JSON mode — want formatted text
        )
        response = await asyncio.wait_for(
            asyncio.to_thread(model.generate_content, prompt),
            timeout=8.0,
        )
        return response.text.strip()
    except asyncio.TimeoutError:
        logger.warning("Gemini SAR narrative timed out — using fallback")
        return _FALLBACK_NARRATIVE
    except Exception as e:
        logger.warning(f"SAR narrative error: {e}")
        return _FALLBACK_NARRATIVE


# ── Part 2 — ReportLab PDF Builder ───────────────────────────────────────────

async def build_pdf_and_upload(
    alert: Dict[str, Any],
    alert_id: str,
    graph_image_b64: str,
    narrative: str,
    transactions: List[Dict[str, Any]],
    audit_log: List[Dict[str, Any]],
) -> str:
    """
    Builds the SAR PDF and uploads to Supabase Storage.
    Returns download URL.
    """
    pdf_bytes = _build_pdf(alert, alert_id, graph_image_b64, narrative, transactions, audit_log)

    # Upload to Supabase Storage
    account_masked = alert.get("account_id_masked", "UNKNOWN")
    date_str = datetime.now(UTC).strftime("%Y_%m_%d")
    filename = f"SAR_{account_masked}_{date_str}_{alert_id[:8]}.pdf"

    try:
        supabase.storage.from_("sar-reports").upload(
            path=filename,
            file=pdf_bytes,
            file_options={"content-type": "application/pdf"},
        )
        url_resp = supabase.storage.from_("sar-reports").get_public_url(filename)
        download_url = url_resp if isinstance(url_resp, str) else url_resp.get("publicURL", "")
    except Exception as e:
        logger.error(f"Supabase storage upload failed: {e}")
        download_url = f"/sar/{filename}"

    return download_url, filename


def _build_pdf(
    alert: Dict[str, Any],
    alert_id: str,
    graph_image_b64: str,
    narrative: str,
    transactions: List[Dict[str, Any]],
    audit_log: List[Dict[str, Any]],
) -> bytes:
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=2 * cm,
        leftMargin=2 * cm,
        topMargin=2 * cm,
        bottomMargin=2 * cm,
    )

    styles = getSampleStyleSheet()
    h1 = ParagraphStyle("H1", fontSize=16, spaceAfter=4, alignment=1, fontName="Helvetica-Bold")
    h2 = ParagraphStyle("H2", fontSize=12, spaceAfter=4, spaceBefore=12, fontName="Helvetica-Bold")
    sub = ParagraphStyle("Sub", fontSize=10, spaceAfter=6, alignment=1, textColor=colors.gray)
    body = ParagraphStyle("Body", fontSize=10, spaceAfter=6, leading=14)
    bold = ParagraphStyle("Bold", fontSize=10, spaceAfter=6, fontName="Helvetica-Bold")

    story = []

    # ── Letterhead ────────────────────────────────────────────────────────────
    story.append(Paragraph("NATIONAL FUND FLOW INTELLIGENCE SYSTEM", h1))
    story.append(Paragraph("Suspicious Activity Report — FIU-IND", sub))
    story.append(Paragraph(f"Report Date: {datetime.now(UTC).strftime('%d %B %Y')}", sub))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.red, spaceAfter=10))

    # ── Case Metadata Table ───────────────────────────────────────────────────
    risk_level = "CRITICAL" if alert.get("risk_score", 0) >= 80 else \
                 "HIGH" if alert.get("risk_score", 0) >= 50 else "MEDIUM"

    meta_data = [
        ["Case Reference", f"AUTO-{alert_id[:12].upper()}"],
        ["Alert Type", alert.get("flag_type", "UNKNOWN")],
        ["Report Date", datetime.now(UTC).strftime("%d/%m/%Y")],
        ["Total Suspicious Amount", f"₹{alert.get('suspicious_amount', 0):,.2f}"],
        ["Risk Level", risk_level],
        ["Status", "PENDING FIU SUBMISSION"],
    ]
    meta_table = Table(meta_data, colWidths=[5 * cm, 12 * cm])
    meta_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#F5F5F5")),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.lightgrey),
        ("ROWBACKGROUNDS", (0, 0), (-1, -1), [colors.white, colors.HexColor("#FAFAFA")]),
    ]))
    story.append(meta_table)
    story.append(Spacer(1, 0.5 * cm))

    # ── Transaction Network Graph ─────────────────────────────────────────────
    if graph_image_b64:
        try:
            img_data = base64.b64decode(graph_image_b64)
            pil_img = PILImage.open(io.BytesIO(img_data))
            if pil_img.mode != "RGB":
                pil_img = pil_img.convert("RGB")
            max_width = 16 * cm
            orig_w, orig_h = pil_img.size
            ratio = max_width / orig_w
            new_h = orig_h * ratio
            pil_img = pil_img.resize((int(max_width * 72 / cm), int(new_h * 72 / cm)), PILImage.LANCZOS)
            png_buf = io.BytesIO()
            pil_img.save(png_buf, format="PNG")
            png_buf.seek(0)
            story.append(Paragraph("Transaction Network Graph", h2))
            story.append(RLImage(png_buf, width=max_width, height=new_h))
            story.append(Spacer(1, 0.3 * cm))
        except Exception as e:
            logger.warning(f"Graph image embed failed: {e}")

    # ── Gemini Narrative ──────────────────────────────────────────────────────
    story.append(Paragraph("AI-Generated Investigation Narrative", h2))
    for line in narrative.split("\n"):
        line = line.strip()
        if not line:
            story.append(Spacer(1, 0.2 * cm))
        elif line.isupper() or line.endswith(":"):
            story.append(Paragraph(line, bold))
        else:
            story.append(Paragraph(line, body))
    story.append(Spacer(1, 0.3 * cm))

    # ── Transaction Evidence Table ────────────────────────────────────────────
    story.append(Paragraph("Transaction Evidence", h2))
    txn_headers = ["Timestamp", "From (Masked)", "To (Masked)", "Amount (₹)", "Channel", "Branch", "City"]
    txn_rows = [txn_headers]
    for t in transactions[:50]:
        ts = str(t.get("timestamp", ""))[:16]
        txn_rows.append([
            ts,
            t.get("from_masked", t.get("from", "—")),
            t.get("to_masked", t.get("to", "—")),
            f"₹{float(t.get('amount', 0)):,.0f}",
            t.get("channel", "—"),
            t.get("branch_id", "—"),
            t.get("city", "—"),
        ])

    if len(txn_rows) > 1:
        txn_table = Table(txn_rows, repeatRows=1)
        txn_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1a1a2e")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 7),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.lightgrey),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F5F8FF")]),
        ]))
        story.append(txn_table)
    story.append(Spacer(1, 0.5 * cm))

    # ── Audit Trail ───────────────────────────────────────────────────────────
    story.append(Paragraph("Chain of Custody — Audit Trail", h2))
    audit_headers = ["Action Type", "Officer / System", "Timestamp", "Amount (₹)", "Notes"]
    audit_rows = [audit_headers]
    for a in audit_log:
        audit_rows.append([
            a.get("action_type", "—"),
            a.get("officer_id") or "SYSTEM_AUTO",
            str(a.get("timestamp", ""))[:16],
            f"₹{float(a.get('amount_frozen', 0)):,.0f}",
            (a.get("notes") or "")[:60],
        ])

    if len(audit_rows) > 1:
        audit_table = Table(audit_rows, repeatRows=1)
        audit_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#c0392b")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 7),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.lightgrey),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#FFF5F5")]),
        ]))
        story.append(audit_table)

    doc.build(story)
    return buffer.getvalue()
