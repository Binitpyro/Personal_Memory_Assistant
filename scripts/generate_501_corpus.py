import os
import random
from pathlib import Path
import docx

# Technical phrases/paragraphs to build natural-looking corpus
TOPICS = [
    "The Personal Memory Assistant uses a hybrid RAG architecture to retrieve local files.",
    "FastAPI provides the backend API layer serving built React bundles directly to the frontend.",
    "SQLite stores metadata and persistent embeddings with FTS5 search capabilities.",
    "LanceDB client is configured with cosine metric and IVF_HNSW_SQ indexing type.",
    "Rust core text extraction provides fast-path scanning and O(1) memory scalability.",
    "Sentence splitting uses NLTK sentence segmenter or a regex fallback pattern.",
    "StreamChunker is designed to handle very large logs and files with limited RAM.",
    "The desktop shell is built on Tauri v2 with system browser authentication flow.",
    "GraphRAG builds a local knowledge graph of entities and code relationships.",
    "ONNX runtime executes the MiniLM embedding model on CPU with bounded memory config."
]

def generate_sentence():
    return random.choice(TOPICS) + " " + " ".join(random.choices([
        "This enhances retrieval precision.",
        "Local-first design ensures data privacy.",
        "Performance optimization reduces peak RSS.",
        "Vector search uses cosine similarity metric.",
        "File metadata includes path, size, and modified time.",
        "Chunking strategy depends on file type.",
        "Code files use AST parsing for better context.",
        "Markdown headers provide structural hierarchy.",
        "Caching results improves user experience.",
        "Incremental sync minimizes indexing time."
    ], k=3))

def generate_paragraph(num_sentences=4):
    return " ".join(generate_sentence() for _ in range(num_sentences))

def generate_text_content(num_paragraphs=3):
    return "\n\n".join(generate_paragraph() for _ in range(num_paragraphs))

def generate_code_py(index):
    return f"""# Dummy python file {index}
import os
import sys

class MemoryProcessor_{index}:
    def __init__(self, capacity: int = 100):
        self.capacity = capacity
        self.data = []

    def ingest_data(self, item: str) -> bool:
        \"\"\"Process and ingest item if under capacity.\"\"\"
        if len(self.data) < self.capacity:
            self.data.append(item)
            return True
        return False

def run_retrieval_job_{index}(query: str, limit: int = 5):
    processor = MemoryProcessor_{index}()
    result = processor.ingest_data(query)
    print(f"Retrieving for query: {{query}}, success: {{result}}")
    return [query] * limit
"""

def generate_code_js(index):
    return f"""// Dummy javascript file {index}

class VectorStore_{index} {{
    constructor(dimension = 384) {{
        this.dimension = dimension;
        this.index = new Map();
    }}

    addVector(id, vector) {{
        if (vector.length !== this.dimension) {{
            throw new Error("Dimension mismatch");
        }}
        this.index.set(id, vector);
        return true;
    }}

    search(queryVector, limit = 10) {{
        console.log("Searching vector index...");
        return Array.from(this.index.keys()).slice(0, limit);
    }}
}}

function executeQuery_{index}(query) {{
    const store = new VectorStore_{index}();
    store.addVector("doc_1", new Array(384).fill(0.1));
    return store.search(query);
}}
"""

def generate_code_rs(index):
    return f"""// Dummy rust file {index}
use std::collections::HashMap;

pub struct ChunkIndexer_{index} {{
    chunks: HashMap<String, Vec<f32>>,
    dimension: usize,
}}

impl ChunkIndexer_{index} {{
    pub fn new(dimension: usize) -> Self {{
        Self {{
            chunks: HashMap::new(),
            dimension,
        }}
    }}

    pub fn insert_chunk(&mut self, id: String, vector: Vec<f32>) -> Result<(), &'static str> {{
        if vector.len() != self.dimension {{
            return Err("Dimension mismatch");
        }}
        self.chunks.insert(id, vector);
        Ok(())
    }}
}}

pub fn main_{index}() {{
    let mut indexer = ChunkIndexer_{index}::new(384);
    let success = indexer.insert_chunk("chunk_1".to_string(), vec![0.1; 384]);
    println!("Insertion status: {{:?}}", success);
}}
"""

