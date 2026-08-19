import os
from langchain_community.document_loaders import PyPDFDirectoryLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_chroma import Chroma

# Set up paths
DATA_PATH = "data" # Put your PDFs here
CHROMA_PATH = "../chroma_db"

def build_vector_database():
    print("1. Loading PDFs from directory...")
    loader = PyPDFDirectoryLoader(DATA_PATH)
    documents = loader.load()
    print(f"Loaded {len(documents)} pages.")

    print("2. Chunking documents...")
    # We use small chunks (500 chars) with high overlap (150 chars) 
    # so code blocks or register tables don't get cut in half.
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=500,
        chunk_overlap=150,
        length_function=len,
    )
    chunks = text_splitter.split_documents(documents)
    print(f"Split into {len(chunks)} chunks.")

    print("3. Embedding and saving to ChromaDB...")
    # Convert text to vectors and save to disk
    # This will download a small, fast, free embedding model to your PC
    db = Chroma.from_documents(
            documents=chunks, 
            embedding=HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2"), 
            persist_directory=CHROMA_PATH
        )
    
    # Chroma automatically saves to disk here.
    print("Database built successfully!")

# Run this once to build the DB
if __name__ == "__main__":
    build_vector_database()