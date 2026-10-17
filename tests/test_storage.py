import tempfile
import unittest
import sqlite3
from pathlib import Path
from vira.storage import Storage

class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.db=Storage(Path(self.temp.name)/'test.db')
    def tearDown(self):self.temp.cleanup()
    def test_conversation_and_cascade_delete(self):
        c=self.db.create_conversation()
        self.db.add_message(c['id'],'user','hello')
        self.db.add_message(c['id'],'assistant','hi')
        self.assertEqual([x['content'] for x in self.db.messages(c['id'])],['hello','hi'])
        self.assertTrue(self.db.delete_conversation(c['id']))
        self.assertEqual(self.db.messages(c['id']),[])
    def test_notes_crud(self):
        n=self.db.save_note('Test','first')
        self.assertEqual(self.db.save_note('Changed','second',n['id'])['content'],'second')
        self.assertEqual(len(self.db.notes()),1)
        self.assertTrue(self.db.delete_note(n['id']))
        self.assertEqual(self.db.notes(),[])
    def test_tasks(self):
        t=self.db.create_task('write tests')
        self.db.set_task(t['id'],True)
        self.assertEqual(self.db.tasks()[0]['completed'],1)
        self.db.delete_task(t['id'])
        self.assertEqual(self.db.tasks(),[])
    def test_missing_ids(self):
        self.assertIsNone(self.db.save_note('x','y','missing'))
        self.assertFalse(self.db.delete_conversation('missing'))
    def test_exchange_rolls_back_if_reply_cannot_be_saved(self):
        conversation=self.db.create_conversation()
        with self.db.connect() as db:
            db.execute("""CREATE TRIGGER reject_reply BEFORE INSERT ON messages
                          WHEN NEW.role='assistant'
                          BEGIN SELECT RAISE(ABORT, 'reply rejected'); END""")
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.add_exchange(conversation['id'],'question','answer')
        self.assertEqual(self.db.messages(conversation['id']),[])
        self.assertEqual(self.db.conversation(conversation['id'])['title'],'New conversation')

if __name__=='__main__':unittest.main()
