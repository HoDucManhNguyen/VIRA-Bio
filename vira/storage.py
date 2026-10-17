"""SQLite persistence; parameterized queries, one connection per operation."""
import sqlite3
import uuid
import re
import json
import math
from contextlib import contextmanager
from pathlib import Path
from datetime import datetime, timezone


def now():
    return datetime.now(timezone.utc).isoformat()


QUERY_STOPWORDS = frozenset('''a an and are as at be by can could did do does for from
    how in is it me my of on or our please say says show tell that the these this those
    to was were what when where which who why with you your document paper report'''.split())


class Storage:
    def __init__(self, path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL, created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL,
                    role TEXT NOT NULL CHECK(role IN ('user','assistant')),
                    content TEXT NOT NULL, created_at TEXT NOT NULL,
                    FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id, created_at);
                CREATE TABLE IF NOT EXISTS notes (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL,
                    content TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL,
                    completed INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS papers (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL, filename TEXT NOT NULL,
                    sha256 TEXT NOT NULL UNIQUE, page_count INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS passages (
                    id INTEGER PRIMARY KEY, paper_id TEXT NOT NULL, page INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    FOREIGN KEY(paper_id) REFERENCES papers(id) ON DELETE CASCADE
                );
                CREATE VIRTUAL TABLE IF NOT EXISTS passages_fts USING fts5(
                    text, content='passages', content_rowid='id', tokenize='unicode61 remove_diacritics 2'
                );
                CREATE TABLE IF NOT EXISTS passage_embeddings (
                    passage_id INTEGER NOT NULL, model TEXT NOT NULL, vector TEXT NOT NULL,
                    PRIMARY KEY (passage_id, model),
                    FOREIGN KEY(passage_id) REFERENCES passages(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS message_sources (
                    message_id TEXT NOT NULL, paper_id TEXT NOT NULL, page INTEGER NOT NULL,
                    title TEXT NOT NULL, snippet TEXT NOT NULL,
                    FOREIGN KEY(message_id) REFERENCES messages(id) ON DELETE CASCADE
                );
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def conversations(self):
        with self.connect() as db:
            return [dict(x) for x in db.execute('SELECT * FROM conversations ORDER BY updated_at DESC')]

    def create_conversation(self, title='New conversation'):
        item = {'id':str(uuid.uuid4()), 'title':title[:100], 'created_at':now(), 'updated_at':now()}
        with self.connect() as db:
            db.execute('INSERT INTO conversations VALUES (:id,:title,:created_at,:updated_at)',item)
        return item

    def conversation(self, cid):
        with self.connect() as db:
            row=db.execute('SELECT * FROM conversations WHERE id=?',(cid,)).fetchone()
            return dict(row) if row else None

    def messages(self, cid):
        with self.connect() as db:
            rows = [dict(x) for x in db.execute('SELECT * FROM messages WHERE conversation_id=? ORDER BY created_at,rowid',(cid,))]
            for row in rows:
                row['sources'] = [dict(x) for x in db.execute(
                    'SELECT paper_id, page, title, snippet FROM message_sources WHERE message_id=?', (row['id'],))]
            return rows

    def add_message(self, cid, role, content):
        item={'id':str(uuid.uuid4()),'conversation_id':cid,'role':role,'content':content,'created_at':now()}
        with self.connect() as db:
            db.execute('INSERT INTO messages VALUES (:id,:conversation_id,:role,:content,:created_at)',item)
            db.execute('UPDATE conversations SET updated_at=? WHERE id=?',(item['created_at'],cid))
        return item

    def add_exchange(self, cid, question, answer, sources=()):
        """Save a completed user/assistant turn as one transaction."""
        timestamp = now()
        user = {'id':str(uuid.uuid4()), 'conversation_id':cid, 'role':'user',
                'content':question, 'created_at':timestamp}
        assistant = {'id':str(uuid.uuid4()), 'conversation_id':cid, 'role':'assistant',
                     'content':answer, 'created_at':timestamp}
        with self.connect() as db:
            for item in (user, assistant):
                db.execute('INSERT INTO messages VALUES (:id,:conversation_id,:role,:content,:created_at)',item)
            for source in sources:
                db.execute('INSERT INTO message_sources VALUES (?,?,?,?,?)',
                           (assistant['id'], source['paper_id'], source['page'],
                            source['title'], source['snippet']))
            db.execute('UPDATE conversations SET updated_at=?, title=CASE '
                       "WHEN title='New conversation' AND "
                       '(SELECT COUNT(*) FROM messages WHERE conversation_id=?)=2 '
                       'THEN ? ELSE title END WHERE id=?',
                       (timestamp, cid, question.strip()[:65], cid))
        return assistant

    def paper_by_hash(self, sha256):
        with self.connect() as db:
            row = db.execute('SELECT * FROM papers WHERE sha256=?', (sha256,)).fetchone()
            return dict(row) if row else None

    def paper(self, paper_id):
        with self.connect() as db:
            row = db.execute('SELECT * FROM papers WHERE id=?', (paper_id,)).fetchone()
            return dict(row) if row else None

    def papers(self):
        with self.connect() as db:
            return [dict(x) for x in db.execute('SELECT * FROM papers ORDER BY created_at DESC')]

    def add_paper(self, title, filename, sha256, pages):
        paper_id = str(uuid.uuid4())
        item = (paper_id, title, filename, sha256, len(pages), now())
        with self.connect() as db:
            db.execute('INSERT INTO papers VALUES (?,?,?,?,?,?)', item)
            for page_number, text in pages:
                for passage in _chunks(text):
                    cur = db.execute('INSERT INTO passages(paper_id,page,text) VALUES (?,?,?)',
                                     (paper_id, page_number, passage))
                    db.execute('INSERT INTO passages_fts(rowid,text) VALUES (?,?)',
                               (cur.lastrowid, passage))
        return self.paper(paper_id)

    def delete_paper(self, paper_id):
        with self.connect() as db:
            if not db.execute('SELECT 1 FROM papers WHERE id=?', (paper_id,)).fetchone():
                return False
            db.execute('DELETE FROM passages_fts WHERE rowid IN '
                       '(SELECT id FROM passages WHERE paper_id=?)', (paper_id,))
            db.execute('DELETE FROM papers WHERE id=?', (paper_id,))
            return True

    def search_passages(self, query, limit=8):
        tokens = [token for token in re.findall(r'\w+', query, flags=re.UNICODE)
                  if len(token) > 1 and token.casefold() not in QUERY_STOPWORDS][:12]
        if not tokens:
            return []
        expression = ' OR '.join('"' + token.replace('"', '') + '"' for token in tokens)
        with self.connect() as db:
            return [dict(x) for x in db.execute('''
                SELECT p.id AS passage_id, p.paper_id, p.page, p.text AS snippet, d.title,
                       bm25(passages_fts) AS score
                FROM passages_fts JOIN passages p ON p.id=passages_fts.rowid
                JOIN papers d ON d.id=p.paper_id
                WHERE passages_fts MATCH ? ORDER BY score LIMIT ?''', (expression, limit))]

    def embedding_status(self, model):
        with self.connect() as db:
            total = db.execute('SELECT COUNT(*) FROM passages').fetchone()[0]
            indexed = db.execute('SELECT COUNT(*) FROM passage_embeddings WHERE model=?',
                                 (model,)).fetchone()[0]
        return {'model':model, 'total':total, 'indexed':indexed,
                'ready':total > 0 and total == indexed}

    def passages_to_embed(self, model, limit=16):
        with self.connect() as db:
            return [dict(row) for row in db.execute('''
                SELECT p.id, p.text FROM passages p
                LEFT JOIN passage_embeddings e ON e.passage_id=p.id AND e.model=?
                WHERE e.passage_id IS NULL ORDER BY p.id LIMIT ?''', (model, limit))]

    def embedding_dimension(self, model):
        with self.connect() as db:
            row = db.execute('SELECT vector FROM passage_embeddings WHERE model=? LIMIT 1',
                             (model,)).fetchone()
        return len(json.loads(row['vector'])) if row else None

    def save_embeddings(self, model, rows):
        with self.connect() as db:
            db.executemany('INSERT OR REPLACE INTO passage_embeddings VALUES (?,?,?)',
                           ((passage_id, model, json.dumps(vector))
                            for passage_id, vector in rows))

    def clear_embeddings(self, model):
        with self.connect() as db:
            db.execute('DELETE FROM passage_embeddings WHERE model=?', (model,))

    def search_embeddings(self, model, query_vector, limit=30):
        query_norm = math.sqrt(sum(value * value for value in query_vector))
        if not query_norm:
            return []
        with self.connect() as db:
            rows = db.execute('''
                SELECT p.id AS passage_id, p.paper_id, p.page, p.text AS snippet,
                       d.title, e.vector
                FROM passage_embeddings e JOIN passages p ON p.id=e.passage_id
                JOIN papers d ON d.id=p.paper_id WHERE e.model=?''', (model,))
            scores = []
            for row in rows:
                item = dict(row)
                vector = json.loads(item.pop('vector'))
                if len(vector) != len(query_vector):
                    continue
                norm = math.sqrt(sum(value * value for value in vector))
                if not norm:
                    continue
                item['score'] = sum(a * b for a, b in zip(vector, query_vector)) / (norm * query_norm)
                scores.append(item)
        return sorted(scores, key=lambda item: item['score'], reverse=True)[:limit]
    def rename_conversation(self,cid,title):
        with self.connect() as db:
            cur=db.execute('UPDATE conversations SET title=?, updated_at=? WHERE id=?',(title[:100],now(),cid))
            return cur.rowcount > 0

    def delete_conversation(self,cid):
        with self.connect() as db:
            cur=db.execute('DELETE FROM conversations WHERE id=?',(cid,))
            return cur.rowcount>0

    def notes(self):
        with self.connect() as db:
            return [dict(x) for x in db.execute('SELECT * FROM notes ORDER BY updated_at DESC')]

    def save_note(self,title,content,nid=None):
        timestamp=now()
        with self.connect() as db:
            if nid:
                cur=db.execute('UPDATE notes SET title=?,content=?,updated_at=? WHERE id=?',(title,content,timestamp,nid))
                if not cur.rowcount: return None
            else:
                nid=str(uuid.uuid4())
                db.execute('INSERT INTO notes VALUES (?,?,?,?,?)',(nid,title,content,timestamp,timestamp))
            row=db.execute('SELECT * FROM notes WHERE id=?',(nid,)).fetchone()
            return dict(row)

    def delete_note(self,nid):
        with self.connect() as db:
            return db.execute('DELETE FROM notes WHERE id=?',(nid,)).rowcount>0

    def tasks(self):
        with self.connect() as db:
            return [dict(x) for x in db.execute('SELECT * FROM tasks ORDER BY completed, created_at DESC')]

    def create_task(self,title):
        item={'id':str(uuid.uuid4()),'title':title,'completed':0,'created_at':now()}
        with self.connect() as db:
            db.execute('INSERT INTO tasks VALUES (:id,:title,:completed,:created_at)',item)
        return item

    def set_task(self,tid,completed):
        with self.connect() as db:
            return db.execute('UPDATE tasks SET completed=? WHERE id=?',(int(completed),tid)).rowcount>0

    def delete_task(self,tid):
        with self.connect() as db:
            return db.execute('DELETE FROM tasks WHERE id=?',(tid,)).rowcount>0


def _chunks(text, size=1100, overlap=150):
    text = ' '.join(text.split())
    if not text:
        return
    start = 0
    while start < len(text):
        end = min(len(text), start + size)
        if end < len(text):
            boundary = text.rfind(' ', start + size // 2, end)
            if boundary > start:
                end = boundary
        yield text[start:end]
        if end == len(text):
            break
        start = max(start + 1, end - overlap)
