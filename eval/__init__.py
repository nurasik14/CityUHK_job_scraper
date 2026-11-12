"""Retrieval evaluation for the job recommender: keyword vs vector vs hybrid search.

See eval/README.md. Everything here reads the committed files in eval/data/ and never
touches the app's private data, except export_corpus.py, which reads the local cache once
to produce the public corpus.
"""
