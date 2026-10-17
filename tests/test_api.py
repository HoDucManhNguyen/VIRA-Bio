import base64
import os
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from vira.app import create_app


def sample_pdf():
    contents = [b'BT /F1 12 Tf 50 750 Td (Survival analysis includes censoring and time to event.) Tj ET',
                b'BT /F1 12 Tf 50 750 Td (A Cox model estimates a hazard ratio.) Tj ET']
    objects = [
        b'<< /Type /Catalog /Pages 2 0 R >>',
        b'<< /Type /Pages /Kids [3 0 R 4 0 R] /Count 2 >>',
        b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 5 0 R >> >> /Contents 6 0 R >>',
        b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 5 0 R >> >> /Contents 7 0 R >>',
        b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
    ]
    objects.extend(b'<< /Length ' + str(len(content)).encode() + b' >>\nstream\n' +
                   content + b'\nendstream' for content in contents)
    result = bytearray(b'%PDF-1.4\n')
    offsets = [0]
    for number, obj in enumerate(objects, 1):
        offsets.append(len(result))
        result.extend(str(number).encode() + b' 0 obj\n' + obj + b'\nendobj\n')
    xref = len(result)
    result.extend(b'xref\n0 8\n0000000000 65535 f \n')
    for offset in offsets[1:]:
        result.extend(f'{offset:010d} 00000 n \n'.encode())
    result.extend(b'trailer\n<< /Size 8 /Root 1 0 R >>\nstartxref\n' +
                  str(xref).encode() + b'\n%%EOF')
    return bytes(result)


class APITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = create_app(Path(self.temp.name) / 'test.db')
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url='http://testserver')

    async def asyncTearDown(self):
        await self.client.aclose()
        self.temp.cleanup()

    async def test_library_import_search_citation_and_delete(self):
        pdf = sample_pdf()
        headers = {'Content-Type':'application/pdf', 'X-Filename':'survival.pdf',
                   'X-Rights-Confirmed':'true'}
        imported = await self.client.post('/api/library/import', content=pdf, headers=headers)
        self.assertEqual(imported.status_code, 201, imported.text)
        paper = imported.json()['paper']
        self.assertEqual(paper['page_count'], 2)
        duplicate = await self.client.post('/api/library/import', content=pdf, headers=headers)
        self.assertFalse(duplicate.json()['created'])
        hits = (await self.client.get('/api/library/search', params={'q':'hazard ratio'})).json()
        self.assertEqual(hits[0]['page'], 2)
        self.assertEqual(hits[0]['paper_id'], paper['id'])
        with patch.dict(os.environ, {'VIRA_PROVIDER':'extractive'}):
            reply = await self.client.post('/api/library/answer', json={
                'question':'What is a hazard ratio?'})
        self.assertEqual(reply.status_code, 200, reply.text)
        self.assertIn('PDF excerpts', reply.json()['answer'])
        self.assertEqual(reply.json()['sources'][0]['page'], 2)
        original = await self.client.get(f"/api/library/{paper['id']}/pdf")
        self.assertEqual(original.content, pdf)
        self.assertEqual((await self.client.delete(f"/api/library/{paper['id']}")).status_code, 200)
        self.assertEqual((await self.client.get('/api/library/search', params={'q':'hazard'})).json(), [])
        self.assertEqual((await self.client.get(f"/api/library/{paper['id']}/pdf")).status_code, 404)

    async def test_no_evidence_and_invalid_upload(self):
        reply = await self.client.post('/api/library/answer', json={
            'question':'unknown finding'})
        self.assertEqual(reply.status_code, 200)
        self.assertIn('No matching evidence', reply.json()['answer'])
        self.assertEqual(reply.json()['sources'], [])
        denied = await self.client.post('/api/library/import', content=sample_pdf(),
                                        headers={'Content-Type':'application/pdf'})
        self.assertEqual(denied.status_code, 400)
        invalid = await self.client.post('/api/library/import', content=b'not a PDF',
                                         headers={'Content-Type':'application/pdf',
                                                  'X-Rights-Confirmed':'true'})
        self.assertEqual(invalid.status_code, 400)

    async def test_existing_notes_tasks_and_local_guard(self):
        note = (await self.client.post('/api/notes', json={'title':'A','content':'Draft'})).json()
        edited = await self.client.put(f"/api/notes/{note['id']}", json={
            'title':'A','content':'Final'})
        self.assertEqual(edited.json()['content'], 'Final')
        task = (await self.client.post('/api/tasks', json={'title':'Review'})).json()
        self.assertEqual((await self.client.patch(f"/api/tasks/{task['id']}",
                      json={'completed':True})).status_code, 200)
        self.assertEqual((await self.client.get('/api/tasks')).json()[0]['completed'], 1)
        self.assertEqual((await self.client.get('/api/health', headers={
            'Host':'example.com'})).status_code, 403)

    async def test_default_has_no_paid_provider(self):
        with patch.dict(os.environ, {'VIRA_PROVIDER':'extractive'}):
            health = (await self.client.get('/api/health')).json()
            self.assertEqual(health['provider'], 'extractive')
            self.assertFalse(health['ai_configured'])

    async def test_public_access_requires_password_and_checks_origin(self):
        password = 'long-random-test-password'
        with patch.dict(os.environ, {'VIRA_PUBLIC_ORIGIN':'https://vira.example.com',
                                      'VIRA_ACCOUNTS':json.dumps({
                                          'manh':password, 'colleague':'another-long-test-password'})}):
            app = create_app(Path(self.temp.name) / 'public.db')
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url='https://vira.example.com') as public:
            unauthorized = await public.get('/')
            self.assertEqual(unauthorized.status_code, 401)
            self.assertIn('Basic', unauthorized.headers['www-authenticate'])
            self.assertEqual((await public.get('/static/app.js')).status_code, 401)
            self.assertEqual((await public.get('/api/library')).status_code, 401)
            bad = {'Authorization':'Basic ' + base64.b64encode(b'manh:wrong').decode()}
            self.assertEqual((await public.get('/', headers=bad)).status_code, 401)
            auth = {'Authorization':'Basic ' + base64.b64encode(
                ('manh:' + password).encode()).decode()}
            colleague = {'Authorization':'Basic ' + base64.b64encode(
                b'colleague:another-long-test-password').decode()}
            page = await public.get('/', headers=auth)
            self.assertEqual(page.status_code, 200)
            self.assertEqual(page.headers['cache-control'], 'no-store')
            pdf = await public.post('/api/library/import', content=sample_pdf(), headers={
                **auth, 'Content-Type':'application/pdf', 'X-Rights-Confirmed':'true',
                'Origin':'https://vira.example.com'})
            self.assertEqual(pdf.status_code, 201, pdf.text)
            self.assertEqual((await public.get('/api/library', headers=auth)).json()[0]['id'],
                             pdf.json()['paper']['id'])
            self.assertEqual((await public.get('/api/library', headers=colleague)).json()[0]['id'],
                             pdf.json()['paper']['id'])
            self.assertEqual((await public.get(
                f"/api/library/{pdf.json()['paper']['id']}/pdf", headers=colleague)).status_code, 200)
            with patch.dict(os.environ, {'VIRA_PROVIDER':'extractive'}):
                answer = await public.post('/api/library/answer', headers=colleague,
                                           json={'question':'hazard ratio'})
            self.assertEqual(answer.status_code, 200)
            self.assertEqual(answer.json()['sources'][0]['paper_id'], pdf.json()['paper']['id'])
            note = (await public.post('/api/notes', headers={**auth, 'Origin':'https://vira.example.com'},
                                      json={'title':'Private', 'content':'Only mine'})).json()
            self.assertEqual((await public.get('/api/notes', headers=colleague)).json()[0]['id'], note['id'])
            self.assertEqual((await public.delete(f"/api/notes/{note['id']}",
                                                   headers=colleague)).status_code, 200)
            forbidden = await public.post('/api/library/answer', headers={
                **auth, 'Origin':'https://attacker.example'}, json={'question':'hazard ratio'})
            self.assertEqual(forbidden.status_code, 403)
            cross_site = await public.post('/api/library/answer', headers={
                **auth, 'Sec-Fetch-Site':'cross-site'}, json={'question':'hazard ratio'})
            self.assertEqual(cross_site.status_code, 403)
            wrong_host = await public.get('/api/library', headers={**auth, 'Host':'elsewhere.example'})
            self.assertEqual(wrong_host.status_code, 403)

    async def test_public_access_rejects_missing_or_short_password(self):
        with patch.dict(os.environ, {'VIRA_PUBLIC_ORIGIN':'https://vira.example.com',
                                      'VIRA_ACCOUNTS':'', 'VIRA_PUBLIC_ANONYMOUS':''}):
            with self.assertRaises(ValueError):
                create_app(Path(self.temp.name) / 'unprotected.db')
        with patch.dict(os.environ, {'VIRA_PUBLIC_ORIGIN':'http://vira.example.com',
                                      'VIRA_ACCOUNTS':'{"manh":"long-random-test-password"}'}):
            with self.assertRaises(ValueError):
                create_app(Path(self.temp.name) / 'insecure.db')
        with patch.dict(os.environ, {'VIRA_PUBLIC_ORIGIN':'https://vira.example.com',
                                      'VIRA_ACCOUNTS':'{"manh":"short"}'}):
            with self.assertRaises(ValueError):
                create_app(Path(self.temp.name) / 'short.db')

    async def test_explicit_anonymous_public_access_shares_library(self):
        with patch.dict(os.environ, {'VIRA_PUBLIC_ORIGIN':'https://open.example.com',
                                      'VIRA_ACCOUNTS':'', 'VIRA_PUBLIC_ANONYMOUS':'1'}):
            app = create_app(Path(self.temp.name) / 'anonymous.db')
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url='https://open.example.com') as public:
            self.assertEqual((await public.get('/')).status_code, 200)
            self.assertEqual((await public.get('/api/library')).json(), [])
            imported = await public.post('/api/library/import', content=sample_pdf(), headers={
                'Content-Type':'application/pdf', 'X-Rights-Confirmed':'true',
                'Origin':'https://open.example.com'})
            self.assertEqual(imported.status_code, 201, imported.text)
            paper_id = imported.json()['paper']['id']
            self.assertEqual((await public.get('/api/library')).json()[0]['id'], paper_id)
            self.assertEqual((await public.get(f'/api/library/{paper_id}/pdf')).status_code, 200)
            with patch.dict(os.environ, {'VIRA_PROVIDER':'extractive'}):
                self.assertEqual((await public.post('/api/library/answer',
                                  json={'question':'hazard ratio'})).status_code, 200)
            self.assertEqual((await public.post('/api/library/answer', headers={
                'Origin':'https://another.example'}, json={'question':'hazard ratio'})).status_code, 403)
            self.assertEqual((await public.get('/api/library', headers={
                'Host':'another.example'})).status_code, 403)

    async def test_interface_assets_load(self):
        page = await self.client.get('/')
        script = await self.client.get('/static/app.js')
        self.assertEqual(page.status_code, 200)
        self.assertIn('PDF Library', page.text)
        self.assertIn('Ask a question', page.text)
        self.assertNotIn('Ask VIRA', page.text)
        self.assertEqual(page.headers['cache-control'], 'no-store')
        self.assertEqual(page.headers['content-language'], 'en')
        self.assertEqual(script.status_code, 200)
        self.assertEqual(script.headers['cache-control'], 'no-store')
        self.assertIn('async function importPaper()', script.text)
        self.assertIn('async function answerFromLibrary()', script.text)

    async def test_ollama_uses_local_evidence(self):
        requests = []

        def answer(request):
            requests.append(request)
            return httpx.Response(200, json={'message':{'content':
                json.dumps({'claims':[{'statement':'A Cox model estimates a hazard ratio',
                                       'source':1, 'quote':'A Cox model estimates a hazard ratio.'}]})}})

        async with httpx.AsyncClient(transport=httpx.MockTransport(answer),
                                     base_url='http://127.0.0.1:11434') as model:
            app = create_app(Path(self.temp.name) / 'ollama.db', client=model)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                         base_url='http://testserver') as api:
                await api.post('/api/library/import', content=sample_pdf(), headers={
                    'Content-Type':'application/pdf', 'X-Rights-Confirmed':'true'})
                with patch.dict(os.environ, {'VIRA_PROVIDER':'ollama', 'VIRA_MODEL':'local-test'}):
                    result = await api.post('/api/library/answer', json={
                        'question':'hazard ratio'})
                self.assertEqual(result.status_code, 200, result.text)
                self.assertIn('A Cox model estimates a hazard ratio [1]', result.json()['answer'])
                self.assertIn('Source quote:', result.json()['answer'])
                self.assertEqual(len(requests), 1)
                self.assertEqual(requests[0].url.host, '127.0.0.1')
                self.assertIn('hazard ratio', requests[0].content.decode())
                self.assertIn('format', json.loads(requests[0].content))

    async def test_ollama_uncheckable_claim_falls_back_to_excerpts(self):
        def answer(request):
            return httpx.Response(200, json={'message':{'content':
                json.dumps({'claims':[{'statement':'The hazard ratio was 0.20',
                                       'source':1, 'quote':'A Cox model estimates a hazard ratio.'}]})}})

        async with httpx.AsyncClient(transport=httpx.MockTransport(answer),
                                     base_url='http://127.0.0.1:11434') as model:
            app = create_app(Path(self.temp.name) / 'uncheckable.db', client=model)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                         base_url='http://testserver') as api:
                await api.post('/api/library/import', content=sample_pdf(), headers={
                    'Content-Type':'application/pdf', 'X-Rights-Confirmed':'true'})
                with patch.dict(os.environ, {'VIRA_PROVIDER':'ollama', 'VIRA_MODEL':'local-test'}):
                    result = await api.post('/api/library/answer', json={
                        'question':'hazard ratio'})
                self.assertEqual(result.status_code, 200, result.text)
                self.assertIn('did not provide checkable source quotes', result.json()['answer'])
                self.assertNotIn('0.20', result.json()['answer'])
                self.assertEqual(result.json()['sources'][0]['page'], 2)

    async def test_hybrid_index_and_search_use_local_embeddings(self):
        requests = []

        def embeddings(request):
            requests.append(request)
            texts = json.loads(request.content)['input']
            vectors = [[1.0, 0.0] if 'Survival analysis' in text or 'follow up' in text
                       else [0.0, 1.0] for text in texts]
            return httpx.Response(200, json={'embeddings':vectors})

        async with httpx.AsyncClient(transport=httpx.MockTransport(embeddings),
                                     base_url='http://127.0.0.1:11434') as model:
            app = create_app(Path(self.temp.name) / 'hybrid.db', client=model)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                         base_url='http://testserver') as api:
                imported = await api.post('/api/library/import', content=sample_pdf(), headers={
                    'Content-Type':'application/pdf', 'X-Rights-Confirmed':'true'})
                before = await api.get('/api/library/search', params={
                    'q':'follow up', 'mode':'hybrid'})
                self.assertEqual(before.status_code, 409)
                built = await api.post('/api/retrieval/index')
                self.assertEqual(built.status_code, 200, built.text)
                self.assertTrue(built.json()['ready'])
                hits = (await api.get('/api/library/search', params={
                    'q':'follow up', 'mode':'hybrid'})).json()
                self.assertEqual(hits[0]['page'], 1)
                self.assertTrue(all(request.url.path == '/api/embed' for request in requests))
                reset = (await api.post('/api/retrieval/reset')).json()
                self.assertEqual(reset['indexed'], 0)
                rebuilt = (await api.post('/api/retrieval/index')).json()
                self.assertTrue(rebuilt['ready'])
                await api.delete(f"/api/library/{imported.json()['paper']['id']}")
                status = (await api.get('/api/retrieval/status')).json()
                self.assertEqual((status['indexed'], status['total']), (0, 0))


if __name__ == '__main__':
    unittest.main()
