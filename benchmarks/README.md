# Evaluate VIRA retrieval on your own PDFs

This benchmark measures whether VIRA retrieves the PDF page that a human identified as relevant. It runs locally and does not send questions or PDFs to a cloud service. It does not judge whether an Ollama answer is medically correct.

## 1. Build a reviewed question set

Import PDFs that you are allowed to store and process. Include questions whose answers are on a known page and questions that the imported library cannot answer. Cover terminology, methods, numeric results, and questions that require care with negation or population details. Do not copy the model's answer into the labels. Have a second person review labels when practical.

Run this command in the VIRA project folder to list imported filenames, SHA-256 hashes, and PDF page counts:

```bash
.venv/bin/python -m vira.evaluate --list-papers
```

Copy `template.jsonl` to a new file outside `data/` and replace each example with a real question. Each line is one JSON object. `expected_sources` lists every acceptable PDF page as its SHA-256 hash and **PDF page position starting at 1**, which may differ from the printed journal page number. Use `answerable: false` and an empty list when the local library has no supporting page.

## 2. Run and compare

```bash
.venv/bin/python -m vira.evaluate --input benchmarks/my_questions.jsonl --mode keyword --top-k 5
.venv/bin/python -m vira.evaluate --input benchmarks/my_questions.jsonl --mode both --top-k 5 --output benchmarks/my_report.json
```

The second command requires a complete local semantic index and a running Ollama embedding model. Keep question labels unchanged between runs. `page_hit_at_k` is the fraction of answerable questions with at least one labeled page among the first *k* results. `document_hit_at_k` ignores page position. `page_mrr` rewards an earlier correct page. `unanswerable_with_results_rate` measures how often retrieval still returns passages for unanswerable questions; it is **not** a measured hallucination rate or an answer refusal rate.

Inspect per-question results, especially missed pages and unanswerable questions with hits. Change a retrieval setting only when the same reviewed question set shows an improvement without unacceptable new failures. Report sample sizes with every percentage. Larger claims about answer faithfulness require separate human review of full answers and their quoted evidence.
