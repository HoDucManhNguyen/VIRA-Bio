"""Optional local semantic indexing and reciprocal-rank fusion."""
import math
import os
from urllib.parse import urlsplit

import httpx


class IndexNotReady(ValueError):
    pass


def embedding_model():
    return os.getenv('VIRA_EMBED_MODEL', 'nomic-embed-text').strip()


def ollama_base_url():
    base = os.getenv('VIRA_OLLAMA_URL', 'http://127.0.0.1:11434')
    parsed = urlsplit(base)
    if (parsed.scheme != 'http' or parsed.hostname not in {'127.0.0.1', 'localhost'}
            or parsed.username or parsed.password or parsed.path not in {'', '/'}
            or parsed.query or parsed.fragment):
        raise ValueError('Ollama must run on localhost over HTTP')
    return base.rstrip('/')


async def embed_texts(texts, model, client=None):
    if not model:
        raise ValueError('Set VIRA_EMBED_MODEL to an installed Ollama embedding model')
    if not texts:
        return []
    payload = {'model':model, 'input':texts, 'truncate':True}
    if client is None:
        async with httpx.AsyncClient(base_url=ollama_base_url(), timeout=120) as session:
            response = await session.post('/api/embed', json=payload)
    else:
        response = await client.post('/api/embed', json=payload)
    response.raise_for_status()
    vectors = response.json().get('embeddings')
    if not isinstance(vectors, list) or len(vectors) != len(texts):
        raise ValueError('Ollama returned an invalid embedding response')
    width = None
    for vector in vectors:
        if not isinstance(vector, list) or not vector or not all(
                isinstance(value, (int, float)) and math.isfinite(value) for value in vector):
            raise ValueError('Ollama returned an invalid embedding vector')
        if not any(value != 0 for value in vector):
            raise ValueError('Ollama returned a zero embedding vector')
        if width is None:
            width = len(vector)
        elif len(vector) != width:
            raise ValueError('Ollama returned inconsistent embedding dimensions')
    return vectors


async def build_index_batch(storage, model, client=None, batch_size=16):
    pending = storage.passages_to_embed(model, batch_size)
    if pending:
        vectors = await embed_texts([item['text'] for item in pending], model, client)
        dimension = storage.embedding_dimension(model)
        if dimension is not None and len(vectors[0]) != dimension:
            raise IndexNotReady('Embedding dimensions changed. Rebuild the semantic index in PDF Library')
        storage.save_embeddings(model, zip((item['id'] for item in pending), vectors))
    return storage.embedding_status(model)


async def search(storage, query, mode='keyword', limit=12, model=None, client=None):
    if mode not in {'keyword', 'hybrid'}:
        raise ValueError('Search mode must be keyword or hybrid')
    if not query.strip():
        return []
    lexical = storage.search_passages(query, 30 if mode == 'hybrid' else limit)
    if mode == 'keyword':
        return lexical
    model = model or embedding_model()
    status = storage.embedding_status(model)
    if status['total'] == 0:
        return []
    if not status['ready']:
        raise IndexNotReady('Build the semantic index in PDF Library before using hybrid search')
    query_vector = (await embed_texts([query], model, client))[0]
    if len(query_vector) != storage.embedding_dimension(model):
        raise IndexNotReady('Embedding dimensions changed. Rebuild the semantic index in PDF Library')
    semantic = storage.search_embeddings(model, query_vector, 30)
    combined = {}
    for results in (lexical, semantic):
        for rank, item in enumerate(results, 1):
            key = item['passage_id']
            if key not in combined:
                combined[key] = {**item, 'fusion_score':0.0}
            combined[key]['fusion_score'] += 1 / (60 + rank)
    ranked = sorted(combined.values(), key=lambda item: item['fusion_score'], reverse=True)
    for item in ranked:
        item.pop('fusion_score')
    return ranked[:limit]
