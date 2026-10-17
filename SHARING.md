# Share VIRA through a public link

This guide gives anyone with the link access to **one shared PDF library, notes, and task list**. No login is required. VIRA and optional Ollama models continue running on your computer. Visitors need only a web browser. No domain or paid AI API is required for this temporary trial.

Anyone who finds or receives the URL can view, add, and delete the shared data. Do not import personal or confidential records. Back up the database and PDF folder before sharing.

Cloudflare Quick Tunnels provide a temporary HTTPS address without a Cloudflare account or domain. Cloudflare describes them as a development and testing feature, without an uptime guarantee. The address changes each time you start a new tunnel.

## 1. Install the free tunnel program

On the host computer, open Terminal and run:

```bash
brew install cloudflared
cloudflared --version
```

If Homebrew is unavailable, use the macOS download from the [official Cloudflare downloads page](https://developers.cloudflare.com/tunnel/downloads/).

## 2. Stop a VIRA process that is already running

If you started VIRA with `python run.py`, return to that Terminal window and press **Control+C**. The sharing command starts VIRA itself on the same local port. To leave the existing local server running, use `VIRA_PORT=8001 .venv/bin/python share.py` in Step 3 instead.

## 3. Start a temporary shared session

```bash
cd VIRA-Bio
.venv/bin/python share.py
```

After the tunnel starts, Terminal prints an `https://...trycloudflare.com` URL. Open it in a browser; VIRA opens without a login.

Share the URL with the people who should use VIRA. Every visitor can import, read, search, and delete the same PDFs, and can read or change the same notes and tasks. The existing `data/vira.db` and `data/library/` are used; the sharing command does not move them.

## 4. Check from another network

Open the URL on a phone using cellular data or another Internet connection. Both devices should see the same items in **PDF Library**. Import one permitted test PDF through **Manage PDFs → Add PDFs**, select **Find passages**, and open a cited page. If Ollama is configured in `.env` and running on the host computer, visitors can select **Ask a question** for cited answers. Visitors do not install Ollama.

If the browser shows a connection error, check that this Terminal window is still open, the host computer is awake and online, and no other VIRA process already occupies the configured port. If the browser shows `403 Host is not allowed`, stop sharing and restart `share.py` to obtain a fresh URL.

## 5. Stop sharing

Press **Control+C** in the sharing Terminal window. The temporary URL stops working. The next session will have a different URL. Your PDFs, notes, and tasks remain on the host computer.

## Later: use a stable website address

A permanent hostname needs a domain or another hosting arrangement. When you have one, set `VIRA_PUBLIC_ORIGIN` and `VIRA_PUBLIC_ANONYMOUS=1` in `.env`, keep `VIRA_HOST=127.0.0.1`, and route the HTTPS hostname through a tunnel to `http://127.0.0.1:8000`. The example values and a password-protected alternative are in `.env.example`. Do not expose the Ollama API port directly to the Internet.

Sources: [Cloudflare Quick Tunnels](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/), [Cloudflare tunnel installation](https://developers.cloudflare.com/tunnel/features/locally-managed-tunnels/create-local-tunnel/).
