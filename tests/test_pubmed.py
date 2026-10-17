import unittest

import httpx

from vira.pubmed import search_pubmed


class PubMedTests(unittest.IsolatedAsyncioTestCase):
    async def test_metadata_only_search(self):
        requests = []

        def respond(request):
            requests.append(request)
            if request.url.path.endswith('esearch.fcgi'):
                return httpx.Response(200, json={'esearchresult':{'idlist':['12345']}})
            return httpx.Response(200, json={'result':{'12345':{
                'title':'Survival study', 'fulljournalname':'Example Journal',
                'pubdate':'2025 Jan', 'authors':[{'name':'A Author'}],
                'articleids':[{'idtype':'doi','value':'10.1234/example'}]}}})

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            results = await search_pubmed('survival analysis', client)
        self.assertEqual(results[0]['pmid'], '12345')
        self.assertEqual(results[0]['doi'], '10.1234/example')
        self.assertEqual(results[0]['url'], 'https://pubmed.ncbi.nlm.nih.gov/12345/')
        self.assertEqual(len(requests), 2)
        self.assertTrue(all('eutils.ncbi.nlm.nih.gov' == req.url.host for req in requests))


if __name__ == '__main__':
    unittest.main()
