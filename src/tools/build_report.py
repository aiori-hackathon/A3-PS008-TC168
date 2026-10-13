"""Build the report from its Markdown source and measured test/browser evidence."""
import json
import re
import xml.etree.ElementTree as ET
from html import escape
from pathlib import Path

from PIL import Image as PILImage
from reportlab.graphics.shapes import Drawing, Line, Rect, String
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Image, KeepTogether, PageBreak, Paragraph, Preformatted, SimpleDocTemplate, Spacer

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "output" / "pdf" / "ps008-technical-report.pdf"
TEAL = colors.HexColor("#087f71")
INK = colors.HexColor("#142d35")
MUTED = colors.HexColor("#62787e")


def measured_results():
    tree = ET.parse(ROOT / "output" / "test-results.xml")
    suites = list(tree.getroot().iter("testsuite"))
    failed = sum(int(s.get("failures", "0")) + int(s.get("errors", "0")) for s in suites)
    skipped = sum(int(s.get("skipped", "0")) for s in suites)
    count = sum(int(s.get("tests", "0")) for s in suites)
    if failed or skipped or not count:
        raise ValueError("Report requires a complete passing test run")
    browser = json.loads((ROOT / "output" / "browser-results.json").read_text(encoding="utf-8"))
    if browser["result"] != "passed" or browser["console_errors"]:
        raise ValueError("Report requires passing browser evidence")
    return count, len(browser["flows"]), browser["at"]


def header_footer(canvas, document):
    canvas.saveState()
    canvas.setStrokeColor(TEAL)
    canvas.line(18 * mm, 16 * mm, 192 * mm, 16 * mm)
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(MUTED)
    canvas.drawString(18 * mm, 11 * mm, "PS-008 | Reviewed network configuration")
    canvas.drawRightString(192 * mm, 11 * mm, f"{document.page}")
    canvas.restoreState()


def diagram(kind):
    width, height = 490, 138
    drawing = Drawing(width, height)
    drawing.add(Rect(0, 0, width, height, rx=9, fillColor=colors.HexColor("#f0f6f3"), strokeColor=None))
    if kind == "topology":
        centers = [80, 245, 410]
        for i in range(2):
            drawing.add(Line(centers[i] + 35, 85, centers[i + 1] - 35, 85, strokeColor=TEAL, strokeWidth=2))
            drawing.add(String((centers[i] + centers[i + 1]) / 2, 101, f"10.0.{i+1}.0/24", textAnchor="middle", fontSize=8, fillColor=MUTED))
        for i, center in enumerate(centers):
            drawing.add(Rect(center - 35, 64, 70, 42, rx=8, fillColor=colors.white, strokeColor=TEAL))
            drawing.add(String(center, 78, f"R{i+1}", textAnchor="middle", fontName="Helvetica-Bold", fontSize=16, fillColor=INK))
            drawing.add(String(center, 44, ["10.10.0.0/24", "10.30.0.0/24", "10.20.0.0/24"][i], textAnchor="middle", fontSize=9, fillColor=INK))
            drawing.add(String(center, 27, ["Operations", "Core", "Engineering"][i], textAnchor="middle", fontSize=8, fillColor=MUTED))
    else:
        labels = [("Intent", "Web / CLI"), ("Generate", "Canonical IOS"), ("Validate", "Cloned twin"), ("Approve", "ID + revision"), ("Apply", "Verify / rollback")]
        for i, (title, detail) in enumerate(labels):
            x = 10 + i * 98
            drawing.add(Rect(x, 76, 78, 43, rx=6, fillColor=colors.white, strokeColor=TEAL))
            drawing.add(String(x + 39, 100, title, textAnchor="middle", fontName="Helvetica-Bold", fontSize=9, fillColor=INK))
            drawing.add(String(x + 39, 86, detail, textAnchor="middle", fontSize=7, fillColor=MUTED))
            if i < 4:
                drawing.add(Line(x + 78, 96, x + 97, 96, strokeColor=TEAL))
        drawing.add(Rect(85, 15, 320, 36, rx=6, fillColor=colors.white, strokeColor=TEAL))
        drawing.add(String(245, 36, "Locked atomic state / persistent reviews / audit", textAnchor="middle", fontSize=9, fillColor=INK))
        drawing.add(String(245, 23, "Snapshot + journal before every deployment", textAnchor="middle", fontSize=8, fillColor=MUTED))
        drawing.add(Line(245, 76, 245, 51, strokeColor=TEAL))
    return drawing