def create_minimal_pdf(filename, text):
    lines = text.split('\n')
    content_parts = ["BT", "/F1 12 Tf", "50 750 Td", "14 TL"]
    for line in lines:
        if not line.strip():
            continue
        words = line.split(' ')
        chunk = ""
        for word in words:
            if len(chunk) + len(word) + 1 > 80:
                escaped = chunk.replace('(', '\\(').replace(')', '\\)')
                content_parts.append(f"({escaped}) Tj T*")
                chunk = word
            else:
                chunk = chunk + " " + word if chunk else word
        if chunk:
            escaped = chunk.replace('(', '\\(').replace(')', '\\)')
            content_parts.append(f"({escaped}) Tj T*")
    content_parts.append("ET")
    content = "\n".join(content_parts)
    content_bytes = content.encode('utf-8', errors='replace')
    
    objects = []
    offsets = []
    header = b"%PDF-1.4\n"
    
    def add_object(obj_bytes):
        offsets.append(len(header) + sum(len(o) for o in objects))
        objects.append(obj_bytes)
        
    add_object(b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n")
    add_object(b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n")
    add_object(b"3 0 obj\n<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R /MediaBox [0 0 612 792] >>\nendobj\n")
    add_object(b"4 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n")
    
    stream_len = len(content_bytes)
    obj5_header = f"5 0 obj\n<< /Length {stream_len} >>\nstream\n".encode('latin1')
    obj5_footer = b"\nendstream\nendobj\n"
    add_object(obj5_header + content_bytes + obj5_footer)
    
    xref_pos = len(header) + sum(len(o) for o in objects)
    xref = f"xref\n0 {len(offsets) + 1}\n0000000000 65535 f \n".encode('latin1')
    for offset in offsets:
        xref += f"{offset:010d} 00000 n \n".encode('latin1')
        
    trailer = f"trailer\n<< /Size {len(offsets) + 1} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n".encode('latin1')
    
    with open(filename, 'wb') as f:
        f.write(header)
        for obj in objects:
            f.write(obj)
        f.write(xref)
        f.write(trailer)

def create_docx(filename, text):
    doc = docx.Document()
    for para in text.split('\n\n'):
        doc.add_paragraph(para)
    doc.save(filename)

def main():
    corpus_dir = Path("tests/fixtures/perf_corpus_501")
    corpus_dir.mkdir(parents=True, exist_ok=True)
    print(f"Generating performance corpus in {corpus_dir.absolute()}...")

    # 1. 500 code files: 200 py, 150 js, 150 rs
    for i in range(1, 21):
        with open(corpus_dir / f"code_{i}.py", "w", encoding="utf-8") as f:
            f.write(generate_code_py(i))
    for i in range(21, 36):
        with open(corpus_dir / f"code_{i}.js", "w", encoding="utf-8") as f:
            f.write(generate_code_js(i))
    for i in range(36, 51):
        with open(corpus_dir / f"code_{i}.rs", "w", encoding="utf-8") as f:
            f.write(generate_code_rs(i))
    print("Generated 500 code files.")

    # 2. 2000 text files
    for i in range(1, 201):
        with open(corpus_dir / f"text_{i}.txt", "w", encoding="utf-8") as f:
            f.write(generate_text_content(num_paragraphs=3))
    print("Generated 2000 text files.")

    # 3. 1000 markdown files
    for i in range(1, 101):
        with open(corpus_dir / f"doc_{i}.md", "w", encoding="utf-8") as f:
            content = f"# Documentation Part {i}\n\n"
            content += f"## Overview\n\n{generate_paragraph()}\n\n"
            content += "## Technical Details\n\n"
            content += f"- Item A: {generate_sentence()}\n"
            content += f"- Item B: {generate_sentence()}\n\n"
            content += "```python\n"
            content += f"def run_example_{i}():\n"
            content += "    # This is an example code block\n"
            content += "    print('Running example')\n"
            content += "```\n\n"
            content += f"### Summary\n\n{generate_paragraph()}"
            f.write(content)
    print("Generated 1000 markdown files.")

    # 4. 1000 PDF files
    for i in range(1, 101):
        text = generate_text_content(num_paragraphs=2)
        create_minimal_pdf(corpus_dir / f"report_{i}.pdf", text)
    print("Generated 1000 PDF files.")

    # 5. 500 DOCX files
    for i in range(1, 52):
        text = generate_text_content(num_paragraphs=2)
        create_docx(corpus_dir / f"spec_{i}.docx", text)
    print("Generated 500 DOCX files.")

    total_files = len(list(corpus_dir.glob("*")))
    print(f"Corpus generation complete. Generated {total_files} files in total.")

if __name__ == "__main__":
    main()
