import chromadb
from chromadb.utils.embedding_functions import ONNXMiniLM_L6_V2
import shutil
import os

embedding_fn = ONNXMiniLM_L6_V2()

CHUNKS = [
    {
        "id": "about",
        "title": "About Hashtee",
        "content": (
            "Hashtee is an industrial AI company that builds custom AI systems for large enterprise clients. "
            "Founded with a mission to bring practical AI to the factory floor, Hashtee specializes in deploying "
            "AI where it matters most — on the production line, in warehouses, and across retail operations. "
            "Their solutions span Computer Vision, Document AI, Audio AI, Augmented Reality, and Retail Analytics. "
            "Hashtee's clients include Maruti Suzuki, Aditya Birla, Schneider Electric, ITT, Mubea, and Pantaloons. "
            "Hashtee works closely with clients to understand their specific operations and builds tailored AI "
            "models — not generic products. Every deployment is custom-fitted to the client's environment, cameras, "
            "data, and workflow."
        )
    },
    {
        "id": "services",
        "title": "Hashtee Services and Capabilities",
        "content": (
            "Hashtee offers five core AI service areas: "
            "1. Computer Vision: Camera-based AI that can detect defects, track objects, inspect products, "
            "and analyze scenes in real time. Used in manufacturing quality control and retail analytics. "
            "2. Document AI: Intelligent OCR that extracts structured data from unstructured documents like "
            "invoices, dispatch notes, garment tags, and number plates — far beyond simple text recognition. "
            "3. Audio AI: Machine sound monitoring using microphones to detect abnormal machine behavior "
            "before breakdowns occur. No sensors required. "
            "4. Augmented Reality: AR overlays for industrial use cases like guided assembly, remote expert "
            "support, and visual inspection guidance. "
            "5. Retail Analytics: In-store intelligence using existing CCTV to understand shopper behavior, "
            "path analysis, dwell time, queue lengths, and crowd density. "
            "All services are delivered as custom deployments — Hashtee handles data collection, model training, "
            "deployment, and ongoing monitoring."
        )
    },
    {
        "id": "tools_models",
        "title": "AI Tools, Models and Technology Stack",
        "content": (
            "Hashtee builds AI systems using a combination of deep learning frameworks and custom-trained models. "
            "For Computer Vision, they use YOLO-based object detection and custom CNN architectures trained on "
            "client-specific defect datasets. Models are optimized for edge deployment — running on NVIDIA Jetson "
            "and similar edge hardware for sub-millisecond inference. "
            "For Audio AI (Obi), they use audio fingerprinting and anomaly detection models trained on the specific "
            "machine sounds from the client's factory. No pre-trained general model — always custom. "
            "For Document AI (Longlichi), they use OCR combined with NLP extraction pipelines to convert "
            "unstructured documents into structured JSON output that feeds directly into client ERPs. "
            "For the AI chatbot (Pechi), they use large language models with tool-use capabilities, enabling "
            "the bot to query databases, trigger actions, and complete transactions — not just answer questions. "
            "Hashtee also uses object re-identification AI for multi-camera tracking (Binbin), which assigns "
            "a consistent identity to a person or item across different camera views without using face recognition."
        )
    },
    {
        "id": "obi",
        "title": "Obi - AI Sound Monitoring Product",
        "content": (
            "Obi is Hashtee's AI-powered sound monitoring product for industrial machines. "
            "If you want to monitor machines without sensors, predict breakdowns, or detect machine health issues, Obi is the solution. "
            "How it works: A microphone is placed near the machine. Obi continuously captures audio and runs it "
            "through an AI model that was trained specifically on the normal sounds of that machine. When the audio "
            "deviates from normal — bearing wear, impact events, abnormal vibrations, cavitation — Obi raises an alert. "
            "Key benefit: 24 to 72 hours early warning before breakdown occurs. This allows planned maintenance "
            "instead of emergency repairs. "
            "No sensors or wiring modifications needed — just a microphone. This makes installation fast and "
            "non-invasive. "
            "Use cases: Rotating machinery (motors, pumps, compressors), conveyor belts, HVAC systems, "
            "vehicle engine diagnostics, elevator maintenance. "
            "Industries: Manufacturing, automotive, energy, logistics. "
            "Deployed at Maruti Suzuki for engine noise detection on warranty claims — identifies the exact "
            "faulty component from the engine sound without disassembly."
        )
    },
    {
        "id": "dodo",
        "title": "Dodo - Machine Vision Quality Inspection Product",
        "content": (
            "Dodo is Hashtee's machine vision product for automated quality inspection on production lines. "
            "If you have quality issues, defects, or need to inspect products on your production line, Dodo is the solution. "
            "How it works: A camera (IP, USB, or GigE) is placed at the inspection point. Dodo's AI model "
            "analyzes each item in real time, generating bounding boxes and confidence scores for detected defects. "
            "Items are automatically classified as pass or fail. "
            "Speed: Sub-millisecond defect detection — under 50ms per part. Dodo works 24/7 without fatigue, "
            "unlike human inspectors. "
            "Works with any standard IP, USB, or GigE camera — no proprietary hardware needed. "
            "Defect types detected: Scratches, dents, surface damage, broken filaments, cross defects, "
            "oily stains, shade variation, dimensional deviations, label misalignment, fill level errors. "
            "Industries: Manufacturing, textile, automotive, pharma, food packaging, PCB inspection. "
            "Deployed at Aditya Birla Grasim (yarn cone inspection), ITT (precision steel valve inspection), "
            "and Mubea (automotive spring inspection — 12-13 defect types, zero defective parts guarantee)."
        )
    },
    {
        "id": "binbin",
        "title": "Binbin - WIP Tracking and Retail Intelligence Product",
        "content": (
            "Binbin is Hashtee's work-in-progress tracking product that uses existing CCTV cameras. "
            "How it works: Binbin connects to the client's existing camera network. Using object re-identification AI, "
            "it assigns a unique ID to each item or person and tracks them across all camera views — no RFID, "
            "no barcodes, no new hardware needed. "
            "In manufacturing: Tracks items (trays, boxes, pallets, parts) as they move through production stages. "
            "Gives a live dashboard of where everything is and how long each stage is taking. Identifies bottlenecks. "
            "In retail (retail intelligence mode): Assigns unique IDs to shoppers. Delivers path analysis "
            "(where shoppers go), dwell time (how long at each section), queue monitoring, and crowd density maps. "
            "Key advantage: Zero new hardware required. Uses cameras the client already has. No barcodes, "
            "no RFID tags, no modifications to products. "
            "Deployed at Pantaloons for multi-camera shopper tracking across retail store floors."
        )
    },
    {
        "id": "pechi",
        "title": "Pechi - AI Chatbot and Agentic Assistant Product",
        "content": (
            "Pechi is Hashtee's AI chatbot product designed for enterprise customer-facing and internal use. "
            "Pechi goes beyond a simple FAQ bot — it can take actions: query databases, trigger workflows, "
            "process transactions, and integrate with existing business systems. "
            "Language support: English, Hindi, and Tamil — making it suitable for Indian enterprise deployments "
            "where multiple languages are needed. "
            "Use cases: Customer service (handling queries, complaints, bookings), internal HR helpdesk, "
            "supplier communication, patient scheduling, student admissions processing, retail customer assistance. "
            "Pechi is fully custom — trained and configured for each client's specific domain, product catalog, "
            "and business rules. It is not a generic chatbot."
        )
    },
    {
        "id": "longlichi",
        "title": "Longlichi - OCR and Document AI Product",
        "content": (
            "Longlichi is Hashtee's OCR and document AI product that extracts structured data from documents. "
            "How it works: Documents (PDFs, images, photos) go in — Longlichi extracts specific fields and "
            "outputs structured JSON that can feed directly into ERPs, databases, or other systems. "
            "Speed: 500 documents per minute in batch mode. "
            "Handles: Dispatch notes, garment tags, number plates, invoices, vendor PDFs, prescription forms, "
            "customs declarations, handwritten and printed text. "
            "Beyond basic OCR: Longlichi also understands the meaning of what it reads. For example, it can "
            "compare extracted vendor scheme data, calculate final discounts, and flag inconsistencies — "
            "not just copy text. "
            "Deployed at Aditya Birla Opus Paints: reads vendor PDFs, extracts scheme data, compares schemes, "
            "calculates discounts. Reduced a 20-30 day manual process to minutes."
        )
    },
    {
        "id": "industries",
        "title": "Industries Hashtee Serves",
        "content": (
            "Hashtee serves multiple industries with tailored AI solutions: "
            "Manufacturing: Obi (sound monitoring) + Dodo (quality inspection) + Binbin (WIP tracking). "
            "Clients: Maruti Suzuki, ITT, Mubea, Schneider Electric. "
            "Textile: Dodo (yarn/fabric defect detection) + Binbin (production flow tracking) + Longlichi (tag/label reading). "
            "Clients: Aditya Birla Grasim. "
            "Retail: Binbin (shopper tracking, path analysis) + Longlichi (tag reading, invoice processing) + Pechi (customer chatbot). "
            "Clients: Pantaloons, Aditya Birla Opus Paints. "
            "Paints/Chemicals: Longlichi (vendor document processing) + Pechi (customer queries). "
            "Clients: Aditya Birla Opus Paints. "
            "Automotive: Obi (engine noise detection) + Dodo (component inspection) + custom AI systems. "
            "Clients: Maruti Suzuki, Mubea."
        )
    },
    {
        "id": "projects",
        "title": "Hashtee Client Projects and Case Studies",
        "content": (
            "Maruti Suzuki projects: "
            "1. Vehicle Dispatch Validation AI — captures OBD data at factory dispatch and dealer delivery, "
            "compares records in real time, raises alert on mismatch. "
            "2. AI Service Monitoring — CCTV and helmet cameras detect whether each PMS (periodic maintenance) "
            "task was performed or skipped at service centers. "
            "3. Engine Noise Detection — acoustic AI for warranty claims. Captures engine sound and identifies "
            "the exact faulty component without opening the engine. "
            "Aditya Birla projects: "
            "1. Yarn Cone Quality Inspection (Grasim) — CV system inspects synthetic yarn cones for broken "
            "filaments, cross defects, oily stains, shade variation. "
            "2. Vendor Scheme Comparison (Opus Paints) — AI reads vendor PDFs, extracts and compares scheme "
            "data, calculates discounts. Reduced 20-30 day process to minutes. "
            "Pantaloons: Multi-camera shopper tracking — unique IDs across cameras, path analysis, dwell time, "
            "queue monitoring, crowd density. "
            "ITT: Visual inspection of precision steel valves — detects scratches, dents, surface damage. "
            "Mubea: 360-degree spring inspection — 12-13 defect types. Zero defective springs reach Maruti Suzuki."
        )
    },
    {
        "id": "contact_demo",
        "title": "Contact Hashtee and Book a Demo",
        "content": (
            "To get in touch with Hashtee or book a demo, visit hashteelab.com/contact. "
            "Hashtee offers live demos of all their products — Obi, Dodo, Binbin, Pechi, and Longlichi. "
            "The demo typically takes 15-30 minutes and is tailored to your industry and specific use case. "
            "For pricing information, Hashtee works on a project basis — pricing depends on the scale of "
            "deployment, number of cameras/machines, and customization required. "
            "Best to discuss pricing directly with the Hashtee team at hashteelab.com/contact."
        )
    },
]


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
        print(f"[RAG] Chunk count changed ({existing} → {len(CHUNKS)}), re-indexing...")
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


def retrieve(query: str, top_k: int = 1) -> str:
    results = collection.query(
        query_texts=[query],
        n_results=min(top_k, collection.count())
    )
    chunks = results["documents"][0]
    titles = [m["title"] for m in results["metadatas"][0]]
    output = []
    for title, content in zip(titles, chunks):
        sentences = content.replace("How it works:", "").replace("Key benefit:", "").split(". ")
        short = ". ".join(s.strip() for s in sentences[:3] if s.strip()) + "."
        output.append(short)
    return "\n\n".join(output)


def retrieve_with_score(query: str) -> tuple[str, float]:
    """Returns (context, distance) — lower distance = more relevant."""
    results = collection.query(
        query_texts=[query],
        n_results=1,
        include=["documents", "distances", "metadatas"]
    )
    content = results["documents"][0][0]
    distance = results["distances"][0][0]
    sentences = content.replace("How it works:", "").replace("Key benefit:", "").split(". ")
    short = ". ".join(s.strip() for s in sentences[:3] if s.strip()) + "."
    return short, distance


index_documents()
