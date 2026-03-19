import os
import chromadb
from chromadb.utils.embedding_functions import ONNXMiniLM_L6_V2

embedding_fn = ONNXMiniLM_L6_V2()

KNOWLEDGE_DIR = os.path.join(os.path.dirname(__file__), "knowledge")


def load_chunks_from_folder(folder: str) -> list[dict]:
    """Load knowledge chunks from .txt files in the knowledge/ folder.

    Each file can contain multiple chunks separated by '---'.
    First line of each chunk must be: title: <Title Text>
    Rest is the content.
    """
    chunks = []
    for filename in sorted(os.listdir(folder)):
        if not filename.endswith(".txt"):
            continue
        filepath = os.path.join(folder, filename)
        with open(filepath, "r", encoding="utf-8") as f:
            raw = f.read()

        # Split file into sections by '---'
        sections = [s.strip() for s in raw.split("---") if s.strip()]
        file_id = filename.replace(".txt", "")

        for i, section in enumerate(sections):
            lines = section.splitlines()
            # First line must be "title: ..."
            title_line = lines[0].strip()
            if title_line.lower().startswith("title:"):
                title = title_line[6:].strip()
            else:
                title = file_id
            content = " ".join(line.strip() for line in lines[1:] if line.strip())
            chunk_id = file_id if len(sections) == 1 else f"{file_id}_{i}"
            chunks.append({
                "id": chunk_id,
                "title": title,
                "content": content
            })

    print(f"[RAG] Loaded {len(chunks)} chunks from {folder}")
    return chunks


CHUNKS = load_chunks_from_folder(KNOWLEDGE_DIR)


def _build_client():
    # Delete old store if chunk count changed (forces re-index with fresh data)
    store_path = "./chroma_store"
    client = chromadb.PersistentClient(path=store_path)
    col = client.get_or_create_collection(
        name="hashtee_knowledge",
        embedding_function=embedding_fn
    )
    existing = col.count()
    if existing > 0 and existing != len(CHUNKS):
        print(f"[RAG] Chunk count changed ({existing} -> {len(CHUNKS)}), re-indexing...")
        client.delete_collection("hashtee_knowledge")
        col = client.get_or_create_collection(
            name="hashtee_knowledge",
            embedding_function=embedding_fn
        )
    return client, col


client, collection = _build_client()


def index_documents():
    if collection.count() == len(CHUNKS):
        print(f"[RAG] Already indexed {collection.count()} chunks in ChromaDB")
        return
    print("[RAG] Indexing documents and generating embeddings...")
    for chunk in CHUNKS:
        text = f"{chunk['title']}. {chunk['content']}"
        collection.add(
            ids=[chunk["id"]],
            documents=[text],
            metadatas=[{"title": chunk["title"]}]
        )
    print(f"[RAG] Indexed {len(CHUNKS)} chunks into ChromaDB successfully!")


# ── Semantic confidential detector ───────────────────────────────────────────
_CONFIDENTIAL_EXAMPLES = [
    "employee salary compensation pay earnings income wages",
    "company revenue profit turnover annual income financial results",
    "number of employees headcount staff team size workforce",
    "funding investment valuation net worth capital raised investors",
    "founder owner ceo cto leadership management executive team",
    "internal financial data confidential business information",
    "how much money company makes earns generates profit",
    "company financial performance earnings revenue profit loss",
    "how many people work staff employed workforce size",
    "who founded started created built company ownership",
]

_conf_client = chromadb.Client()
_conf_col = _conf_client.get_or_create_collection(
    name="confidential_examples",
    embedding_function=embedding_fn
)
_conf_col.add(
    ids=[str(i) for i in range(len(_CONFIDENTIAL_EXAMPLES))],
    documents=_CONFIDENTIAL_EXAMPLES
)


def is_confidential_semantic(query: str, threshold: float = 0.65) -> bool:
    """Returns True if the query is semantically similar to a known confidential question."""
    results = _conf_col.query(
        query_texts=[query],
        n_results=1,
        include=["distances"]
    )
    distance = results["distances"][0][0]
    return distance <= threshold


def retrieve_top_k(query: str, top_k: int = 3) -> list[dict]:
    """Returns list of {text, distance} sorted by relevance."""
    if collection.count() == 0:
        return []
    results = collection.query(
        query_texts=[query],
        n_results=min(top_k, collection.count()),
        include=["documents", "distances", "metadatas"]
    )
    output = []
    for doc, dist in zip(results["documents"][0], results["distances"][0]):
        output.append({"text": doc, "distance": dist})
    return output


def retrieve_with_score(query: str) -> tuple[str, float]:
    """Returns (context, distance) — lower distance = more relevant."""
    results = collection.query(
        query_texts=[query],
        n_results=1,
        include=["documents", "distances", "metadatas"]
    )
    content = results["documents"][0][0]
    distance = results["distances"][0][0]
    return content, distance


index_documents()
