"""Share the local VIRA workspace through a temporary HTTPS URL."""

import os
import re
import select
import shutil
import socket
import subprocess
import sys
import threading
import time

import uvicorn
from dotenv import load_dotenv


TUNNEL_URL = re.compile(r'https://[a-z0-9-]+\.trycloudflare\.com\b')


def wait_for_tunnel(process, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError('Cloudflare Tunnel stopped before providing a URL')
        ready, _, _ = select.select([process.stdout], [], [], 0.5)
        if ready:
            line = process.stdout.readline()
            match = TUNNEL_URL.search(line)
            if match:
                return match.group(0)
    raise RuntimeError('Cloudflare Tunnel did not provide a URL within one minute')


def drain_tunnel_output(process):
    for line in process.stdout:
        sys.stderr.write(line)


def main():
    load_dotenv()
    cloudflared = shutil.which('cloudflared')
    if not cloudflared:
        raise SystemExit('cloudflared is not installed. See SHARING.md for installation steps.')
    try:
        port = int(os.getenv('VIRA_PORT', '8000'))
    except ValueError as exc:
        raise SystemExit('VIRA_PORT must be a valid port number') from exc
    if not 1 <= port <= 65535:
        raise SystemExit('VIRA_PORT must be between 1 and 65535')
    with socket.socket() as probe:
        try:
            probe.bind(('127.0.0.1', port))
        except OSError as exc:
            raise SystemExit(f'Port {port} is unavailable. Stop the existing VIRA process first.') from exc

    process = subprocess.Popen(
        [cloudflared, 'tunnel', '--url', f'http://127.0.0.1:{port}'],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
    )
    try:
        url = wait_for_tunnel(process)
        os.environ['VIRA_PUBLIC_ORIGIN'] = url
        os.environ['VIRA_ACCOUNTS'] = ''
        os.environ['VIRA_PUBLIC_ANONYMOUS'] = '1'
        threading.Thread(target=drain_tunnel_output, args=(process,), daemon=True).start()
        print('\nVIRA is available temporarily at:', url, flush=True)
        print('No login is required. Anyone with this URL can read, add, or delete shared data.', flush=True)
        print('Keep this window and your Mac awake. Press Control+C to stop sharing.\n', flush=True)
        uvicorn.run('vira.app:app', host='127.0.0.1', port=port)
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


if __name__ == '__main__':
    main()
