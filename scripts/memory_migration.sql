-- Phase 1 — semantic memory (RAG) on Supabase pgvector. 384-dim to match the LOCAL
-- fastembed model (BAAI/bge-small-en-v1.5) so no text/PII ever leaves for embeddings.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS kb_chunks (
  id          bigserial PRIMARY KEY,
  kind        text NOT NULL DEFAULT 'knowledge',   -- knowledge | briefing | decision | qa
  content     text NOT NULL,
  embedding   vector(384),
  meta        jsonb DEFAULT '{}'::jsonb,
  created_at  timestamptz DEFAULT now()
);

-- NOTE: deliberately NO ivfflat index. The KB is small (glossary + briefings + decisions),
-- and ivfflat with few rows probes near-empty lists and returns nothing. An exact cosine
-- scan is correct and fast at this scale. Add an ivfflat/hnsw index only past ~50k rows.

ALTER TABLE kb_chunks ENABLE ROW LEVEL SECURITY;  -- service-role only (like audit_log)

-- Cosine-similarity search. Pass the query embedding as a '[..]' text (cast to vector).
CREATE OR REPLACE FUNCTION match_kb(query_text text, k int DEFAULT 5, kinds text[] DEFAULT NULL)
RETURNS TABLE(id bigint, kind text, content text, meta jsonb, similarity float)
LANGUAGE sql STABLE AS $$
  SELECT id, kind, content, meta, 1 - (embedding <=> query_text::vector) AS similarity
  FROM kb_chunks
  WHERE embedding IS NOT NULL
    AND (kinds IS NULL OR kind = ANY(kinds))
  ORDER BY embedding <=> query_text::vector
  LIMIT k;
$$;
