"""Extract the store policy manual from PDF to plain text, once.

The manual is ~7 pages, so the agent keeps the whole text in its system prompt
instead of chunking it for retrieval. Run again only if the PDF changes:

    python -m scripts.extract_policies
"""

import re
from pathlib import Path

from pypdf import PdfReader

ROOT = Path(__file__).resolve().parent.parent
PDF = ROOT / "data" / "raw" / "politicas_da_loja.pdf"
OUT = ROOT / "data" / "policies.txt"

# Repeated on every page; it only adds noise to the prompt.
PAGE_NOISE = re.compile(r"^\s*(Empório da Música Manual de Políticas e Procedimentos|Página \d+)\s*$")


def extract(pdf_path: Path) -> str:
    # Layout mode keeps table columns aligned; the default mode returns one word per line for this PDF.
    pages = [page.extract_text(extraction_mode="layout") for page in PdfReader(pdf_path).pages]
    lines = [line.rstrip() for page in pages for line in page.splitlines() if not PAGE_NOISE.match(line)]
    text = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"


def main() -> None:
    text = extract(PDF)
    OUT.write_text(text, encoding="utf-8")
    print(f"{OUT.relative_to(ROOT)}: {len(text)} chars, {len(text.splitlines())} lines")


if __name__ == "__main__":
    main()
