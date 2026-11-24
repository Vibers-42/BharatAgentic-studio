"""Writes the small sample files for the shared-knowledge acceptance test (samples/knowledge/): one TXT,
Markdown, DOCX, CSV and XLSX file, each holding one distinct, fictional fact that a question can check.
The DOCX is built by hand (a minimal valid Office Open XML package); the XLSX with openpyxl.

    python samples/make_knowledge_samples.py
"""

import csv
import io
import zipfile
from pathlib import Path

OUT = Path(__file__).resolve().parent / "knowledge"

# file name -> (question, fact that must appear in the answer)
FACTS = {
    "warehouse-notes.txt": ("Until what time does the Hyderabad warehouse ship orders on weekdays?", "6:30"),
    "support-runbook.md": ("Within how many minutes must a priority-1 support ticket get a first response?", "15"),
    "leave-policy.docx": ("How many days of paid leave do employees get each year?", "18"),
    "courier-partners.csv": ("What is the maximum parcel weight that the courier SwiftShip carries?", "32"),
    "gift-cards.xlsx": ("For how many months is the Festive gift card valid?", "9"),
}

TXT = """Hyderabad warehouse operating notes (fictional sample)

The Hyderabad warehouse ships orders until 6:30 PM every weekday.
On Saturdays the last pickup is at 1 PM, and the warehouse is closed on Sundays.
Fragile items are packed in double-walled boxes.
"""

MD = """# Customer support runbook (fictional sample)

## Response times

- Priority-1 tickets (site down, payments failing) must get a first response within 15 minutes.
- Priority-2 tickets must get a first response within 4 hours.

## Escalation

Unresolved priority-1 tickets are escalated to the on-call engineering lead after 30 minutes.
"""

DOCX_PARAGRAPHS = [
    "Leave policy (fictional sample)",
    "Employees get 18 days of paid leave each year.",
    "Unused paid leave of up to 5 days can be carried over to the next year.",
    "Sick leave is separate and does not reduce paid leave.",
]

CSV_ROWS = [
    ["Courier", "City", "Max parcel weight (kg)", "Cash on delivery"],
    ["SwiftShip", "Pune", "32", "yes"],
    ["RapidRoute", "Chennai", "20", "no"],
    ["MetroDrop", "Kolkata", "12", "yes"],
]

XLSX_ROWS = [
    ["Gift card", "Validity (months)", "Minimum value (Rs.)"],
    ["Festive card", 9, 500],
    ["Birthday card", 12, 250],
    ["Corporate card", 24, 2000],
]


def docx_bytes(paragraphs: list[str]) -> bytes:
    body = "".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paragraphs)
    files = {
        "[Content_Types].xml": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>',
        "_rels/.rels": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="word/document.xml"/></Relationships>',
        "word/document.xml": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body></w:document>",
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, text in files.items():
            z.writestr(zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0)), text)
    return buf.getvalue()


def main():
    from openpyxl import Workbook
    OUT.mkdir(exist_ok=True)
    (OUT / "warehouse-notes.txt").write_text(TXT, encoding="utf-8", newline="\n")
    (OUT / "support-runbook.md").write_text(MD, encoding="utf-8", newline="\n")
    (OUT / "leave-policy.docx").write_bytes(docx_bytes(DOCX_PARAGRAPHS))
    with open(OUT / "courier-partners.csv", "w", encoding="utf-8", newline="") as f:
        csv.writer(f).writerows(CSV_ROWS)
    wb = Workbook()
    ws = wb.active
    ws.title = "Gift cards"
    for row in XLSX_ROWS:
        ws.append(row)
    wb.save(OUT / "gift-cards.xlsx")
    print("wrote", ", ".join(sorted(p.name for p in OUT.iterdir())))


if __name__ == "__main__":
    main()
