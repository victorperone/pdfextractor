from __future__ import annotations

from structured_pdf_text.cli import main

if __name__ == "__main__":
    raise SystemExit(main(["inspect", *(__import__("sys").argv[1:])]))
