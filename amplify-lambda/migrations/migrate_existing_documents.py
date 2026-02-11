"""
Migration Script: Migrate Existing Documents to Hybrid Search

Reindexes existing documents that were indexed with QA generation
to use new Hybrid Search (Dense + BM25)

This script:
1. Identifies documents without BM25 index
2. Rebuilds BM25 index from existing chunks
3. Optionally reprocesses visual-heavy documents with VDR
4. Tracks migration progress
"""

import psycopg2
import json
import os
import sys
from typing import List, Dict, Tuple
import argparse
from datetime import datetime

from pycommon.logger import getLogger
from embedding.bm25_indexer import index_document_bm25

logger = getLogger("migration")


class DocumentMigration:
    """Handles migration of existing documents to Hybrid Search"""

    def __init__(self, dry_run: bool = False, batch_size: int = 100):
        self.dry_run = dry_run
        self.batch_size = batch_size
        self.conn = self.get_db_connection()
        self.stats = {
            'total_documents': 0,
            'documents_needing_migration': 0,
            'documents_migrated': 0,
            'documents_failed': 0,
            'documents_skipped': 0
        }

    def get_db_connection(self):
        """Get database connection"""
        return psycopg2.connect(
            host=os.environ.get("RAG_POSTGRES_DB_WRITE_ENDPOINT"),
            database=os.environ.get("RAG_POSTGRES_DB_NAME"),
            user=os.environ.get("RAG_POSTGRES_DB_USERNAME"),
            password=os.environ.get("RAG_POSTGRES_DB_SECRET")
        )

    def find_documents_needing_migration(self) -> List[Tuple[str, str, int]]:
        """
        Find documents that need BM25 indexing

        Returns:
            List of (document_id, pipeline_type, num_chunks)
        """
        cursor = self.conn.cursor()

        # Find documents without BM25 metadata
        cursor.execute("""
            SELECT
                d.id,
                d.pipeline_type,
                COUNT(c.id) as num_chunks
            FROM documents d
            LEFT JOIN chunks c ON c.document_id = d.id
            LEFT JOIN document_bm25_metadata m ON m.document_id = d.id
            WHERE m.document_id IS NULL
              AND d.pipeline_type IS NOT NULL
            GROUP BY d.id, d.pipeline_type
            HAVING COUNT(c.id) > 0
            ORDER BY d.created_at DESC
        """)

        documents = cursor.fetchall()
        cursor.close()

        logger.info(f"Found {len(documents)} documents needing migration")

        return documents

    def get_document_chunks(self, document_id: str) -> List[Dict]:
        """
        Get chunks for a document

        Args:
            document_id: Document UUID

        Returns:
            List of chunk dicts
        """
        cursor = self.conn.cursor()

        cursor.execute("""
            SELECT id, content, embedding, page_num, chunk_index, metadata
            FROM chunks
            WHERE document_id = %s
            ORDER BY chunk_index
        """, (document_id,))

        rows = cursor.fetchall()
        cursor.close()

        chunks = [
            {
                'id': row[0],
                'content': row[1],
                'embedding': row[2],
                'page_num': row[3],
                'chunk_index': row[4],
                'metadata': row[5]
            }
            for row in rows
        ]

        return chunks

    def migrate_document(self, document_id: str, pipeline_type: str) -> bool:
        """
        Migrate a single document to Hybrid Search

        Args:
            document_id: Document UUID
            pipeline_type: 'text_rag' or 'vdr'

        Returns:
            True if successful, False otherwise
        """
        try:
            logger.info(f"Migrating document {document_id} ({pipeline_type})")

            if self.dry_run:
                logger.info("  [DRY RUN] Would migrate this document")
                return True

            # Get existing chunks
            chunks = self.get_document_chunks(document_id)

            if not chunks:
                logger.warning(f"  No chunks found for {document_id}")
                return False

            logger.info(f"  Found {len(chunks)} chunks")

            # Build BM25 index
            logger.info("  Building BM25 index...")
            bm25_stats = index_document_bm25(document_id, chunks)

            logger.info(f"  ✓ BM25 index built: {bm25_stats}")

            return True

        except Exception as e:
            logger.error(f"  ✗ Migration failed for {document_id}: {str(e)}")
            return False

    def migrate_batch(self, documents: List[Tuple[str, str, int]]) -> Dict:
        """
        Migrate a batch of documents

        Args:
            documents: List of (document_id, pipeline_type, num_chunks)

        Returns:
            Dict with batch statistics
        """
        batch_stats = {
            'migrated': 0,
            'failed': 0,
            'skipped': 0
        }

        for document_id, pipeline_type, num_chunks in documents:
            # Skip very small documents
            if num_chunks < 3:
                logger.info(f"Skipping {document_id}: too few chunks ({num_chunks})")
                batch_stats['skipped'] += 1
                continue

            # Migrate document
            success = self.migrate_document(document_id, pipeline_type)

            if success:
                batch_stats['migrated'] += 1
            else:
                batch_stats['failed'] += 1

        return batch_stats

    def run_migration(self) -> Dict:
        """
        Run full migration

        Returns:
            Dict with migration statistics
        """
        logger.info("=" * 60)
        logger.info("Document Migration to Hybrid Search")
        logger.info("=" * 60)

        if self.dry_run:
            logger.info("DRY RUN MODE - No changes will be made")

        # Find documents needing migration
        documents = self.find_documents_needing_migration()

        self.stats['total_documents'] = len(documents)
        self.stats['documents_needing_migration'] = len(documents)

        if not documents:
            logger.info("No documents need migration")
            return self.stats

        # Process in batches
        total_batches = (len(documents) + self.batch_size - 1) // self.batch_size

        logger.info(f"Processing {len(documents)} documents in {total_batches} batches")
        logger.info("")

        for batch_num in range(total_batches):
            start_idx = batch_num * self.batch_size
            end_idx = min(start_idx + self.batch_size, len(documents))

            batch = documents[start_idx:end_idx]

            logger.info(f"Batch {batch_num + 1}/{total_batches}: {len(batch)} documents")

            batch_stats = self.migrate_batch(batch)

            self.stats['documents_migrated'] += batch_stats['migrated']
            self.stats['documents_failed'] += batch_stats['failed']
            self.stats['documents_skipped'] += batch_stats['skipped']

            logger.info(f"  Batch complete: {batch_stats['migrated']} migrated, "
                       f"{batch_stats['failed']} failed, {batch_stats['skipped']} skipped")
            logger.info("")

        # Print final statistics
        logger.info("=" * 60)
        logger.info("Migration Complete")
        logger.info("=" * 60)
        logger.info(f"Total documents: {self.stats['total_documents']}")
        logger.info(f"Needed migration: {self.stats['documents_needing_migration']}")
        logger.info(f"Successfully migrated: {self.stats['documents_migrated']}")
        logger.info(f"Failed: {self.stats['documents_failed']}")
        logger.info(f"Skipped: {self.stats['documents_skipped']}")
        logger.info("=" * 60)

        return self.stats

    def verify_migration(self) -> Dict:
        """
        Verify migration was successful

        Checks:
        1. All documents have BM25 metadata
        2. All chunks have BM25 index entries
        3. Term statistics populated

        Returns:
            Dict with verification results
        """
        cursor = self.conn.cursor()

        # Check documents without BM25 metadata
        cursor.execute("""
            SELECT COUNT(*)
            FROM documents d
            LEFT JOIN document_bm25_metadata m ON m.document_id = d.id
            WHERE d.pipeline_type IS NOT NULL
              AND m.document_id IS NULL
              AND EXISTS (SELECT 1 FROM chunks WHERE document_id = d.id)
        """)

        docs_without_bm25 = cursor.fetchone()[0]

        # Check chunks without BM25 entries
        cursor.execute("""
            SELECT COUNT(*)
            FROM chunks c
            LEFT JOIN chunk_bm25_index b ON b.chunk_id = c.id
            WHERE b.chunk_id IS NULL
        """)

        chunks_without_bm25 = cursor.fetchone()[0]

        # Check term statistics
        cursor.execute("SELECT COUNT(*) FROM bm25_term_stats")
        total_term_stats = cursor.fetchone()[0]

        cursor.close()

        results = {
            'documents_without_bm25': docs_without_bm25,
            'chunks_without_bm25': chunks_without_bm25,
            'total_term_stats': total_term_stats,
            'verified': docs_without_bm25 == 0 and chunks_without_bm25 == 0
        }

        logger.info("=" * 60)
        logger.info("Migration Verification")
        logger.info("=" * 60)
        logger.info(f"Documents without BM25: {docs_without_bm25}")
        logger.info(f"Chunks without BM25: {chunks_without_bm25}")
        logger.info(f"Total term statistics: {total_term_stats}")
        logger.info(f"Status: {'✓ VERIFIED' if results['verified'] else '✗ INCOMPLETE'}")
        logger.info("=" * 60)

        return results

    def close(self):
        """Close database connection"""
        if self.conn:
            self.conn.close()


def main():
    """Main migration script"""
    parser = argparse.ArgumentParser(
        description='Migrate existing documents to Hybrid Search'
    )

    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Run in dry-run mode (no changes made)'
    )

    parser.add_argument(
        '--batch-size',
        type=int,
        default=100,
        help='Number of documents to process per batch (default: 100)'
    )

    parser.add_argument(
        '--verify',
        action='store_true',
        help='Only verify migration, don\'t run it'
    )

    args = parser.parse_args()

    # Initialize migration
    migration = DocumentMigration(
        dry_run=args.dry_run,
        batch_size=args.batch_size
    )

    try:
        if args.verify:
            # Verify only
            results = migration.verify_migration()
            sys.exit(0 if results['verified'] else 1)
        else:
            # Run migration
            stats = migration.run_migration()

            # Verify after migration
            logger.info("")
            results = migration.verify_migration()

            # Exit with appropriate code
            if stats['documents_failed'] > 0 or not results['verified']:
                sys.exit(1)
            else:
                sys.exit(0)

    finally:
        migration.close()


if __name__ == '__main__':
    main()
