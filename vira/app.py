"""VIRA API with optional password protection for a small shared workspace."""
import base64
import binascii
import hmac
import json
import os
import logging
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit
from typing import Literal

import httpx
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from vira.storage import Storage
from vira.library import MAX_PDF_BYTES, ImportErrorPDF, import_pdf, pdf_path
from vira.pubmed import search_pubmed
from vira.evidence import ANSWER_FORMAT, NO_SUPPORTED_ANSWER, SYSTEM_PROMPT, validate_model_answer
from vira.retrieval import (IndexNotReady, build_index_batch, embedding_model,
                            ollama_base_url, search)

load_dotenv()
logging.basicConfig(level=logging.INFO)
LOG=logging.getLogger('vira')
APP_DIR=Path(__file__).resolve().parent

class LibraryQuestion(BaseModel):
    question: str=Field(min_length=1,max_length=10000)
    mode: Literal['keyword', 'hybrid']='keyword'

class NameInput(BaseModel):
    title: str=Field(min_length=1,max_length=150)

class NoteInput(BaseModel):
    title: str=Field(min_length=1,max_length=150)
    content: str=Field(default='',max_length=50000)

class TaskStatus(BaseModel):
    completed: bool


def public_origin_from_env():
    value = os.getenv('VIRA_PUBLIC_ORIGIN', '').strip().rstrip('/')
    if not value:
        return None
    parsed = urlsplit(value)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
            or parsed.path or parsed.query or parsed.fragment or parsed.port not in (None, 443)):
        raise ValueError('VIRA_PUBLIC_ORIGIN must be an HTTPS origin such as https://vira.example.com')
    return value.lower()


def accounts_from_env():
    raw = os.getenv('VIRA_ACCOUNTS', '').strip()
    if not raw:
        return {}
    try:
        accounts = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError('VIRA_ACCOUNTS must be a JSON object of usernames and passwords') from exc
    if (not isinstance(accounts, dict) or not accounts
            or any(not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,32}', name)
                   or not isinstance(password, str) or len(password) < 16
                   for name, password in accounts.items())
            or len(set(accounts.values())) != len(accounts)):
        raise ValueError('VIRA_ACCOUNTS needs unique usernames and distinct passwords of at least 16 characters')
    return accounts


def authenticated_user(authorization, accounts):
    if not authorization.startswith('Basic '):
        return None
    try:
        decoded = base64.b64decode(authorization[6:].strip(), validate=True).decode('utf-8')
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return None
    username, separator, supplied = decoded.partition(':')
    expected = accounts.get(username)
    if separator and expected and hmac.compare_digest(supplied, expected):
        return username
    return None


def current_storage(request: Request):
    return request.state.storage

