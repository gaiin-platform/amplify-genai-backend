/**
 * File Service with RAG Pipeline Routing
 *
 * This service replaces the existing fileService.ts and adds intelligent routing
 * between the legacy (synchronous) and new (async v2) RAG pipelines.
 *
 * Usage:
 *   import { uploadAndProcessDocument } from './fileServiceRouted';
 *   const result = await uploadAndProcessDocument(file, userEmail);
 */

import {
  routeDocumentWithOverride,
  RagPipeline,
  getPipelineEndpoints,
  type RoutingDecision
} from './ragRoutingService';

import { documentStatusService } from './documentStatusService';

/**
 * Upload result with routing metadata
 */
export interface UploadResult {
  success: boolean;
  pipeline: RagPipeline;
  routingDecision: RoutingDecision;

  // For async v2 pipeline
  statusId?: string;
  websocketUrl?: string;

  // For legacy pipeline
  s3Key?: string;
  documentId?: string;

  // Error handling
  error?: string;
}

/**
 * Get S3 presigned URL for upload
 */
async function getPresignedUploadUrl(
  fileName: string,
  contentType: string,
  userEmail: string
): Promise<{ url: string; key: string; bucket: string }> {
  const response = await fetch('/api/files/presigned-upload', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${getAuthToken()}`,
    },
    body: JSON.stringify({
      fileName,
      contentType,
      userEmail,
    }),
  });

  if (!response.ok) {
    throw new Error(`Failed to get presigned URL: ${response.statusText}`);
  }

  return await response.json();
}

/**
 * Upload file to S3 using presigned URL
 */
async function uploadToS3(
  file: File,
  presignedUrl: string,
  onProgress?: (progress: number) => void
): Promise<void> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();

    xhr.upload.addEventListener('progress', (event) => {
      if (event.lengthComputable && onProgress) {
        const progress = (event.loaded / event.total) * 100;
        onProgress(progress);
      }
    });

    xhr.addEventListener('load', () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve();
      } else {
        reject(new Error(`Upload failed with status ${xhr.status}`));
      }
    });

    xhr.addEventListener('error', () => {
      reject(new Error('Upload failed'));
    });

    xhr.open('PUT', presignedUrl);
    xhr.setRequestHeader('Content-Type', file.type);
    xhr.send(file);
  });
}

/**
 * Get auth token from storage
 */
function getAuthToken(): string {
  // Replace with your actual auth token retrieval logic
  return localStorage.getItem('auth_token') || '';
}

/**
 * Process document via LEGACY pipeline (S3 trigger)
 *
 * Flow:
 * 1. Upload to S3
 * 2. S3 event triggers process_document_for_rag Lambda
 * 3. Poll for completion (120s timeout)
 */
async function processDocumentLegacy(
  file: File,
  userEmail: string,
  onProgress?: (progress: number) => void
): Promise<UploadResult> {
  try {
    // Get presigned URL
    onProgress?.(5);
    const { url, key, bucket } = await getPresignedUploadUrl(
      file.name,
      file.type,
      userEmail
    );

    // Upload to S3
    onProgress?.(10);
    await uploadToS3(file, url, (uploadProgress) => {
      onProgress?.(10 + uploadProgress * 0.3); // 10-40% range
    });

    onProgress?.(40);

    // S3 upload triggers Lambda automatically
    // Poll for completion (legacy behavior)
    const documentId = await pollForCompletion(bucket, key, (pollProgress) => {
      onProgress?.(40 + pollProgress * 0.6); // 40-100% range
    });

    onProgress?.(100);

    return {
      success: true,
      pipeline: RagPipeline.LEGACY,
      routingDecision: {
        pipeline: RagPipeline.LEGACY,
        reason: 'Legacy pipeline used',
        confidence: 'high',
      },
      s3Key: key,
      documentId,
    };
  } catch (error) {
    console.error('[Legacy Pipeline] Error:', error);
    return {
      success: false,
      pipeline: RagPipeline.LEGACY,
      routingDecision: {
        pipeline: RagPipeline.LEGACY,
        reason: 'Legacy pipeline used',
        confidence: 'high',
      },
      error: error instanceof Error ? error.message : 'Unknown error',
    };
  }
}

/**
 * Process document via ASYNC V2 pipeline (API Gateway)
 *
 * Flow:
 * 1. Upload to S3
 * 2. Call async RAG API with bucket/key
 * 3. Get statusId
 * 4. Subscribe to WebSocket for real-time updates
 */
async function processDocumentAsyncV2(
  file: File,
  userEmail: string,
  onProgress?: (progress: number) => void
): Promise<UploadResult> {
  try {
    const endpoints = getPipelineEndpoints(RagPipeline.ASYNC_V2);

    if (!endpoints.processUrl) {
      throw new Error('Async RAG API URL not configured');
    }

    // Get presigned URL
    onProgress?.(5);
    const { url, key, bucket } = await getPresignedUploadUrl(
      file.name,
      file.type,
      userEmail
    );

    // Upload to S3
    onProgress?.(10);
    await uploadToS3(file, url, (uploadProgress) => {
      onProgress?.(10 + uploadProgress * 0.2); // 10-30% range
    });

    onProgress?.(30);

    // Call async RAG API
    const response = await fetch(endpoints.processUrl, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${getAuthToken()}`,
      },
      body: JSON.stringify({
        bucket,
        key,
        metadata: {
          fileName: file.name,
          contentType: file.type,
          size: file.size,
          userEmail,
        },
      }),
    });

    if (!response.ok) {
      throw new Error(`Async RAG API error: ${response.statusText}`);
    }

    const { statusId, message } = await response.json();

    onProgress?.(40);

    // Connect to WebSocket for real-time updates
    if (endpoints.websocketUrl) {
      await documentStatusService.connect(userEmail);

      // Subscribe to status updates
      documentStatusService.subscribe(statusId, (update) => {
        const progress = update.metadata?.progress || 40;
        onProgress?.(progress);

        if (update.status === 'completed') {
          onProgress?.(100);
        }
      });
    }

    return {
      success: true,
      pipeline: RagPipeline.ASYNC_V2,
      routingDecision: {
        pipeline: RagPipeline.ASYNC_V2,
        reason: 'Async v2 pipeline used',
        confidence: 'high',
      },
      statusId,
      websocketUrl: endpoints.websocketUrl,
      s3Key: key,
    };
  } catch (error) {
    console.error('[Async V2 Pipeline] Error:', error);
    return {
      success: false,
      pipeline: RagPipeline.ASYNC_V2,
      routingDecision: {
        pipeline: RagPipeline.ASYNC_V2,
        reason: 'Async v2 pipeline used',
        confidence: 'high',
      },
      error: error instanceof Error ? error.message : 'Unknown error',
    };
  }
}

