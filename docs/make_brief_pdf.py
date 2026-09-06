"""Generate docs/Coin_Tray_Project_Brief.pdf with the Python stdlib only."""
from pathlib import Path


def pdf_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def build_pdf(lines: list[tuple[str, str]]) -> bytes:
    """Very small single-page PDF writer (Helvetica, Letter)."""
    # Letter: 612 x 792 pt. Margins ~48 pt.
    y = 744
    left = 48
    content = ["BT"]
    for kind, text in lines:
        text = text.replace("\u2014", "-").replace("\u2013", "-").replace("\u00b7", ".")
        text = text.replace("\u2019", "'").replace("\u201c", '"').replace("\u201d", '"')
        size = 11
        leading = 14
        if kind == "h1":
            size, leading = 18, 22
            y -= 8
        elif kind == "h2":
            size, leading = 12, 16
            y -= 10
        elif kind == "li":
            text = "* " + text
            size, leading = 9, 12
        elif kind == "quote":
            size, leading = 9, 12
        else:
            size, leading = 9, 12

        # crude wrap at ~90 chars
        words = text.split()
        row = ""
        chunks = []
        for w in words:
            trial = (row + " " + w).strip()
            if len(trial) > 95:
                chunks.append(row)
                row = w
            else:
                row = trial
        if row:
            chunks.append(row)
        for i, chunk in enumerate(chunks):
            if y < 48:
                break
            content.append(f"/F1 {size} Tf")
            content.append(f"1 0 0 1 {left} {y} Tm")
            content.append(f"({pdf_escape(chunk)}) Tj")
            y -= leading
        y -= 2

    content.append("ET")
    stream = "\n".join(content).encode("latin-1", errors="replace")

    objects = []
    objects.append(b"1 0 obj<< /Type /Catalog /Pages 2 0 R >>endobj\n")
    objects.append(b"2 0 obj<< /Type /Pages /Kids [3 0 R] /Count 1 >>endobj\n")
    objects.append(
        b"3 0 obj<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>endobj\n"
    )
    objects.append(
        b"4 0 obj<< /Length %d >>stream\n" % len(stream) + stream + b"\nendstream\nendobj\n"
    )
    objects.append(b"5 0 obj<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>endobj\n")

    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for obj in objects:
        offsets.append(len(out))
        out.extend(obj)
    xref = len(out)
    out.extend(f"xref\n0 {len(objects) + 1}\n".encode())
    out.extend(b"0000000000 65535 f \n")
    for off in offsets[1:]:
        out.extend(f"{off:010d} 00000 n \n".encode())
    out.extend(
        f"trailer<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    return bytes(out)


def main():
    here = Path(__file__).resolve().parent
    brief = (here / "PROJECT_BRIEF.md").read_text(encoding="utf-8")
    lines = []
    for line in brief.splitlines():
        s = line.strip()
        if s.startswith("# "):
            lines.append(("h1", s[2:]))
        elif s.startswith("## "):
            lines.append(("h2", s[3:]))
        elif s.startswith("> "):
            lines.append(("quote", s[2:].replace("**", "")))
        elif s.startswith("- "):
            lines.append(("li", s[2:].replace("**", "")))
        elif not s or s.startswith("|") or s.startswith("---"):
            if s.startswith("|") and "---" not in s and "Area" not in s and "Tech" not in s:
                cells = [c.strip().replace("**", "") for c in s.strip("|").split("|")]
                if cells and cells[0]:
                    lines.append(("p", " - ".join(cells)))
            continue
        else:
            lines.append(("p", s.replace("**", "").replace("`", "")))

    out = here / "Coin_Tray_Project_Brief.pdf"
    out.write_bytes(build_pdf(lines))
    print("Wrote", out, "bytes", out.stat().st_size)


if __name__ == "__main__":
    main()
