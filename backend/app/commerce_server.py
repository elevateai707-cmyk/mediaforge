"""Isolated sandbox commerce API using the same router as MediaForge main.
Keeps sandbox testing independent of an active editing/rendering session on :8420.
"""
from fastapi import FastAPI
from .commerce import router
app = FastAPI(title='MediaForge sandbox commerce')
app.include_router(router)
