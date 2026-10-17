"""Start VIRA on localhost only."""
import os
import uvicorn
from dotenv import load_dotenv

if __name__ == '__main__':
    load_dotenv()
    host=os.getenv('VIRA_HOST','127.0.0.1')
    if host not in ('127.0.0.1','localhost'):
        raise SystemExit('For safety VIRA only permits localhost. Edit run.py only after adding authentication.')
    uvicorn.run('vira.app:app',host=host,port=int(os.getenv('VIRA_PORT','8000')))
