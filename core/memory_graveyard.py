from dataclasses import dataclass
from datetime import datetime
import numpy as np
import sqlite3
from sentence_transformers import SentenceTransformer
from sklearn.metrics.pairwise import cosine_similarity  
import networkx as nx
from apscheduler.schedulers.background import BackgroundScheduler


# Memory class to represent each memory entry in the system
@dataclass
class Memory:
    id: int  # local ID, not Discord Snowflake
    content: str
    timestamp: datetime
    embedding: np.ndarray  # Vector from sentence-transformers
    tags: set[str]  # e.g., 'physics', 'shitpost'
    storage_strength: float = 1.0  # How well encoded
    retrieval_strength: float = 1.0  # How easy to access
    related_ids: list[int] = None  # For graph links


# Initialize the DB
def init_db(db_path='leepabot_memory.db'):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute('''
        CREATE TABLE IF NOT EXISTS memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content TEXT,
            timestamp DATETIME,
            embedding BLOB,  # Store as bytes
            tags TEXT,  # Comma-separated
            storage_strength REAL,
            retrieval_strength REAL
        )
    ''')
    cur.execute('''
        CREATE TABLE IF NOT EXISTS edges (
            from_id INTEGER,
            to_id INTEGER,
            weight REAL,
            PRIMARY KEY (from_id, to_id)
        )
    ''')
    conn.commit()
    return conn


# get embedding model
model = SentenceTransformer('all-MiniLM-L6-v2')  # Lightweight embedder

def get_embedding(text):
    return model.encode(text)


# Function: is it an ongoing conversation or a new topic?
def is_ongoing_convo(new_msg, recent_memories, time_threshold_hours=1, sim_threshold=0.7):
    new_emb = get_embedding(new_msg.content)
    now = datetime.now()
    
    for mem in sorted(recent_memories, key=lambda m: m.timestamp, reverse=True):
        time_decay = np.exp(-(now - mem.timestamp).total_seconds() / (3600 * time_threshold_hours))  # Exponential decay
        sim = cosine_similarity([new_emb], [mem.embedding])[0][0] * time_decay
        if sim > sim_threshold:
            return True, mem.id  # Part of this cluster, link to it
    return False, None  # New topic


# Function: store memory in DB
def ingest_memory(conn, new_msg):
    new_emb = get_embedding(new_msg.content)
    cur = conn.cursor()
    
    # Fetch recent/similar memories
    cur.execute('SELECT id, embedding FROM memories ORDER BY timestamp DESC LIMIT 50')
    candidates = cur.fetchall()
    
    for cand_id, cand_emb_bytes in candidates:
        cand_emb = np.frombuffer(cand_emb_bytes, dtype=np.float32)
        sim = cosine_similarity([new_emb], [cand_emb])[0][0]
        if sim > 0.85:  # Merge threshold
            # For simplicity, update existing or skip
            return  # Or merge contents if needed
    
    # Store new
    cur.execute('''
        INSERT INTO memories (content, timestamp, embedding, tags, storage_strength, retrieval_strength)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (new_msg.content, datetime.now(), new_emb.tobytes(), ','.join(new_msg.tags), 1.0, 1.0))
    new_id = cur.lastrowid
    conn.commit()
    
    # Link if ongoing
    is_ongoing, related_id = is_ongoing_convo(new_msg, [])  # Pass recent from DB
    if is_ongoing:
        add_edge(conn, related_id, new_id, weight=sim)


# Function: add edge in graph
def add_edge(conn, from_id, to_id, weight):
    cur = conn.cursor()
    cur.execute('INSERT OR REPLACE INTO edges (from_id, to_id, weight) VALUES (?, ?, ?)', (from_id, to_id, weight))
    conn.commit()

# Building the graph and spreading activation can be done periodically or on demand
def build_graph(conn):  # Load on demand
    G = nx.DiGraph()
    cur = conn.cursor()
    cur.execute('SELECT id FROM memories')
    for (mid,) in cur.fetchall():
        G.add_node(mid)
    cur.execute('SELECT from_id, to_id, weight FROM edges')
    for from_id, to_id, weight in cur.fetchall():
        G.add_edge(from_id, to_id, weight=weight)
    return G

def spread_activation(graph, seed_id, max_depth=3):
    activated = set()
    frontier = [seed_id]
    for _ in range(max_depth):
        next_frontier = []
        for node in frontier:
            for neighbor in graph.neighbors(node):
                if neighbor not in activated:
                    activated.add(neighbor)
                    next_frontier.append(neighbor)
        frontier = next_frontier
    return activated


# Decay strengths over time
def decay_strengths(conn):
    cur = conn.cursor()
    now = datetime.now()
    cur.execute('SELECT id, timestamp, retrieval_strength FROM memories')
    for mid, ts, rs in cur.fetchall():
        elapsed_days = (now - ts).days
        new_rs = rs * (0.9 ** elapsed_days)  # Simple exponential decay; use FSRS for fancy
        cur.execute('UPDATE memories SET retrieval_strength = ? WHERE id = ?', (new_rs, mid))
    conn.commit()

# get context for LLM
def get_context(conn, query_msg, top_k=5):
    query_emb = get_embedding(query_msg.content)
    # Vector search (simple loop for now; use FAISS for speed)
    cur = conn.cursor()
    cur.execute('SELECT id, embedding, retrieval_strength FROM memories ORDER BY timestamp DESC LIMIT 100')
    candidates = [(mid, np.frombuffer(emb, np.float32), rs) for mid, emb, rs in cur.fetchall()]
    scores = [cosine_similarity([query_emb], [emb])[0][0] * rs for _, emb, rs in candidates]
    top_ids = [candidates[i][0] for i in np.argsort(scores)[-top_k:]]
    
    # Spread for more
    graph = build_graph(conn)
    activated = spread_activation(graph, top_ids[0])  # From most relevant
    # Fetch contents from activated IDs
    context = []
    cur.execute('SELECT content FROM memories WHERE id IN ({})'.format(','.join('?' for _ in activated)), list(activated))
    for (content,) in cur.fetchall():
        context.append(content)
    return '\n'.join(context)  # Weave into prompt

# Initialize the scheduler for decay
def fsrs_review(conn):
    # Simplified FSRS: Boost low-strength
    cur = conn.cursor()
    cur.execute('SELECT id FROM memories WHERE retrieval_strength < 0.5')
    for (mid,) in cur.fetchall():
        cur.execute('UPDATE memories SET retrieval_strength = retrieval_strength * 1.5 WHERE id = ?', (mid,))
    conn.commit()

scheduler = BackgroundScheduler()
scheduler.add_job(lambda: fsrs_review(conn), 'interval', hours=6)
scheduler.start()

# Memory dreaming for long term features:
def memory_dreaming(conn):
    cur = conn.cursor()
    cur.execute('SELECT id FROM memories WHERE timestamp > DATETIME("now", "-1 day")')  # Recent
    recent_ids = [row[0] for row in cur.fetchall()]
    
    for i in range(len(recent_ids)):
        for j in range(i+1, len(recent_ids)):
            # Check sim or tags overlap
            # If high, add_edge(conn, recent_ids[i], recent_ids[j], 0.6)
            pass

scheduler.add_job(lambda: memory_dreaming(conn), 'interval', hours=12)



conn = init_db()
scheduler = BackgroundScheduler()
scheduler.add_job(lambda: decay_strengths(conn), 'interval', hours=1)  # More frequent for dynamic
scheduler.add_job(lambda: fsrs_review(conn), 'interval', hours=6)
scheduler.add_job(lambda: memory_dreaming(conn), 'interval', hours=12)
scheduler.start()