/**
 * Poll for document completion (legacy behavior)
 * Used by legacy pipeline which doesn't have WebSocket
 */
async function pollForCompletion(
  bucket: string,
  key: string,
  onProgress?: (progress: number) => void
): Promise<string> {
  const maxAttempts = 60; // 120 seconds (2 minutes)
  const pollInterval = 2000; // 2 seconds

  for (let attempt = 0; attempt < maxAttempts; attempt++) {
    const progress = (attempt / maxAttempts) * 100;
    onProgress?.(progress);

    // Check document status
    const response = await fetch(`/api/files/status?bucket=${bucket}&key=${key}`, {
      headers: {
        'Authorization': `Bearer ${getAuthToken()}`,
      },
    });

    if (response.ok) {
      const { status, documentId } = await response.json();

      if (status === 'completed' && documentId) {
        return documentId;
      }

      if (status === 'failed') {
        throw new Error('Document processing failed');
      }
    }

    // Wait before next poll
    await new Promise(resolve => setTimeout(resolve, pollInterval));
  }

  throw new Error('Document processing timeout (120s)');
}

/**
 * Main function: Upload and process document with intelligent routing
 *
 * @param file - File to upload
 * @param userEmail - Current user's email
 * @param onProgress - Optional progress callback (0-100)
 * @returns Upload result with routing metadata
 */
