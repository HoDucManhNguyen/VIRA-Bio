# Architecture and evidence flow

VIRA Bio is a single FastAPI application with a browser interface. It stores metadata, extracted passages, search indexes, notes, and tasks in SQLite. Original imported PDFs are stored beside the database. This keeps the application usable without a paid AI API or a separate database server.

## Import and retrieval

1. The user confirms permission to store a PDF. The server accepts PDFs up to 25 MB and 500 pages, validates the file, and extracts text with `pypdf`.
2. The server stores the original PDF under `data/library/` and splits extracted page text into searchable passages. It records the source PDF and page for each passage.
3. Keyword search uses SQLite FTS5. Optional hybrid search combines FTS5 with local Ollama embeddings using reciprocal-rank fusion.
4. Search results include source metadata and a link that opens the original PDF at the cited page.

Image-only scans must be OCR processed before import. Text extraction may miss tables, figures, equations, or unusual PDF layouts; the original page is the authoritative source.

## Source-backed questions

The PDF question endpoint retrieves a small set of passages first. If nothing relevant is found, it reports insufficient evidence. In `extractive` mode it shows the retrieved excerpts directly. In `ollama` mode it sends only those excerpts and the question to a local Ollama model, then validates the returned source numbers, exact quotes, and numbers. If the model's answer does not pass these checks, the interface falls back to excerpts.

Validation reduces some unsupported output but does not establish semantic correctness, medical validity, or completeness. Users should verify claims on the linked PDF pages. The model does not browse PubMed while answering a PDF question.

## External connections and data

| Component | Purpose | Data sent |
| --- | --- | --- |
| Ollama on `127.0.0.1:11434` | Optional answers and embeddings | Retrieved excerpts or passage text |
| NCBI PubMed | On-demand citation metadata lookup | Search terms |
| Temporary HTTPS tunnel | Optional remote browser access | Browser requests to the shared workspace |

The default local server accepts localhost traffic only. An HTTPS tunnel can make the shared application reachable without exposing the Ollama port. Anonymous public access lets anyone with the URL read and modify the shared workspace; use only material suitable for that audience.

## Evaluation boundaries

The tests verify application behavior, not the scientific correctness of biomedical claims. The [retrieval benchmark](../benchmarks/README.md) provides a template for human-labeled source pages. Report page hit rates only after labeling a representative set of PDFs and questions.
