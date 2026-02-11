"""
End-to-End Test for Async RAG Pipeline
Tests complete flow: upload → async processing → VDR/Text RAG → completion
"""

import boto3
import json
import time
import os
from typing import Dict, List
import pytest

s3 = boto3.client('s3')
sqs = boto3.client('sqs')
dynamodb = boto3.resource('dynamodb')


class TestAsyncPipelineE2E:
    """End-to-end tests for async RAG pipeline"""

    def setup_method(self):
        """Setup test environment"""
        self.bucket = os.environ.get('TEST_S3_BUCKET', 'amplify-files-dev')
        self.status_table_name = os.environ.get('DOCUMENT_STATUS_TABLE', 'document-processing-status-dev')
        self.status_table = dynamodb.Table(self.status_table_name)

        self.test_files = {
            'small': 'tests/fixtures/test_10_pages.pdf',
            'medium': 'tests/fixtures/test_200_pages.pdf',
            'visual': 'tests/fixtures/test_presentation.pptx'
        }

    def upload_test_file(self, file_path: str, rag_enabled: bool = True) -> str:
        """
        Upload test file to S3 with RAG metadata

        Args:
            file_path: Path to test file
            rag_enabled: Enable RAG processing

        Returns:
            str: S3 object key
        """
        file_name = os.path.basename(file_path)
        key = f"test-user/{int(time.time())}_{file_name}"

        with open(file_path, 'rb') as f:
            s3.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=f,
                Metadata={
                    'rag_enabled': str(rag_enabled).lower(),
                    'test': 'true'
                }
            )

        print(f"✓ Uploaded: s3://{self.bucket}/{key}")
        return key

    def wait_for_status(
        self,
        key: str,
        expected_status: str,
        timeout: int = 600,
        poll_interval: int = 2
    ) -> Dict:
        """
        Wait for document to reach expected status

        Args:
            key: S3 object key
            expected_status: Expected status (e.g., 'completed', 'failed')
            timeout: Max wait time in seconds
            poll_interval: Polling interval in seconds

        Returns:
            Dict: Final status record

        Raises:
            TimeoutError: If timeout reached
        """
        status_id = f"{self.bucket}#{key}"
        start_time = time.time()

        print(f"Waiting for status '{expected_status}' (timeout: {timeout}s)...")

        while time.time() - start_time < timeout:
            response = self.status_table.get_item(Key={'statusId': status_id})

            if 'Item' in response:
                item = response['Item']
                current_status = item.get('status')
                progress = item.get('metadata', {}).get('progress', 0)

                print(f"  Status: {current_status} ({progress}%)")

                if current_status == expected_status:
                    elapsed = time.time() - start_time
                    print(f"✓ Reached status '{expected_status}' in {elapsed:.1f}s")
                    return item

                if current_status == 'failed':
                    error = item.get('metadata', {}).get('error', 'Unknown error')
                    raise Exception(f"Processing failed: {error}")

            time.sleep(poll_interval)

        raise TimeoutError(f"Timeout waiting for status '{expected_status}' after {timeout}s")

    def get_processing_stages(self, key: str) -> List[Dict]:
        """
        Get all processing stages from DynamoDB

        Args:
            key: S3 object key

        Returns:
            List[Dict]: All status updates
        """
        status_id = f"{self.bucket}#{key}"

        response = self.status_table.query(
            KeyConditionExpression='statusId = :sid',
            ExpressionAttributeValues={':sid': status_id},
            ScanIndexForward=True
        )

        return response.get('Items', [])

    def verify_document_indexed(self, document_id: str) -> Dict:
        """
        Verify document is properly indexed in database

        Args:
            document_id: Document UUID

        Returns:
            Dict: Verification results
        """
        import psycopg2

        conn = psycopg2.connect(
            host=os.environ.get("RAG_POSTGRES_DB_READ_ENDPOINT"),
            database=os.environ.get("RAG_POSTGRES_DB_NAME"),
            user=os.environ.get("RAG_POSTGRES_DB_USERNAME"),
            password=os.environ.get("RAG_POSTGRES_DB_SECRET")
        )

        cursor = conn.cursor()

        cursor.execute("SELECT pipeline_type FROM documents WHERE id = %s", (document_id,))
        result = cursor.fetchone()
        pipeline_type = result[0] if result else None

        cursor.execute("SELECT COUNT(*) FROM chunks WHERE document_id = %s", (document_id,))
        num_chunks = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM chunk_bm25_index WHERE chunk_id IN (SELECT id FROM chunks WHERE document_id = %s)", (document_id,))
        num_bm25_entries = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM document_vdr_pages WHERE document_id = %s", (document_id,))
        num_vdr_pages = cursor.fetchone()[0]

        cursor.close()
        conn.close()

        return {
            'pipeline_type': pipeline_type,
            'num_chunks': num_chunks,
            'num_bm25_entries': num_bm25_entries,
            'num_vdr_pages': num_vdr_pages
        }

    def test_small_document_text_rag(self):
        """Test small document (10 pages) → Text RAG pipeline"""
        print("\n=== Test: Small Document (Text RAG) ===")

        if not os.path.exists(self.test_files['small']):
            pytest.skip("Test file not found")

        key = self.upload_test_file(self.test_files['small'])

        final_status = self.wait_for_status(key, 'completed', timeout=120)

        assert final_status['status'] == 'completed'
        assert final_status['metadata'].get('pipeline') == 'text_rag'

        document_id = final_status['metadata'].get('document_id')
        assert document_id, "No document_id in final status"

        verification = self.verify_document_indexed(document_id)

        assert verification['pipeline_type'] == 'text_rag'
        assert verification['num_chunks'] > 0, "No chunks indexed"
        assert verification['num_bm25_entries'] > 0, "No BM25 entries"

        processing_time = final_status['metadata'].get('processing_time_seconds', 0)
        assert processing_time < 60, f"Processing too slow: {processing_time}s"

        print(f"✓ Test passed: {verification['num_chunks']} chunks in {processing_time}s")

    def test_visual_document_vdr(self):
        """Test visual-heavy document (presentation) → VDR pipeline"""
        print("\n=== Test: Visual Document (VDR) ===")

        if not os.path.exists(self.test_files['visual']):
            pytest.skip("Test file not found")

        key = self.upload_test_file(self.test_files['visual'])

        final_status = self.wait_for_status(key, 'completed', timeout=300)

        assert final_status['status'] == 'completed'
        assert final_status['metadata'].get('pipeline') == 'vdr'

        document_id = final_status['metadata'].get('document_id')
        assert document_id, "No document_id in final status"

        verification = self.verify_document_indexed(document_id)

        assert verification['pipeline_type'] == 'vdr'
        assert verification['num_vdr_pages'] > 0, "No VDR pages indexed"

        processing_time = final_status['metadata'].get('processing_time_seconds', 0)
        print(f"✓ Test passed: {verification['num_vdr_pages']} pages in {processing_time}s")

    def test_medium_document_no_timeout(self):
        """Test medium document (200 pages) completes without timeout"""
        print("\n=== Test: Medium Document (No Timeout) ===")

        if not os.path.exists(self.test_files['medium']):
            pytest.skip("Test file not found")

        key = self.upload_test_file(self.test_files['medium'])

        start_time = time.time()

        final_status = self.wait_for_status(key, 'completed', timeout=600)

        elapsed_time = time.time() - start_time

        assert final_status['status'] == 'completed'
        assert elapsed_time < 300, f"Processing took {elapsed_time}s (should be <300s)"

        document_id = final_status['metadata'].get('document_id')
        verification = self.verify_document_indexed(document_id)

        assert verification['num_chunks'] > 0 or verification['num_vdr_pages'] > 0

        print(f"✓ Test passed: completed in {elapsed_time:.1f}s (no timeout!)")

    def test_status_progression(self):
        """Test status progression through pipeline stages"""
        print("\n=== Test: Status Progression ===")

        if not os.path.exists(self.test_files['small']):
            pytest.skip("Test file not found")

        key = self.upload_test_file(self.test_files['small'])

        final_status = self.wait_for_status(key, 'completed', timeout=120)

        stages = self.get_processing_stages(key)

        expected_stages = ['validating', 'queued', 'processing_started']

        found_stages = [item['status'] for item in stages]

        for expected in expected_stages:
            assert expected in found_stages, f"Missing stage: {expected}"

        print(f"✓ Test passed: progression through {len(found_stages)} stages")
        print(f"  Stages: {' → '.join(found_stages)}")

    def test_error_handling(self):
        """Test error handling for invalid file"""
        print("\n=== Test: Error Handling ===")

        key = f"test-user/{int(time.time())}_invalid.xyz"

        s3.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=b"invalid content",
            Metadata={'rag_enabled': 'true', 'test': 'true'}
        )

        print(f"✓ Uploaded invalid file: {key}")

        try:
            final_status = self.wait_for_status(key, 'failed', timeout=60)
            assert final_status['status'] == 'failed'
            assert 'error' in final_status['metadata']
            print(f"✓ Test passed: error properly handled")
        except TimeoutError:
            pytest.fail("Expected 'failed' status but timed out")

    def test_parallel_uploads(self):
        """Test multiple documents processing in parallel"""
        print("\n=== Test: Parallel Processing ===")

        if not os.path.exists(self.test_files['small']):
            pytest.skip("Test file not found")

        keys = []
        for i in range(3):
            key = self.upload_test_file(self.test_files['small'])
            keys.append(key)

        start_time = time.time()

        for key in keys:
            self.wait_for_status(key, 'completed', timeout=180)

        elapsed_time = time.time() - start_time

        avg_time_per_doc = elapsed_time / len(keys)

        print(f"✓ Test passed: {len(keys)} documents in {elapsed_time:.1f}s")
        print(f"  Average: {avg_time_per_doc:.1f}s per document")

        assert avg_time_per_doc < 60, f"Parallel processing too slow: {avg_time_per_doc}s per doc"


def run_all_tests():
    """Run all end-to-end tests"""
    test = TestAsyncPipelineE2E()
    test.setup_method()

    tests = [
        ('Small Document (Text RAG)', test.test_small_document_text_rag),
        ('Visual Document (VDR)', test.test_visual_document_vdr),
        ('Medium Document (No Timeout)', test.test_medium_document_no_timeout),
        ('Status Progression', test.test_status_progression),
        ('Error Handling', test.test_error_handling),
        ('Parallel Processing', test.test_parallel_uploads)
    ]

    passed = 0
    failed = 0

    for name, test_func in tests:
        try:
            print(f"\nRunning: {name}...")
            test_func()
            passed += 1
            print(f"✓ PASSED: {name}")
        except Exception as e:
            failed += 1
            print(f"✗ FAILED: {name}")
            print(f"  Error: {str(e)}")

    print(f"\n{'='*60}")
    print(f"Test Results: {passed} passed, {failed} failed")
    print(f"{'='*60}")

    return failed == 0


if __name__ == '__main__':
    import sys
    success = run_all_tests()
    sys.exit(0 if success else 1)
