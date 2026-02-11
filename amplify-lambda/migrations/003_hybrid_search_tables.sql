-- Migration: Add Hybrid Search (BM25 + Dense) tables
-- Replaces QA generation with Hybrid Search (10,000s → 180s speedup)

-- Table: chunk_bm25_index
-- Stores BM25 term frequencies for each chunk
CREATE TABLE IF NOT EXISTS chunk_bm25_index (
    chunk_id UUID PRIMARY KEY REFERENCES chunks(id) ON DELETE CASCADE,

    -- Term frequencies: {term: count}
    term_frequencies JSONB NOT NULL,

    -- Document length (number of tokens)
    doc_length INTEGER NOT NULL,

    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Index for fast chunk lookups
CREATE INDEX IF NOT EXISTS idx_chunk_bm25_chunk_id ON chunk_bm25_index(chunk_id);

-- GIN index for JSONB term frequencies (enables fast term lookups)
CREATE INDEX IF NOT EXISTS idx_chunk_bm25_term_freqs ON chunk_bm25_index USING GIN(term_frequencies);

-- Table: bm25_term_stats
-- Global term statistics for BM25 IDF calculation
CREATE TABLE IF NOT EXISTS bm25_term_stats (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,

    -- Term (lowercased, stemmed)
    term VARCHAR(255) NOT NULL,

    -- Number of documents containing this term (for IDF calculation)
    document_frequency INTEGER NOT NULL DEFAULT 1,

    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),

    -- One entry per (document_id, term) pair
    UNIQUE(document_id, term)
);

-- Indexes for fast term lookups
CREATE INDEX IF NOT EXISTS idx_bm25_term_stats_document_id ON bm25_term_stats(document_id);
CREATE INDEX IF NOT EXISTS idx_bm25_term_stats_term ON bm25_term_stats(term);
CREATE INDEX IF NOT EXISTS idx_bm25_term_stats_doc_term ON bm25_term_stats(document_id, term);

-- Table: document_bm25_metadata
-- Document-level BM25 metadata
CREATE TABLE IF NOT EXISTS document_bm25_metadata (
    document_id UUID PRIMARY KEY REFERENCES documents(id) ON DELETE CASCADE,

    -- Total number of chunks in document
    total_chunks INTEGER NOT NULL,

    -- Average chunk length (for BM25 normalization)
    avg_chunk_length FLOAT NOT NULL,

    -- Total unique terms across all chunks
    total_unique_terms INTEGER NOT NULL,

    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Index for fast document lookups
CREATE INDEX IF NOT EXISTS idx_document_bm25_metadata_doc_id ON document_bm25_metadata(document_id);

-- Table: hybrid_search_config
-- Configuration for hybrid search weights
CREATE TABLE IF NOT EXISTS hybrid_search_config (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Configuration name
    name VARCHAR(255) NOT NULL UNIQUE,

    -- Weight for dense (semantic) search (0-1)
    dense_weight FLOAT NOT NULL DEFAULT 0.7,

    -- Weight for sparse (lexical/BM25) search (0-1)
    sparse_weight FLOAT NOT NULL DEFAULT 0.3,

    -- BM25 k1 parameter (term frequency saturation)
    bm25_k1 FLOAT NOT NULL DEFAULT 1.5,

    -- BM25 b parameter (document length normalization)
    bm25_b FLOAT NOT NULL DEFAULT 0.75,

    -- Whether to use Reciprocal Rank Fusion
    use_rrf BOOLEAN NOT NULL DEFAULT false,

    -- RRF k constant
    rrf_k INTEGER DEFAULT 60,

    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),

    -- Ensure weights sum to 1.0
    CONSTRAINT valid_weights CHECK (ABS(dense_weight + sparse_weight - 1.0) < 0.001)
);

-- Insert default configuration
INSERT INTO hybrid_search_config (name, dense_weight, sparse_weight, bm25_k1, bm25_b, use_rrf, rrf_k)
VALUES ('default', 0.7, 0.3, 1.5, 0.75, false, 60)
ON CONFLICT (name) DO NOTHING;

-- Insert RRF configuration
INSERT INTO hybrid_search_config (name, dense_weight, sparse_weight, bm25_k1, bm25_b, use_rrf, rrf_k)
VALUES ('rrf', 0.5, 0.5, 1.5, 0.75, true, 60)
ON CONFLICT (name) DO NOTHING;

-- Add updated_at trigger for chunk_bm25_index
CREATE OR REPLACE FUNCTION update_chunk_bm25_index_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trigger_update_chunk_bm25_index_timestamp
    BEFORE UPDATE ON chunk_bm25_index
    FOR EACH ROW
    EXECUTE FUNCTION update_chunk_bm25_index_timestamp();

-- Add updated_at trigger for bm25_term_stats
CREATE OR REPLACE FUNCTION update_bm25_term_stats_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trigger_update_bm25_term_stats_timestamp
    BEFORE UPDATE ON bm25_term_stats
    FOR EACH ROW
    EXECUTE FUNCTION update_bm25_term_stats_timestamp();

-- Add updated_at trigger for document_bm25_metadata
CREATE OR REPLACE FUNCTION update_document_bm25_metadata_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trigger_update_document_bm25_metadata_timestamp
    BEFORE UPDATE ON document_bm25_metadata
    FOR EACH ROW
    EXECUTE FUNCTION update_document_bm25_metadata_timestamp();

-- Add comments
COMMENT ON TABLE chunk_bm25_index IS 'BM25 term frequencies for each chunk';
COMMENT ON TABLE bm25_term_stats IS 'Global term statistics for BM25 IDF calculation';
COMMENT ON TABLE document_bm25_metadata IS 'Document-level BM25 metadata';
COMMENT ON TABLE hybrid_search_config IS 'Configuration for hybrid search weights';

COMMENT ON COLUMN chunk_bm25_index.term_frequencies IS 'JSONB: {term: count} for all terms in chunk';
COMMENT ON COLUMN chunk_bm25_index.doc_length IS 'Number of tokens in chunk';
COMMENT ON COLUMN bm25_term_stats.document_frequency IS 'Number of chunks containing this term';
COMMENT ON COLUMN document_bm25_metadata.avg_chunk_length IS 'Average chunk length for BM25 normalization';

-- Grant permissions
GRANT SELECT, INSERT, UPDATE, DELETE ON chunk_bm25_index TO rag_user;
GRANT SELECT, INSERT, UPDATE, DELETE ON bm25_term_stats TO rag_user;
GRANT SELECT, INSERT, UPDATE, DELETE ON document_bm25_metadata TO rag_user;
GRANT SELECT ON hybrid_search_config TO rag_user;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO rag_user;

-- Performance note: JSONB GIN index enables fast term lookups
-- Example query: SELECT * FROM chunk_bm25_index WHERE term_frequencies ? 'machine';
