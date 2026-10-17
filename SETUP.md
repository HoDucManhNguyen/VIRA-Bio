# Set up VIRA Bio locally

These steps work from a local project folder and do not require a paid API key. If VIRA is already installed, keep its `.env` file and `data/` folder in place.

After local setup, use [SHARING.md](SHARING.md) to let others access the same library through a temporary public HTTPS URL. You do not need a domain for that trial.

## 1. Open the project in Terminal

```bash
git clone https://github.com/HoDucManhNguyen/VIRA-Bio.git
cd VIRA-Bio
pwd
```

The printed path should end in `/VIRA-Bio`.

## 2. Confirm Python dependencies

Create a virtual environment if this is a fresh clone, then install the dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -c "import fastapi, httpx, pypdf; print('Python dependencies: OK')"
```

A network connection is needed for package installation, not for later offline library searches.

## 3. Start VIRA or switch to evidence-only mode

On a fresh clone, create the settings file from the example. Existing installations should keep their current `.env` file.

```bash
cp .env.example .env
nano .env
```

The following values select the evidence-only mode. Change only the settings you need, and preserve any database path or port you deliberately configured.

```dotenv
VIRA_PROVIDER=extractive
VIRA_MODEL=
VIRA_OLLAMA_URL=http://127.0.0.1:11434
VIRA_EMBED_MODEL=nomic-embed-text
VIRA_DB_PATH=./data/vira.db
VIRA_HOST=127.0.0.1
VIRA_PORT=8000
```

In `nano`, press **Control+O**, **Return**, then **Control+X** to save and exit. There is no API key to create for this mode.

Start VIRA:

```bash
python run.py
```

Open <http://127.0.0.1:8000>. Keep this Terminal window open. If VIRA was already running, press **Control+C** in its Terminal window before starting it again. A page refresh alone does not reload `.env` settings.

## 4. Test PDF import and search

1. VIRA opens on **Instructions**. Read the PDF Library guide there, then click **Open PDF Library**.
2. Expand **Manage PDFs → Add PDFs**, click **Choose Files**, and select a PDF with selectable text that you are allowed to store and process.
3. Check **I have permission to store and process these PDFs**, then click **Import PDFs**.
4. Expand **Manage PDFs** and confirm the document appears under **Your PDFs**.
5. Select **Find passages**, search for `Hazard Ratio`, and open a result at its cited PDF page.
6. Select **Ask a question** and ask: `What does this document say about hazard ratios?`

With `VIRA_PROVIDER=ollama`, a local model drafts a short answer only when source quotes pass checks. With `VIRA_PROVIDER=extractive`, VIRA lists retrieved excerpts without drafting a conclusion. You can delete the test PDF from the library afterward. Imported files and the search index are stored under `data/`.

Each PDF may be up to 25 MB and 500 pages. Scanned image-only PDFs need OCR before VIRA can search their text.

## 5. Search PubMed without an API key

In **PDF Library**, expand **Find articles on PubMed** under Optional tools, enter a topic, and click **Search PubMed**. This sends the search terms to NCBI and returns citation metadata and links. It does not import a PDF. NCBI allows ordinary low-rate E-utilities use without an API key; VIRA spaces its requests below the default rate limit. See the [NCBI E-utilities guidance](https://www.ncbi.nlm.nih.gov/books/NBK25497/).

To add a research article to VIRA, first obtain a PDF you are permitted to store and process, then import it as in Step 4. Do not assume every PubMed or PMC result permits automated full-text download. Check the article license, including within the [PMC Open Access Subset](https://pmc.ncbi.nlm.nih.gov/tools/openftlist/).

## 6. Optional: enable free local AI with Ollama

`gemma3:4b` is a small starting model for a computer with sufficient memory, though biomedical answers still require source review. Ollama lists this download at about 3.4 GB; allow additional disk space and memory for runtime files. Check the model's [license and details](https://ollama.com/library/gemma3:4b) before using it.

1. Download and install the [Ollama macOS app](https://ollama.com/download/mac), then open the app. The [official macOS guide](https://docs.ollama.com/macos) describes installation and local model storage. No Ollama account or subscription is required for a model running on this Mac.
2. To keep Ollama in local-only mode, create `~/.ollama/server.json` with this content, then quit and reopen Ollama:

   ```json
   {
     "disable_ollama_cloud": true
   }
   ```

   See the [official local-only setting](https://docs.ollama.com/faq). If the file already exists, add this key to its JSON object without replacing other settings.
3. Open a **new** Terminal window and run:

   ```bash
   ollama pull gemma3:4b
   ollama list
   curl http://127.0.0.1:11434/api/tags
   ```

   `ollama list` should show `gemma3:4b`. The local API response should contain a `models` list. No Ollama cloud account or API key is needed for a local model.
4. Stop VIRA with **Control+C** if you are restarting the local server. In the VIRA project folder, open `.env` with `nano .env` and change only these lines:

   ```dotenv
   VIRA_PROVIDER=ollama
   VIRA_MODEL=gemma3:4b
   VIRA_OLLAMA_URL=http://127.0.0.1:11434
   ```

5. Save `.env`, then run `python run.py` again for local-only access. To let visitors use the AI through a temporary HTTPS link, run `VIRA_PORT=8001 .venv/bin/python share.py` from the project folder instead. This creates a new public URL; keep the Terminal window and Mac awake. The visitors only need a browser, and Ollama remains on `127.0.0.1:11434` on the Mac.
6. In PDF Library, select **Ask a question** and ask about a document already in the library. VIRA sends retrieved excerpts to your local Ollama service. It shows model claims only when their source numbers, exact quotes, and numbers pass basic checks. If the format fails, VIRA shows the source excerpts instead. These checks cannot prove the interpretation is correct; inspect the PDF.

To return to evidence-only search, set `VIRA_PROVIDER=extractive` and restart VIRA. The PDF library remains available either way.

## 7. Optional: enable local hybrid search

Hybrid search combines the existing keyword index with semantic embeddings. It uses Ollama on this computer, independently of whether PDF answers use extractive or Ollama mode. The free [nomic-embed-text model](https://ollama.com/library/nomic-embed-text) is an initial option; measure its retrieval quality on your own documents before relying on it.

1. Install and start Ollama as in Step 6, then run `ollama pull nomic-embed-text` in Terminal. This model is a separate download from any chat model.
2. Keep `VIRA_EMBED_MODEL=nomic-embed-text` in `.env` (add that line if your existing file predates this feature). Restart VIRA after changing `.env`.
3. Import your PDFs. In **PDF Library**, expand **Semantic search** under Optional tools and click **Build semantic index**. Leave the page open while the progress count advances. After importing more PDFs, click it again; only new passages are processed. If you update the same embedding model and need fresh vectors, click **Rebuild semantic index**.
4. When the status says **Hybrid search is ready**, choose **Hybrid** beside the question or passage search field. If Ollama is stopped, hybrid requests will fail; choose **Keyword** to search offline.

The vector index is stored in the same local SQLite database as the keyword index. If you change the embedding model name, build a separate index for that model. Hybrid search is optional and has not yet been proven better on your biomedical PDFs; use the [benchmark guide](benchmarks/README.md) to compare it with keyword search.

## 8. Evaluate retrieval and back up your work

Use the [benchmark guide](benchmarks/README.md) to label real source pages and measure page hit rate, document hit rate, and missed or spurious retrieval. VIRA currently contains no human-labeled biomedical benchmark, so it cannot report a trustworthy accuracy percentage yet.

Run the application tests when needed:

```bash
python -m pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
```

Back up **both** `data/vira.db` and `data/library/`. Copy them while VIRA is stopped for a simple consistent backup. Never replace your existing `data/` directory with files from a fresh project copy.

## Quick troubleshooting

| Symptom | Check |
| --- | --- |
| `ollama: command not found` | Open Ollama, accept its CLI setup prompt, and open a new Terminal window. |
| VIRA reports that the local model failed | Check that Ollama is open, `ollama list` contains the exact model name, and `.env` matches it. |
| A PDF imports but searches find nothing | Check whether you can select and copy text from the PDF; image scans need OCR. |
| Port 8000 is already in use | Stop the earlier VIRA process in its Terminal window before starting another. |
| The interface still shows old text | Restart VIRA after changing Python settings, then refresh the browser page. |
