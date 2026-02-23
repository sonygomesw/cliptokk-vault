#!/usr/bin/env python3
"""
Voyage AI Indexer pour Obsidian Vault
Indexe les fichiers .md et permet la recherche semantique
"""

import os
import json
import hashlib
from pathlib import Path
from typing import List, Dict, Optional
import voyageai
import chromadb
from chromadb.config import Settings

# Configuration
VAULT_PATH = os.environ.get("VAULT_PATH", "/home/node/vault")
VOYAGE_API_KEY = os.environ.get("VOYAGE_API_KEY", "pa--safToJz-Ll_qzLV-ug-zHQTJRfeSWJu2DBkRUYLAKX")
CHROMA_PATH = os.environ.get("CHROMA_PATH", "/home/node/.openclaw/chroma_db")
COLLECTION_NAME = "obsidian_vault"

# Init clients
voyage_client = voyageai.Client(api_key=VOYAGE_API_KEY)
chroma_client = chromadb.PersistentClient(path=CHROMA_PATH)

def get_or_create_collection():
    """Get or create the ChromaDB collection"""
    return chroma_client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"description": "Obsidian vault documents"}
    )

def read_markdown_files(vault_path: str) -> List[Dict]:
    """Read all markdown files from the vault"""
    documents = []
    vault = Path(vault_path)

    for md_file in vault.rglob("*.md"):
        # Skip templates and hidden files
        if md_file.name.startswith("_") or "/.git/" in str(md_file):
            continue

        try:
            content = md_file.read_text(encoding="utf-8")
            if len(content.strip()) < 10:  # Skip empty/tiny files
                continue

            # Create document ID from path
            rel_path = md_file.relative_to(vault)
            doc_id = hashlib.md5(str(rel_path).encode()).hexdigest()

            documents.append({
                "id": doc_id,
                "path": str(rel_path),
                "content": content,
                "title": md_file.stem,
                "folder": str(rel_path.parent) if rel_path.parent != Path(".") else "root"
            })
        except Exception as e:
            print(f"Error reading {md_file}: {e}")

    return documents

def chunk_text(text: str, max_chars: int = 2000) -> List[str]:
    """Split text into chunks for embedding"""
    if len(text) <= max_chars:
        return [text]

    chunks = []
    paragraphs = text.split("\n\n")
    current_chunk = ""

    for para in paragraphs:
        if len(current_chunk) + len(para) < max_chars:
            current_chunk += para + "\n\n"
        else:
            if current_chunk:
                chunks.append(current_chunk.strip())
            current_chunk = para + "\n\n"

    if current_chunk:
        chunks.append(current_chunk.strip())

    return chunks

def create_embeddings(texts: List[str]) -> List[List[float]]:
    """Create embeddings using Voyage AI"""
    import time

    if not texts:
        return []

    # Rate limit: 3 RPM without payment method, so batch small and wait
    batch_size = 20  # Small batches to stay under token limit
    all_embeddings = []

    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        print(f"  Processing batch {i//batch_size + 1}/{(len(texts)-1)//batch_size + 1}...")

        try:
            result = voyage_client.embed(
                texts=batch,
                model="voyage-3",
                input_type="document"
            )
            all_embeddings.extend(result.embeddings)
        except Exception as e:
            if "RateLimitError" in str(type(e).__name__) or "rate" in str(e).lower():
                print(f"  Rate limited, waiting 25s...")
                time.sleep(25)
                # Retry
                result = voyage_client.embed(
                    texts=batch,
                    model="voyage-3",
                    input_type="document"
                )
                all_embeddings.extend(result.embeddings)
            else:
                raise e

        # Wait between batches to respect rate limit (3 RPM = 20s between requests)
        if i + batch_size < len(texts):
            print(f"  Waiting 22s for rate limit...")
            time.sleep(22)

    return all_embeddings

def index_vault():
    """Index all documents in the vault"""
    print(f"Indexing vault: {VAULT_PATH}")

    # Read all markdown files
    documents = read_markdown_files(VAULT_PATH)
    print(f"Found {len(documents)} documents")

    if not documents:
        print("No documents found!")
        return

    # Get collection
    collection = get_or_create_collection()

    # Clear existing documents
    try:
        collection.delete(where={})
    except:
        pass

    # Process each document
    all_ids = []
    all_embeddings = []
    all_documents = []
    all_metadatas = []

    for doc in documents:
        chunks = chunk_text(doc["content"])

        for i, chunk in enumerate(chunks):
            chunk_id = f"{doc['id']}_{i}"
            all_ids.append(chunk_id)
            all_documents.append(chunk)
            all_metadatas.append({
                "path": doc["path"],
                "title": doc["title"],
                "folder": doc["folder"],
                "chunk_index": i
            })

    print(f"Creating embeddings for {len(all_documents)} chunks...")
    all_embeddings = create_embeddings(all_documents)

    # Add to collection
    print("Storing in ChromaDB...")
    collection.add(
        ids=all_ids,
        embeddings=all_embeddings,
        documents=all_documents,
        metadatas=all_metadatas
    )

    print(f"Indexed {len(all_ids)} chunks from {len(documents)} documents")
    return len(documents), len(all_ids)

def search(query: str, n_results: int = 5) -> List[Dict]:
    """Search the vault using semantic search"""
    collection = get_or_create_collection()

    # Create query embedding
    result = voyage_client.embed(
        texts=[query],
        model="voyage-3",
        input_type="query"
    )
    query_embedding = result.embeddings[0]

    # Search
    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=n_results,
        include=["documents", "metadatas", "distances"]
    )

    # Format results
    formatted = []
    for i, doc in enumerate(results["documents"][0]):
        formatted.append({
            "content": doc,
            "path": results["metadatas"][0][i]["path"],
            "title": results["metadatas"][0][i]["title"],
            "score": 1 - results["distances"][0][i]  # Convert distance to similarity
        })

    return formatted

# FastAPI server for search API
def create_app():
    from fastapi import FastAPI, HTTPException
    from pydantic import BaseModel

    app = FastAPI(title="Vault Search API")

    class SearchRequest(BaseModel):
        query: str
        n_results: int = 5

    class IndexResponse(BaseModel):
        documents: int
        chunks: int

    @app.post("/search")
    async def api_search(request: SearchRequest):
        try:
            results = search(request.query, request.n_results)
            return {"results": results}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/index")
    async def api_index():
        try:
            docs, chunks = index_vault()
            return {"documents": docs, "chunks": chunks}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    return app

if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        if sys.argv[1] == "index":
            index_vault()
        elif sys.argv[1] == "search":
            query = " ".join(sys.argv[2:]) if len(sys.argv) > 2 else "prospects"
            results = search(query)
            for r in results:
                print(f"\n--- {r['title']} ({r['path']}) [score: {r['score']:.3f}] ---")
                print(r['content'][:500] + "..." if len(r['content']) > 500 else r['content'])
        elif sys.argv[1] == "serve":
            import uvicorn
            app = create_app()
            uvicorn.run(app, host="0.0.0.0", port=8765)
    else:
        print("Usage:")
        print("  python voyage-indexer.py index   - Index the vault")
        print("  python voyage-indexer.py search <query>  - Search the vault")
        print("  python voyage-indexer.py serve   - Start API server")
