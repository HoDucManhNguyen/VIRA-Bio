"""Evaluate retrieval against human-labeled PDF pages without sending data away."""
import argparse
import asyncio
import json
import os
import re
from pathlib import Path

from dotenv import load_dotenv

from vira.retrieval import search
from vira.storage import Storage


def load_cases(path):
    cases = []
    seen = set()
    for line_number, line in enumerate(Path(path).read_text(encoding='utf-8').splitlines(), 1):
        if not line.strip():
            continue
        try:
            case = json.loads(line)
            case_id = case['id']
            question = case['question']
            answerable = case['answerable']
            expected = case['expected_sources']
            if (not isinstance(case_id, str) or not case_id.strip() or case_id in seen
                    or not isinstance(question, str) or not question.strip()
                    or not isinstance(answerable, bool) or not isinstance(expected, list)
                    or answerable != bool(expected)):
                raise ValueError('Invalid id, question, answerable flag, or expected_sources')
            for source in expected:
                if (not isinstance(source, dict)
                        or not re.fullmatch(r'[0-9a-f]{64}', source['sha256'])
                        or not isinstance(source['page'], int) or source['page'] < 1):
                    raise ValueError('Each expected source needs a PDF SHA-256 and page >= 1')
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError(f'Invalid benchmark line {line_number}: {exc}') from exc
        seen.add(case_id)
        cases.append(case)
    if not cases:
        raise ValueError('Benchmark has no labeled questions')
    return cases


async def evaluate(storage, cases, mode='keyword', top_k=5, client=None):
    if top_k < 1 or top_k > 50:
        raise ValueError('top_k must be between 1 and 50')
    papers = {paper['sha256']:paper for paper in storage.papers()}
    hashes_by_id = {paper['id']:sha256 for sha256, paper in papers.items()}
    missing = {source['sha256'] for case in cases for source in case['expected_sources']} - papers.keys()
    if missing:
        raise ValueError(f'{len(missing)} labeled PDF(s) are not imported in this library')
    if any(source['page'] > papers[source['sha256']]['page_count']
           for case in cases for source in case['expected_sources']):
        raise ValueError('A labeled page exceeds its imported PDF page count')
    rows = []
    for case in cases:
        hits = await search(storage, case['question'], mode, top_k, client=client)
        expected_pages = {(papers[source['sha256']]['id'], source['page'])
                          for source in case['expected_sources']}
        expected_papers = {paper_id for paper_id, _ in expected_pages}
        page_rank = next((rank for rank, hit in enumerate(hits, 1)
                          if (hit['paper_id'], hit['page']) in expected_pages), None)
        document_rank = next((rank for rank, hit in enumerate(hits, 1)
                              if hit['paper_id'] in expected_papers), None)
        rows.append({'id':case['id'], 'answerable':case['answerable'],
                     'page_rank':page_rank, 'document_rank':document_rank,
                     'retrieved':len(hits),
                     'hits':[{'sha256':hashes_by_id[hit['paper_id']],
                              'page':hit['page']} for hit in hits]})
    answerable = [row for row in rows if row['answerable']]
    unanswerable = [row for row in rows if not row['answerable']]
    def rate(items, predicate):
        return sum(predicate(item) for item in items) / len(items) if items else None
    return {'mode':mode, 'top_k':top_k, 'questions':len(rows),
            'answerable_questions':len(answerable), 'unanswerable_questions':len(unanswerable),
            'page_hit_at_k':rate(answerable, lambda row: row['page_rank'] is not None),
            'document_hit_at_k':rate(answerable, lambda row: row['document_rank'] is not None),
            'page_mrr':(sum(1 / row['page_rank'] for row in answerable if row['page_rank'])
                        / len(answerable) if answerable else None),
            'unanswerable_with_results_rate':rate(unanswerable,
                                                  lambda row: row['retrieved'] > 0),
            'cases':rows}


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', help='Human-labeled JSONL benchmark')
    parser.add_argument('--db', default=None, help='VIRA SQLite database path')
    parser.add_argument('--list-papers', action='store_true', help='Show imported PDF hashes and pages')
    parser.add_argument('--mode', choices=('keyword', 'hybrid', 'both'), default='keyword')
    parser.add_argument('--top-k', type=int, default=5)
    parser.add_argument('--output', help='Optional path for the JSON report')
    args = parser.parse_args()
    load_dotenv()
    db_path = args.db or os.getenv('VIRA_DB_PATH', './data/vira.db')
    if not Path(db_path).is_file():
        parser.error(f'Database not found: {db_path}')
    storage = Storage(db_path)
    if args.list_papers:
        print(json.dumps([{'filename':paper['filename'], 'sha256':paper['sha256'],
                           'pages':paper['page_count']} for paper in storage.papers()], indent=2))
        return
    if not args.input:
        parser.error('--input is required unless --list-papers is used')
    cases = load_cases(args.input)
    modes = ('keyword', 'hybrid') if args.mode == 'both' else (args.mode,)
    report = {'benchmark':str(args.input),
              'results':[await evaluate(storage, cases, mode, args.top_k) for mode in modes]}
    rendered = json.dumps(report, indent=2)
    if args.output:
        Path(args.output).write_text(rendered + '\n', encoding='utf-8')
    print(rendered)


if __name__ == '__main__':
    asyncio.run(main())