def build():
    tests, browser_count, evidence_time = measured_results()
    source = (ROOT / "docs" / "technical-report.md").read_text(encoding="utf-8")
    source = source.replace("{{TEST_COUNT}}", str(tests)).replace("{{BROWSER_COUNT}}", str(browser_count))
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="ReportTitle", fontName="Helvetica-Bold", fontSize=29, leading=35, textColor=INK, spaceAfter=14))
    styles.add(ParagraphStyle(name="ReportSection", fontName="Helvetica-Bold", fontSize=15, leading=19, textColor=TEAL, spaceBefore=16, spaceAfter=9, keepWithNext=True))
    styles.add(ParagraphStyle(name="ReportBody", fontSize=10, leading=15, textColor=INK, spaceAfter=9))
    styles.add(ParagraphStyle(name="ReportCaption", fontSize=9, leading=13, textColor=MUTED, spaceBefore=7, spaceAfter=16))
    styles.add(ParagraphStyle(name="ReportCode", fontName="Courier", fontSize=8, leading=11, backColor=colors.HexColor("#eff5f2"), borderPadding=12, spaceBefore=8, spaceAfter=12))
    story = [
        Spacer(1, 26 * mm),
        Paragraph("PS-008", styles["ReportTitle"]),
        Paragraph("Network Management Agent", styles["ReportTitle"]),
        Paragraph("Technical implementation report", styles["ReportSection"]),
        Paragraph("Parth Bhad and Aaditya Hingmire<br/>4 October 2026", styles["ReportBody"]),
        Spacer(1, 14 * mm),
        Paragraph("Structured intent. Explainable validation.<br/>Human approval. Verified deployment. Recoverable rollback.", styles["ReportSection"]),
        Spacer(1, 8 * mm),
        Paragraph(f"{tests} passing core/API tests<br/>{browser_count} verified browser flows<br/>Three-router software twin / Cisco IOS-style commands", styles["ReportBody"]),
        Paragraph("Evidence recorded: " + escape(evidence_time), styles["ReportCaption"]),
        Paragraph("Working intents are provisional pending mentor confirmation.", styles["ReportCaption"]),
        PageBreak(),
    ]
    paragraph, code = [], []
    fenced = False

    def flush():
        if paragraph:
            story.append(Paragraph(escape(" ".join(paragraph)), styles["ReportBody"]))
            paragraph.clear()

    for line in source.splitlines():
        if line.startswith("# ") or line.startswith("Technical implementation"):
            continue
        if line.startswith("```"):
            flush()
            if fenced:
                story.append(Preformatted("\n".join(code), styles["ReportCode"]))
                code.clear()
            fenced = not fenced
            continue
        if fenced:
            code.append(line)
            continue
        if line.startswith("## "):
            flush()
            if line[3:] == "Architecture and interfaces":
                story.append(PageBreak())
            story.append(Paragraph(escape(line[3:]), styles["ReportSection"]))
        elif line.startswith("!["):
            flush()
            match = re.fullmatch(r"!\[(.*?)\]\((.*?)\)", line)
            if not match:
                raise ValueError("Malformed report image")
            caption, relative = match.groups()
            if relative.endswith(".svg"):
                story.append(KeepTogether([diagram("topology" if "topology" in relative else "architecture"), Paragraph(escape(caption), styles["ReportCaption"])]))
            else:
                path = ROOT / "docs" / relative
                with PILImage.open(path) as image:
                    width, height = image.size
                scale = min(174 * mm / width, 218 * mm / height)
                if not isinstance(story[-1], PageBreak):
                    story.append(PageBreak())
                story.extend([Image(str(path), width=width * scale, height=height * scale), Paragraph(escape(caption), styles["ReportCaption"]), PageBreak()])
        elif line.startswith("- "):
            flush()
            story.append(Paragraph("- " + escape(line[2:]), styles["ReportBody"]))
        elif not line.strip():
            flush()
        else:
            paragraph.append(line.strip())
    flush()
    document = SimpleDocTemplate(str(OUTPUT), pagesize=A4, leftMargin=18*mm, rightMargin=18*mm,
                                 topMargin=18*mm, bottomMargin=23*mm,
                                 title="PS-008 Network Management Agent", author="Parth Bhad and Aaditya Hingmire")
    document.build(story, onFirstPage=header_footer, onLaterPages=header_footer)
    print(OUTPUT)


if __name__ == "__main__":
    build()
