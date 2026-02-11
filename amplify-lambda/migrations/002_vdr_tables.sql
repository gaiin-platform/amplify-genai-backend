-- Migration: Add VDR (Visual Document Retrieval) tables
-- Creates tables for storing page-level embeddings with late interaction vectors

-- Enable pgvector extension if not already enabled
CREATE EXTENSION IF NOT EXISTS vector;

-- Add pipeline_type to documents table
ALTER TABLE documents
ADD COLUMN IF NOT EXISTS pipeline_type VARCHAR(50) DEFAULT 'text_rag';

CREATE INDEX IF NOT EXISTS idx_documents_pipeline_type ON documents(pipeline_type);

-- Create document_vdr_pages table for VDR embeddings
-- Each page has multiple embedding vectors (late interaction representation)
-- Typical: 1,030 vectors per page, each 128-256 dimensions
CREATE TABLE IF NOT EXISTS document_vdr_pages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    page_num INTEGER NOT NULL,

    -- Multi-vector embeddings for late interaction
    -- Stored as array of vectors: vector[]
    -- Each page has ~1,030 patch embeddings
    embedding_vectors JSONB NOT NULL,  -- Temporary storage as JSONB

    -- Metadata
    num_vectors INTEGER NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),

    -- Ensure one entry per document-page
    UNIQUE(document_id, page_num)
);

-- Indexes for VDR queries
CREATE INDEX IF NOT EXISTS idx_vdr_pages_document_id ON document_vdr_pages(document_id);
CREATE INDEX IF NOT EXISTS idx_vdr_pages_page_num ON document_vdr_pages(document_id, page_num);

-- Create specialized VDR query function
-- Uses MaxSim (Maximum Similarity) for late interaction matching
CREATE OR REPLACE FUNCTION vdr_search_pages(
    query_embedding vector,
    max_results INTEGER DEFAULT 10,
    similarity_threshold FLOAT DEFAULT 0.5
)
RETURNS TABLE (
    document_id UUID,
    page_num INTEGER,
    similarity_score FLOAT,
    bucket VARCHAR,
    key VARCHAR
) AS $$
BEGIN
    -- This is a placeholder function
    -- Actual implementation requires ColBERT-style MaxSim computation
    -- TODO: Implement proper late interaction matching

    RETURN QUERY
    SELECT
        dp.document_id,
        dp.page_num,
        0.0::FLOAT AS similarity_score,  -- Placeholder
        d.bucket,
        d.key
    FROM document_vdr_pages dp
    JOIN documents d ON d.id = dp.document_id
    WHERE d.pipeline_type = 'vdr'
    LIMIT max_results;
END;
$$ LANGUAGE plpgsql;

-- Add updated_at trigger
CREATE OR REPLACE FUNCTION update_vdr_pages_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trigger_update_vdr_pages_timestamp
    BEFORE UPDATE ON document_vdr_pages
    FOR EACH ROW
    EXECUTE FUNCTION update_vdr_pages_timestamp();

-- Add comments
COMMENT ON TABLE document_vdr_pages IS 'VDR page embeddings using late interaction (multi-vector per page)';
COMMENT ON COLUMN document_vdr_pages.embedding_vectors IS 'Array of patch embeddings (~1,030 vectors per page)';
COMMENT ON COLUMN document_vdr_pages.num_vectors IS 'Number of embedding vectors for this page';

-- Grant permissions
GRANT SELECT, INSERT, UPDATE, DELETE ON document_vdr_pages TO rag_user;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO rag_user;