def create_app(db_path=None, client=None):
    public_origin = public_origin_from_env()
    accounts = accounts_from_env()
    anonymous_public_access = os.getenv('VIRA_PUBLIC_ANONYMOUS', '') == '1'
    if public_origin and not accounts and not anonymous_public_access:
        raise ValueError('Set VIRA_ACCOUNTS or VIRA_PUBLIC_ANONYMOUS=1 before enabling public access')
    app=FastAPI(title='VIRA Bio', version='0.1.0',docs_url=None,redoc_url=None)
    local_db_path = Path(db_path or os.getenv('VIRA_DB_PATH','./data/vira.db'))
    app.state.storage = Storage(local_db_path)
    app.state.ai_client=client
    app.mount('/static',StaticFiles(directory=APP_DIR/'static'),name='static')

    @app.middleware('http')
    async def access_guard(request:Request,call_next):
        # Keep the server on loopback; an HTTPS tunnel may forward the public Host header.
        host=request.url.hostname.lower() if request.url.hostname else ''
        origin=request.headers.get('origin')
        allowed_hosts={'127.0.0.1','localhost','testserver'}
        if public_origin:
            allowed_hosts.add(urlsplit(public_origin).hostname)
        if host not in allowed_hosts:
            return JSONResponse({'detail':'Host is not allowed'},status_code=403)
        if request.headers.get('sec-fetch-site') == 'cross-site':
            return JSONResponse({'detail':'Cross-site requests are blocked'},status_code=403)
        if origin:
            parts=urlsplit(origin)
            local_origin=(parts.scheme in {'http','https'} and parts.hostname == host
                          and parts.port == request.url.port and host in {'127.0.0.1','localhost','testserver'})
            if not local_origin and origin.lower() != public_origin:
                return JSONResponse({'detail':'Cross-origin requests are blocked'},status_code=403)
        if accounts:
            username = authenticated_user(request.headers.get('authorization',''), accounts)
            if username is None:
                return JSONResponse({'detail':'Authentication required'},status_code=401,
                                    headers={'WWW-Authenticate':'Basic realm="VIRA Bio", charset="UTF-8"',
                                             'Cache-Control':'no-store'})
        request.state.storage = app.state.storage
        response = await call_next(request)
        if public_origin or request.url.path == '/' or request.url.path.startswith('/static/'):
            response.headers['Cache-Control'] = 'no-store'
            response.headers['Content-Language'] = 'en'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'same-origin'
        return response

    @app.get('/')
    async def index():
        return FileResponse(APP_DIR/'static'/'index.html')

    @app.get('/api/health')
    async def health():
        provider = os.getenv('VIRA_PROVIDER', 'extractive').lower()
        return {'status':'ok', 'provider':provider,
                'model':os.getenv('VIRA_MODEL','') if provider == 'ollama' else None,
                'ai_configured': provider == 'ollama' and bool(os.getenv('VIRA_MODEL','').strip()),
                'public_access': bool(public_origin and anonymous_public_access and not accounts)}

    @app.get('/api/library')
    async def list_library(storage: Storage = Depends(current_storage)):
        return storage.papers()

    @app.post('/api/library/import', status_code=201)
    async def upload_pdf(request: Request, storage: Storage = Depends(current_storage)):
        if request.headers.get('content-type', '').split(';')[0] != 'application/pdf':
            raise HTTPException(415, 'Choose a PDF file')
        if request.headers.get('x-rights-confirmed') != 'true':
            raise HTTPException(400, 'Confirm that you have permission to store this PDF')
        data = bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data) > MAX_PDF_BYTES:
                raise HTTPException(413, 'PDF exceeds the 25 MB limit')
        try:
            paper, created = import_pdf(storage, bytes(data), unquote(request.headers.get('x-filename','paper.pdf')))
        except ImportErrorPDF as exc:
            raise HTTPException(400, str(exc)) from exc
        return {'paper': paper, 'created': created}

    @app.get('/api/library/{paper_id}/pdf')
    async def get_pdf(paper_id: str, storage: Storage = Depends(current_storage)):
        paper = storage.paper(paper_id)
        if not paper:
            raise HTTPException(404, 'Paper not found')
        return FileResponse(pdf_path(storage, paper), media_type='application/pdf',
                            content_disposition_type='inline')

    @app.get('/api/library/search')
    async def search_library(q: str = '', mode: Literal['keyword', 'hybrid'] = 'keyword',
                             storage: Storage = Depends(current_storage)):
        try:
            return await search(storage, q, mode, 12, client=client)
        except IndexNotReady as exc:
            raise HTTPException(409, str(exc)) from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise HTTPException(502, 'Local embedding model unavailable. Check Ollama and VIRA_EMBED_MODEL.') from exc

    @app.post('/api/library/answer')
    async def answer_from_library(body: LibraryQuestion, storage: Storage = Depends(current_storage)):
        question = body.question.strip()
        if not question:
            raise HTTPException(422, 'Enter a question about your PDF library')
        try:
            sources = await search(storage, question, body.mode, 5, client=client)
        except IndexNotReady as exc:
            raise HTTPException(409, str(exc)) from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise HTTPException(502, 'Local embedding model unavailable. Check Ollama and VIRA_EMBED_MODEL.') from exc
        if not sources:
            return {'answer': 'No matching evidence found in the imported PDFs. Import a relevant PDF or try different search terms.',
                    'sources': []}

        provider = os.getenv('VIRA_PROVIDER', 'extractive').lower()
        if provider == 'extractive':
            answer = ('Related PDF excerpts are shown below. Check the original pages before drawing a conclusion.')
        elif provider == 'ollama':
            model = os.getenv('VIRA_MODEL', '').strip()
            if not model:
                raise HTTPException(503, 'Set VIRA_MODEL to an installed Ollama model')
            try:
                base = ollama_base_url()
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
            excerpts = '\n\n'.join(f"[{i}] {source['title']}, p. {source['page']}: {source['snippet']}"
                                    for i, source in enumerate(sources, 1))
            messages = [
                {'role': 'system', 'content': SYSTEM_PROMPT},
                {'role': 'user', 'content': f'Current evidence excerpts:\n{excerpts}\n\nQuestion: {question}'},
            ]
            try:
                if client:
                    response = await client.post('/api/chat', json={
                        'model': model, 'messages': messages, 'stream': False,
                        'format': ANSWER_FORMAT, 'options': {'temperature': 0}})
                else:
                    async with httpx.AsyncClient(base_url=base, timeout=90) as local_client:
                        response = await local_client.post('/api/chat', json={
                            'model': model, 'messages': messages, 'stream': False,
                            'format': ANSWER_FORMAT, 'options': {'temperature': 0}})
                response.raise_for_status()
                raw_answer = response.json()['message']['content'].strip()
                if not raw_answer:
                    raise ValueError('Empty model response')
                answer = validate_model_answer(raw_answer, sources)
                if answer is None:
                    answer = ('The local model did not provide checkable source quotes. '
                              'Related excerpts are shown below instead. Check the original PDF.')
                elif answer == NO_SUPPORTED_ANSWER:
                    sources = []
            except (httpx.HTTPError, KeyError, ValueError) as exc:
                LOG.warning('Local model unavailable: %s', type(exc).__name__)
                raise HTTPException(502, 'Local model failed. Check Ollama and VIRA_MODEL.') from exc
        else:
            raise HTTPException(400, 'Unknown VIRA_PROVIDER; use extractive or ollama')
        return {'answer': answer, 'sources': sources}

    @app.get('/api/retrieval/status')
    async def retrieval_status(storage: Storage = Depends(current_storage)):
        return storage.embedding_status(embedding_model())

    @app.post('/api/retrieval/reset')
    async def reset_retrieval_index(storage: Storage = Depends(current_storage)):
        model = embedding_model()
        storage.clear_embeddings(model)
        return storage.embedding_status(model)

    @app.post('/api/retrieval/index')
    async def index_library(storage: Storage = Depends(current_storage)):
        try:
            return await build_index_batch(storage, embedding_model(), client=client)
        except IndexNotReady as exc:
            raise HTTPException(409, str(exc)) from exc
        except (httpx.HTTPError, ValueError) as exc:
            LOG.warning('Semantic indexing failed: %s', type(exc).__name__)
            raise HTTPException(502, 'Local embedding model unavailable. Start Ollama and install VIRA_EMBED_MODEL.') from exc

    @app.get('/api/pubmed/search')
    async def pubmed_lookup(q: str = ''):
        if len(q) > 300:
            raise HTTPException(400,'Search query is too long')
        try:
            return await search_pubmed(q)
        except (httpx.HTTPError, ValueError) as exc:
            LOG.warning('PubMed lookup failed: %s',type(exc).__name__)
            raise HTTPException(502,'PubMed is unavailable. Retry later; local library still works.') from exc

    @app.delete('/api/library/{paper_id}')
    async def remove_pdf(paper_id: str, storage: Storage = Depends(current_storage)):
        paper = storage.paper(paper_id)
        if not paper:
            raise HTTPException(404, 'Paper not found')
        storage.delete_paper(paper_id)
        pdf_path(storage, paper).unlink(missing_ok=True)
        return {'ok':True}

    @app.get('/api/notes')
    async def get_notes(storage: Storage = Depends(current_storage)):return storage.notes()

    @app.post('/api/notes',status_code=201)
    async def add_note(data:NoteInput, storage: Storage = Depends(current_storage)):
        return storage.save_note(data.title,data.content)

    @app.put('/api/notes/{nid}')
    async def edit_note(nid:str,data:NoteInput, storage: Storage = Depends(current_storage)):
        row=storage.save_note(data.title,data.content,nid)
        if not row:raise HTTPException(404,'Note not found')
        return row

    @app.delete('/api/notes/{nid}')
    async def delete_note(nid:str, storage: Storage = Depends(current_storage)):
        if not storage.delete_note(nid):raise HTTPException(404,'Note not found')
        return {'ok':True}

    @app.get('/api/tasks')
    async def get_tasks(storage: Storage = Depends(current_storage)):return storage.tasks()

    @app.post('/api/tasks',status_code=201)
    async def new_task(data:NameInput, storage: Storage = Depends(current_storage)):
        return storage.create_task(data.title)

    @app.patch('/api/tasks/{tid}')
    async def update_task(tid:str,data:TaskStatus, storage: Storage = Depends(current_storage)):
        if not storage.set_task(tid,data.completed):raise HTTPException(404,'Task not found')
        return {'ok':True}

    @app.delete('/api/tasks/{tid}')
    async def delete_task(tid:str, storage: Storage = Depends(current_storage)):
        if not storage.delete_task(tid):raise HTTPException(404,'Task not found')
        return {'ok':True}

    return app

app=create_app()
