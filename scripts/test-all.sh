#!/bin/sh
# Runs inside the api image (scripts/test-all.ps1 starts it): every api test, including the database tests,
# against the separate bharat_test database. Cloud API keys are blanked so no test can call Groq or Gemini.
pip install -q pytest==8.3.4 >/dev/null 2>&1
TEST_DATABASE_URL="$(python -c 'import os, urllib.parse as u; print("postgresql://%s:%s@db:5432/bharat_test" % (u.quote(os.environ["POSTGRES_USER"], safe=""), u.quote(os.environ["POSTGRES_PASSWORD"], safe="")))')"
export TEST_DATABASE_URL
export DATABASE_URL="$TEST_DATABASE_URL" OLLAMA_URL=http://ollama:11434 UPLOAD_DIR=/tmp/uploads GEMINI_API_KEY= GROQ_API_KEY=
cd /w && python -m pytest -q -p no:cacheprovider tests
