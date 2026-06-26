"""Generates notebooks/test_components.ipynb from cell definitions.

Run: python notebooks/build_notebook.py
"""

import json
from pathlib import Path

CELLS = []


def md(text):
    CELLS.append({"cell_type": "markdown", "metadata": {}, "source": text.splitlines(keepends=True)})


def code(text):
    CELLS.append(
        {
            "cell_type": "code",
            "execution_count": None,
            "metadata": {},
            "outputs": [],
            "source": text.strip("\n").splitlines(keepends=True),
        }
    )


md("# JobHuntAI — Component Tests\n"
   "\n"
   "Each section is runnable independently with just a populated `.env`.\n"
   "Run this from the project root so imports resolve.\n")

md("## 0. Setup — load env + fix sys.path")
code(
    "import os, sys\n"
    "sys.path.insert(0, os.path.abspath('..'))\n"
    "sys.path.insert(0, os.path.abspath('.'))\n"
    "from dotenv import load_dotenv\n"
    "load_dotenv()\n"
    "print('OPENAI key set:', bool(os.getenv('OPENAI_API_KEY')))\n"
)

md("## 1. DB Layer\n"
   "Create tables, insert a user/session/messages, query them back.")
code(
    "from db import sqlite\n"
    "sqlite.init_db()\n"
    "import uuid\n"
    "uname = 'nb_' + uuid.uuid4().hex[:6]\n"
    "user = sqlite.create_user(uname, uname + '@example.com', 'hashed-pw')\n"
    "session = sqlite.create_session(user['user_id'], title='Notebook test')\n"
    "sqlite.add_message(session['session_id'], 'user', 'Hello agent')\n"
    "sqlite.add_message(session['session_id'], 'assistant', 'Hi! How can I help?')\n"
    "print('User:', user['user_id'])\n"
    "print('Messages:', sqlite.get_messages(session['session_id']))\n"
)

md("## 2. Auth\n"
   "Hash a password, issue a JWT, decode it back.")
code(
    "from api import auth\n"
    "hashed = auth.hash_password('secret123')\n"
    "print('verify ok:', auth.verify_password('secret123', hashed))\n"
    "tok = auth.create_access_token('user-abc')\n"
    "print('token:', tok[:40], '...')\n"
    "print('decoded sub:', auth.decode_token(tok))\n"
)

md("## 3. ChromaDB\n"
   "Upsert 3 docs to `memory:test_user`, query top-2.")
code(
    "from db import chroma\n"
    "ns = chroma.memory_ns('test_user')\n"
    "chroma.delete_namespace(ns)\n"
    "chroma.upsert(ns, documents=[\n"
    "    'Senior backend engineer, 8 years Python.',\n"
    "    'Led monolith to microservices migration.',\n"
    "    'Prefers remote-first fintech companies.'],\n"
    "    metadatas=[{'k':'skill'},{'k':'achievement'},{'k':'pref'}])\n"
    "docs, metas, dists = chroma.query(ns, 'preferred company type', 2)\n"
    "for d, dist in zip(docs, dists): print(round(dist,3), d)\n"
)

md("## 4. Embeddings\n"
   "Load all-MiniLM-L6-v2 and embed a sentence.")
code(
    "from sentence_transformers import SentenceTransformer\n"
    "model = SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2')\n"
    "vec = model.encode('A backend engineer who loves distributed systems.')\n"
    "print('vector shape:', vec.shape)\n"
)

md("## 5. Resume Ingestion (structured extraction)\n"
   "Run GPT-4o extraction on a sample resume text string.")
code(
    "import asyncio\n"
    "from agent.llm import complete_json\n"
    "from agent.resume.ingestion import _EXTRACTION_SYSTEM\n"
    "SAMPLE = '''Jane Doe | jane@x.com | 555-1234 | NYC\n"
    "Summary: Backend engineer, 6 yrs.\n"
    "Experience: Senior Engineer, Acme (2020-2024) - Built payments API.\n"
    "Skills: Python, Go, Postgres, Kafka\n"
    "Education: BS Computer Science, MIT (2014-2018)'''\n"
    "profile = asyncio.run(complete_json(_EXTRACTION_SYSTEM, SAMPLE))\n"
    "import json; print(json.dumps(profile, indent=2)[:800])\n"
)

md("## 6. Resume Tailoring (gap analysis)\n"
   "Given a JD + resume JSON, show matched / missing.")