export async function uploadAndProcessDocument(
  file: File,
  userEmail: string,
  onProgress?: (progress: number) => void
): Promise<UploadResult> {
  // Route the document
  const decision = routeDocumentWithOverride(file, userEmail);

  console.log('[File Service] Routing decision:', decision);

  // Process via appropriate pipeline
  if (decision.pipeline === RagPipeline.ASYNC_V2) {
    const result = await processDocumentAsyncV2(file, userEmail, onProgress);
    result.routingDecision = decision;
    return result;
  } else {
    const result = await processDocumentLegacy(file, userEmail, onProgress);
    result.routingDecision = decision;
    return result;
  }
}

/**
 * Batch upload multiple documents
 */
export async function uploadMultipleDocuments(
  files: File[],
  userEmail: string,
  onFileProgress?: (fileIndex: number, progress: number) => void,
  onOverallProgress?: (progress: number) => void
): Promise<UploadResult[]> {
  const results: UploadResult[] = [];

  for (let i = 0; i < files.length; i++) {
    const file = files[i];

    const result = await uploadAndProcessDocument(
      file,
      userEmail,
      (progress) => {
        onFileProgress?.(i, progress);
        const overallProgress = ((i + progress / 100) / files.length) * 100;
        onOverallProgress?.(overallProgress);
      }
    );

    results.push(result);
  }

  return results;
}

/**
 * Query documents (works with both pipelines)
 */
export async function queryDocuments(
  query: string,
  documentIds?: string[],
  options?: {
    topK?: number;
    searchMode?: 'hybrid' | 'vdr' | 'hybrid_vdr_text';
    denseWeight?: number;
    sparseWeight?: number;
    useRRF?: boolean;
  }
): Promise<any> {
  // Try async v2 query endpoint first
  const asyncEndpoints = getPipelineEndpoints(RagPipeline.ASYNC_V2);
  const legacyEndpoints = getPipelineEndpoints(RagPipeline.LEGACY);

  let queryUrl = asyncEndpoints.queryUrl || legacyEndpoints.queryUrl;

  if (!queryUrl) {
    throw new Error('No query endpoint configured');
  }

  const response = await fetch(queryUrl, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${getAuthToken()}`,
    },
    body: JSON.stringify({
      query,
      document_ids: documentIds,
      top_k: options?.topK || 10,
      search_mode: options?.searchMode || 'hybrid',
      dense_weight: options?.denseWeight || 0.7,
      sparse_weight: options?.sparseWeight || 0.3,
      use_rrf: options?.useRRF || false,
    }),
  });

  if (!response.ok) {
    throw new Error(`Query failed: ${response.statusText}`);
  }

  return await response.json();
}

/**
 * Get document processing status
 * Works for both legacy (polling) and async v2 (WebSocket)
 */
export async function getDocumentStatus(
  statusId: string,
  pipeline: RagPipeline
): Promise<any> {
  if (pipeline === RagPipeline.ASYNC_V2) {
    // Async v2 uses WebSocket - status is pushed
    // Return last known status from service
    return documentStatusService.getLastStatus(statusId);
  } else {
    // Legacy uses polling
    const [bucket, key] = statusId.split('#');
    const response = await fetch(`/api/files/status?bucket=${bucket}&key=${key}`, {
      headers: {
        'Authorization': `Bearer ${getAuthToken()}`,
      },
    });

    if (!response.ok) {
      throw new Error(`Status check failed: ${response.statusText}`);
    }

    return await response.json();
  }
}

/**
 * Cancel document processing (async v2 only)
 */
export async function cancelDocumentProcessing(statusId: string): Promise<boolean> {
  const endpoints = getPipelineEndpoints(RagPipeline.ASYNC_V2);

  if (!endpoints.processUrl) {
    console.warn('Cancel not supported for legacy pipeline');
    return false;
  }

  const response = await fetch(`${endpoints.processUrl}/cancel`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${getAuthToken()}`,
    },
    body: JSON.stringify({ statusId }),
  });

  return response.ok;
}

// Export types for external use
export type { RoutingDecision };
export { RagPipeline };
