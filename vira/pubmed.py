"""On-demand PubMed metadata lookup. Never fetch or store article full text."""
import asyncio
import time

import httpx


EUTILS = 'https://eutils.ncbi.nlm.nih.gov/entrez/eutils/'
_lock = asyncio.Lock()
_last_request = 0.0


async def _get(client, endpoint, params):
    global _last_request
    async with _lock:
        remaining = 0.4 - (time.monotonic() - _last_request)
        if remaining > 0:
            await asyncio.sleep(remaining)
        _last_request = time.monotonic()
        response = await client.get(EUTILS + endpoint, params={**params, 'tool':'VIRABio'})
    response.raise_for_status()
    return response.json()


async def search_pubmed(query, client=None):
    if not query.strip():
        return []
    async def run(session):
        found = await _get(session, 'esearch.fcgi', {
            'db':'pubmed', 'term':query[:300], 'retmode':'json', 'retmax':'10', 'sort':'pub date'})
        ids = found.get('esearchresult', {}).get('idlist', [])
        if not ids:
            return []
        summary = await _get(session, 'esummary.fcgi', {
            'db':'pubmed', 'id':','.join(ids), 'retmode':'json', 'version':'2.0'})
        records = summary.get('result', {})
        output = []
        for pmid in ids:
            item = records.get(pmid, {})
            if not item:
                continue
            doi = next((ident.get('value','') for ident in item.get('articleids', [])
                        if ident.get('idtype') == 'doi'), '')
            output.append({
                'pmid':pmid, 'title':item.get('title',''),
                'journal':item.get('fulljournalname') or item.get('source',''),
                'date':item.get('pubdate',''), 'doi':doi,
                'authors':', '.join(author.get('name','') for author in item.get('authors',[])[:6]),
                'url':f'https://pubmed.ncbi.nlm.nih.gov/{pmid}/'
            })
        return output
    if client is not None:
        return await run(client)
    async with httpx.AsyncClient(timeout=15) as session:
        return await run(session)