code(
    "from agent.resume import tailoring\n"
    "JD = 'Looking for a backend engineer with Python, Kafka, AWS, and gRPC. "
    "Kubernetes preferred.'\n"
    "sample_profile = {'contact':{'name':'Jane Doe'},'summary':'Backend eng',\n"
    "    'skills':['Python','Kafka','Postgres'],'experience':[],'education':[],\n"
    "    'certifications':[],'projects':[]}\n"
    "gaps = asyncio.run(tailoring.gap_analysis(JD, sample_profile))\n"
    "print('matched:', gaps['matched'])\n"
    "print('missing_required:', gaps['missing_required'])\n"
    "print('missing_preferred:', gaps['missing_preferred'])\n"
)

md("## 7. Job Search Tool\n"
   "Discover roles at Stripe in New York (DuckDuckGo). Network-dependent.")
code(
    "from agent.tools import company_job_search\n"
    "res = asyncio.run(company_job_search.run(\n"
    "    {'company':'Stripe','location':'New York','role':'backend'}, 'test_user'))\n"
    "for r in res['results'][:3]:\n"
    "    print(r['ats_platform'], round(r['score'],1), r['title'][:70])\n"
)

md("## 8. SSE Event format\n"
   "Show how each event type serialises.")
code(
    "import json\n"
    "EVENTS = [\n"
    "    {'type':'token','content':'Hello'},\n"
    "    {'type':'tool_start','tool':'company_job_search'},\n"
    "    {'type':'tool_end','tool':'company_job_search','result':{}},\n"
    "    {'type':'progress','step':'Planning'},\n"
    "    {'type':'hitl_request','action':'write_resume','details':{}},\n"
    "    {'type':'applied','company':'Stripe','role':'SWE'},\n"
    "    {'type':'resume_ready','path':'resumes/x.pdf'},\n"
    "    {'type':'onboarding_required','message':'Upload resume'},\n"
    "    {'type':'captcha_blocked'},\n"
    "    {'type':'login_required','url':'https://...'},\n"
    "    {'type':'done'}]\n"
    "for e in EVENTS: print('data: ' + json.dumps(e) + chr(10))\n"
)

md("## 9. LangSmith\n"
   "Verify env vars, create a run, log metadata, close it.")
code(
    "from observability import langsmith\n"
    "print('tracing enabled:', langsmith.tracing_enabled())\n"
    "rid = langsmith.create_run('nb_test', {'foo':'bar'}, metadata={'src':'notebook'})\n"
    "print('run id:', rid)\n"
    "langsmith.end_run(rid, outputs={'ok': True})\n"
    "langsmith.flush_trace()\n"
)

md("## 10. LangGraph Agent\n"
   "Minimal invocation with a 'hello' message (no tools).")
code(
    "from agent.graph import build_graph, initial_state\n"
    "graph = build_graph()\n"
    "state = initial_state('test_user','nb-session','Hi there!', memories=[])\n"
    "# Force no tools so we don't make tool calls in this smoke test.\n"
    "state['pending_goals'] = []\n"
    "out = asyncio.run(graph.ainvoke(state, config={'configurable':{'thread_id':'nb-session'}}))\n"
    "print('final_response:', out.get('final_response'))\n"
)

md("## 11. Full Pipeline smoke test\n"
   "POST /chat/stream against a locally running server (uvicorn api.main:app).\n"
   "Start the server first, then register + stream.")
code(
    "import httpx, json\n"
    "BASE = 'http://localhost:8000'\n"
    "uname = 'smoke_' + uuid.uuid4().hex[:6]\n"
    "try:\n"
    "    tok = httpx.post(f'{BASE}/auth/register', json={'username':uname,\n"
    "        'email':uname+'@x.com','password':'secret123'}, timeout=30).json()['access_token']\n"
    "    events = []\n"
    "    with httpx.stream('POST', f'{BASE}/chat/stream',\n"
    "        headers={'Authorization': f'Bearer {tok}'},\n"
    "        json={'message':'hello'}, timeout=120) as r:\n"
    "        for line in r.iter_lines():\n"
    "            if line.startswith('data:'):\n"
    "                events.append(json.loads(line[5:].strip()))\n"
    "    for e in events: print(e)\n"
    "except Exception as ex:\n"
    "    print('Is the server running? Error:', ex)\n"
)

nb = {
    "cells": CELLS,
    "metadata": {
        "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
        "language_info": {"name": "python", "version": "3.11"},
    },
    "nbformat": 4,
    "nbformat_minor": 5,
}

out = Path(__file__).parent / "test_components.ipynb"
out.write_text(json.dumps(nb, indent=1))
print(f"Wrote {out}")
