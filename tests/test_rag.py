import json
import tempfile
import unittest
from pathlib import Path

import httpx

from vira.evidence import validate_model_answer
from vira.evaluate import evaluate, load_cases
from vira.library import import_pdf
from vira.retrieval import IndexNotReady, search
from vira.storage import Storage
from test_api import sample_pdf


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.sources = [{'snippet':'The treatment group included 20 participants.'}]

    def test_exact_quote_and_source_number_required(self):
        claim = {'statement':'The treatment group included 20 participants',
                 'source':1, 'quote':'The treatment group included 20 participants.'}
        valid = json.dumps({'claims':[claim]})
        self.assertIn('Source quote:', validate_model_answer(valid, self.sources))
        self.assertIsNone(validate_model_answer(json.dumps({'claims':[{**claim, 'source':2}]}), self.sources))
        self.assertIsNone(validate_model_answer(json.dumps({'claims':[{**claim,
            'statement':'The treatment group included 30 participants'}]}), self.sources))
        self.assertIsNone(validate_model_answer(json.dumps({'claims':[{**claim,
            'quote':'The control group included 20 participants.'}]}), self.sources))
        self.assertIn('Insufficient evidence', validate_model_answer('{"claims":[]}', self.sources))
        repeated = json.dumps({'claims':[claim, {**claim, 'statement':claim['statement'] + '.'}]})
        self.assertEqual(validate_model_answer(repeated, self.sources).count('Source quote:'), 1)


class BenchmarkTests(unittest.IsolatedAsyncioTestCase):
    async def test_changed_embedding_dimensions_require_rebuild(self):
        with tempfile.TemporaryDirectory() as directory:
            storage = Storage(Path(directory) / 'test.db')
            import_pdf(storage, sample_pdf(), 'survival.pdf')
            pending = storage.passages_to_embed('test-model')
            storage.save_embeddings('test-model', ((item['id'], [1.0, 0.0]) for item in pending))
            def changed_model(request):
                return httpx.Response(200, json={'embeddings':[[1.0, 0.0, 0.0]]})
            async with httpx.AsyncClient(transport=httpx.MockTransport(changed_model),
                                         base_url='http://127.0.0.1:11434') as model:
                with self.assertRaisesRegex(IndexNotReady, 'Rebuild'):
                    await search(storage, 'hazard ratio', 'hybrid', model='test-model', client=model)

    async def test_generic_question_does_not_retrieve_unrelated_pdf(self):
        with tempfile.TemporaryDirectory() as directory:
            storage = Storage(Path(directory) / 'test.db')
            import_pdf(storage, sample_pdf(), 'survival.pdf')
            self.assertEqual(storage.search_passages('What does this paper say about the document?'), [])

    async def test_labeled_page_metrics_and_unanswerable_case(self):
        with tempfile.TemporaryDirectory() as directory:
            storage = Storage(Path(directory) / 'test.db')
            paper, _ = import_pdf(storage, sample_pdf(), 'survival.pdf')
            cases_path = Path(directory) / 'questions.jsonl'
            cases_path.write_text('\n'.join(json.dumps(item) for item in [
                {'id':'page-two', 'question':'hazard ratio', 'answerable':True,
                 'expected_sources':[{'sha256':paper['sha256'], 'page':2}]},
                {'id':'unknown', 'question':'genomics', 'answerable':False,
                 'expected_sources':[]}
            ]), encoding='utf-8')
            result = await evaluate(storage, load_cases(cases_path))
            self.assertEqual(result['page_hit_at_k'], 1.0)
            self.assertEqual(result['document_hit_at_k'], 1.0)
            self.assertEqual(result['page_mrr'], 1.0)
            self.assertEqual(result['unanswerable_with_results_rate'], 0.0)
            self.assertEqual(result['cases'][0]['hits'][0]['page'], 2)

    async def test_missing_labeled_pdf_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            storage = Storage(Path(directory) / 'test.db')
            with self.assertRaisesRegex(ValueError, 'not imported'):
                await evaluate(storage, [{'id':'missing', 'question':'test', 'answerable':True,
                                          'expected_sources':[{'sha256':'0' * 64, 'page':1}]}])
