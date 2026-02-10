# agent/extensions/vector_memory.py
import faiss
import numpy as np
import json
from sentence_transformers import SentenceTransformer

class BugMemory:
    def __init__(self, dim=384):
        self.model = SentenceTransformer("all-MiniLM-L6-v2")
        self.index = faiss.IndexFlatL2(dim)
        self.vectors = []
        self.entries = []

    def add_bug(self, text: str, metadata: dict):
        vec = self.model.encode([text])[0].astype("float32")
        self.index.add(np.array([vec]))
        self.vectors.append(vec)
        self.entries.append(metadata)

    def find_similar(self, text: str, top_k=3):
        query = self.model.encode([text])[0].astype("float32")
        D, I = self.index.search(np.array([query]), top_k)
        return [self.entries[i] for i in I[0] if i < len(self.entries)]

