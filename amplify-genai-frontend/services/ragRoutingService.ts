/**
 * RAG Routing Service
 *
 * Determines whether to route a document to:
 * - Old system: process_document_for_rag (synchronous, S3 trigger)
 * - New system: async-v2-api-processor (async, API Gateway)
 *
 * Supports multiple routing strategies:
 * 1. Feature flag (global toggle)
 * 2. User-based (beta users, gradual rollout)
 * 3. Document-based (file size, type)
 * 4. A/B testing (percentage rollout)
 */

export enum RagPipeline {
  LEGACY = 'legacy',      // Old synchronous system
  ASYNC_V2 = 'async_v2'   // New async system
}

export interface RoutingConfig {
  // Feature flags
  enableAsyncRag: boolean;
  rolloutPercentage: number;  // 0-100

  // User targeting
  betaUsers: string[];
  forceLegacyUsers: string[];

  // Document rules
  asyncForLargeFiles: boolean;
  largeFileThresholdMB: number;
  asyncForPresentations: boolean;
  asyncForForms: boolean;

  // A/B testing
  enableABTesting: boolean;
  abTestSeed: string;
}

export interface RoutingDecision {
  pipeline: RagPipeline;
  reason: string;
  confidence: 'high' | 'medium' | 'low';
  metadata?: Record<string, any>;
}

/**
 * Default routing configuration
 * Override with environment variables or runtime config
 */
const defaultConfig: RoutingConfig = {
  enableAsyncRag: process.env.REACT_APP_USE_ASYNC_RAG === 'true',
  rolloutPercentage: parseInt(process.env.REACT_APP_ASYNC_RAG_ROLLOUT_PERCENTAGE || '0', 10),

  betaUsers: process.env.REACT_APP_ASYNC_RAG_BETA_USERS?.split(',') || [],
  forceLegacyUsers: process.env.REACT_APP_ASYNC_RAG_FORCE_LEGACY_USERS?.split(',') || [],

  asyncForLargeFiles: process.env.REACT_APP_ASYNC_RAG_LARGE_FILES !== 'false',
  largeFileThresholdMB: parseInt(process.env.REACT_APP_ASYNC_RAG_LARGE_FILE_THRESHOLD_MB || '5', 10),
  asyncForPresentations: process.env.REACT_APP_ASYNC_RAG_PRESENTATIONS !== 'false',
  asyncForForms: process.env.REACT_APP_ASYNC_RAG_FORMS !== 'false',

  enableABTesting: process.env.REACT_APP_ASYNC_RAG_AB_TESTING === 'true',
  abTestSeed: process.env.REACT_APP_ASYNC_RAG_AB_TEST_SEED || 'default-seed',
};

/**
 * Hash a string to a number (for consistent A/B testing)
 */
function hashString(str: string, seed: string = ''): number {
  const combined = str + seed;
  let hash = 0;

  for (let i = 0; i < combined.length; i++) {
    const char = combined.charCodeAt(i);
    hash = ((hash << 5) - hash) + char;
    hash = hash & hash; // Convert to 32-bit integer
  }

  return Math.abs(hash);
}

/**
 * Check if user is in beta program
 */
function isBetaUser(userEmail: string, config: RoutingConfig): boolean {
  return config.betaUsers.includes(userEmail);
}

/**
 * Check if user should use legacy system
 */
function isForceLegacyUser(userEmail: string, config: RoutingConfig): boolean {
  return config.forceLegacyUsers.includes(userEmail);
}

/**
 * A/B test assignment based on user email
 * Returns true if user is in the treatment group (async RAG)
 */
function isInTreatmentGroup(userEmail: string, config: RoutingConfig): boolean {
  if (!config.enableABTesting) {
    return false;
  }

  const hash = hashString(userEmail, config.abTestSeed);
  const bucket = hash % 100;

  return bucket < config.rolloutPercentage;
}

/**
 * Check if file is large (should use async RAG for better timeout handling)
 */
function isLargeFile(file: File, config: RoutingConfig): boolean {
  if (!config.asyncForLargeFiles) {
    return false;
  }

  const fileSizeMB = file.size / (1024 * 1024);
  return fileSizeMB > config.largeFileThresholdMB;
}

/**
 * Check if file is a presentation format
 * Presentations benefit from VDR pipeline (visual layout is important)
 */
function isPresentationFile(file: File, config: RoutingConfig): boolean {
  if (!config.asyncForPresentations) {
    return false;
  }

  const presentationExtensions = [
    '.ppt', '.pptx',      // PowerPoint
    '.key',               // Keynote
    '.odp',               // OpenOffice Impress
    '.pdf'                // PDF (may be exported presentations)
  ];

  const fileName = file.name.toLowerCase();
  return presentationExtensions.some(ext => fileName.endsWith(ext));
}

/**
 * Check if file is a form or invoice
 * Forms/invoices benefit from VDR pipeline (structure is important)
 */
function isFormOrInvoice(file: File, config: RoutingConfig): boolean {
  if (!config.asyncForForms) {
    return false;
  }

  const fileName = file.name.toLowerCase();
  const formKeywords = [
    'form', 'invoice', 'receipt', 'w2', 'w9', '1040',
    'tax', 'application', 'contract', 'agreement'
  ];

  return formKeywords.some(keyword => fileName.includes(keyword));
}

/**
 * Main routing decision function
 *
 * Decision priority (first match wins):
 * 1. Force legacy users → LEGACY
 * 2. Global disable → LEGACY
 * 3. Beta users → ASYNC_V2
 * 4. Large files → ASYNC_V2
 * 5. Presentations → ASYNC_V2
 * 6. Forms/invoices → ASYNC_V2
 * 7. A/B test assignment → ASYNC_V2 or LEGACY
 * 8. Default → LEGACY
 */
export function routeDocument(
  file: File,
  userEmail: string,
  config: RoutingConfig = defaultConfig
): RoutingDecision {
  // Priority 1: Force legacy users
  if (isForceLegacyUser(userEmail, config)) {
    return {
      pipeline: RagPipeline.LEGACY,
      reason: 'User is in force-legacy list',
      confidence: 'high',
      metadata: { userEmail }
    };
  }

  // Priority 2: Global disable
  if (!config.enableAsyncRag) {
    return {
      pipeline: RagPipeline.LEGACY,
      reason: 'Async RAG is globally disabled',
      confidence: 'high'
    };
  }

  // Priority 3: Beta users
  if (isBetaUser(userEmail, config)) {
    return {
      pipeline: RagPipeline.ASYNC_V2,
      reason: 'User is in beta program',
      confidence: 'high',
      metadata: { userEmail }
    };
  }

  // Priority 4: Large files
  if (isLargeFile(file, config)) {
    const fileSizeMB = (file.size / (1024 * 1024)).toFixed(2);
    return {
      pipeline: RagPipeline.ASYNC_V2,
      reason: `File is large (${fileSizeMB}MB > ${config.largeFileThresholdMB}MB threshold)`,
      confidence: 'high',
      metadata: { fileSizeMB, threshold: config.largeFileThresholdMB }
    };
  }

  // Priority 5: Presentations
  if (isPresentationFile(file, config)) {
    return {
      pipeline: RagPipeline.ASYNC_V2,
      reason: 'File is a presentation (VDR pipeline is optimal)',
      confidence: 'medium',
      metadata: { fileName: file.name }
    };
  }

  // Priority 6: Forms/invoices
  if (isFormOrInvoice(file, config)) {
    return {
      pipeline: RagPipeline.ASYNC_V2,
      reason: 'File appears to be a form or invoice (VDR pipeline is optimal)',
      confidence: 'medium',
      metadata: { fileName: file.name }
    };
  }

  // Priority 7: A/B test assignment
  if (isInTreatmentGroup(userEmail, config)) {
    return {
      pipeline: RagPipeline.ASYNC_V2,
      reason: `User assigned to treatment group (${config.rolloutPercentage}% rollout)`,
      confidence: 'low',
      metadata: {
        userEmail,
        rolloutPercentage: config.rolloutPercentage,
        abTestSeed: config.abTestSeed
      }
    };
  }

  // Default: Use legacy system
  return {
    pipeline: RagPipeline.LEGACY,
    reason: 'Default routing (no async criteria met)',
    confidence: 'low'
  };
}

/**
 * Get API endpoint URLs for each pipeline
 */
export function getPipelineEndpoints(pipeline: RagPipeline) {
  if (pipeline === RagPipeline.ASYNC_V2) {
    return {
      processUrl: process.env.REACT_APP_ASYNC_RAG_API_URL!,
      queryUrl: process.env.REACT_APP_ASYNC_RAG_QUERY_URL!,
      websocketUrl: process.env.REACT_APP_ASYNC_RAG_WS_URL!,
    };
  } else {
    // Legacy system uses S3 upload (no direct API)
    return {
      processUrl: null,  // S3 trigger, no API endpoint
      queryUrl: process.env.REACT_APP_LEGACY_RAG_QUERY_URL || '/api/query',
      websocketUrl: null,  // Legacy doesn't have WebSocket
    };
  }
}

/**
 * Log routing decision to analytics
 */
export function logRoutingDecision(
  decision: RoutingDecision,
  file: File,
  userEmail: string
) {
  // Log to console in development
  if (process.env.NODE_ENV === 'development') {
    console.log('[RAG Routing]', {
      pipeline: decision.pipeline,
      reason: decision.reason,
      confidence: decision.confidence,
      fileName: file.name,
      fileSize: file.size,
      userEmail,
      metadata: decision.metadata,
    });
  }

  // Send to analytics service (if configured)
  if (window.analytics) {
    window.analytics.track('RAG Pipeline Routed', {
      pipeline: decision.pipeline,
      reason: decision.reason,
      confidence: decision.confidence,
      fileName: file.name,
      fileSize: file.size,
      fileType: file.type,
      userEmail,
      timestamp: new Date().toISOString(),
      ...decision.metadata,
    });
  }
}

/**
 * Get routing statistics for a user
 * Useful for admin dashboards
 */
export interface RoutingStats {
  legacyCount: number;
  asyncV2Count: number;
  totalCount: number;
  asyncV2Percentage: number;
}

export function getRoutingStats(userEmail: string): RoutingStats {
  // In production, fetch from backend analytics
  // For now, return from localStorage
  const stats = JSON.parse(localStorage.getItem(`rag-routing-stats-${userEmail}`) || '{}');

  return {
    legacyCount: stats.legacyCount || 0,
    asyncV2Count: stats.asyncV2Count || 0,
    totalCount: (stats.legacyCount || 0) + (stats.asyncV2Count || 0),
    asyncV2Percentage: stats.totalCount > 0
      ? ((stats.asyncV2Count || 0) / stats.totalCount) * 100
      : 0,
  };
}

/**
 * Update routing statistics
 */
export function updateRoutingStats(userEmail: string, pipeline: RagPipeline) {
  const stats = getRoutingStats(userEmail);

  if (pipeline === RagPipeline.LEGACY) {
    stats.legacyCount += 1;
  } else {
    stats.asyncV2Count += 1;
  }

  stats.totalCount = stats.legacyCount + stats.asyncV2Count;
  stats.asyncV2Percentage = (stats.asyncV2Count / stats.totalCount) * 100;

  localStorage.setItem(`rag-routing-stats-${userEmail}`, JSON.stringify(stats));
}

/**
 * Admin function: Force a specific pipeline for testing
 */
export function forceRoutingOverride(pipeline: RagPipeline | null) {
  if (pipeline === null) {
    localStorage.removeItem('rag-routing-override');
  } else {
    localStorage.setItem('rag-routing-override', pipeline);
  }
}

/**
 * Get forced routing override (for admin testing)
 */
export function getRoutingOverride(): RagPipeline | null {
  const override = localStorage.getItem('rag-routing-override');
  return override as RagPipeline | null;
}

/**
 * Main export: Route with override support
 */
export function routeDocumentWithOverride(
  file: File,
  userEmail: string,
  config: RoutingConfig = defaultConfig
): RoutingDecision {
  // Check for admin override first
  const override = getRoutingOverride();
  if (override) {
    return {
      pipeline: override,
      reason: 'Admin override enabled',
      confidence: 'high',
      metadata: { override: true }
    };
  }

  // Normal routing
  const decision = routeDocument(file, userEmail, config);

  // Log decision
  logRoutingDecision(decision, file, userEmail);

  // Update stats
  updateRoutingStats(userEmail, decision.pipeline);

  return decision;
}

// Export for global access (debugging)
if (typeof window !== 'undefined') {
  (window as any).__ragRouting = {
    routeDocument,
    forceRoutingOverride,
    getRoutingOverride,
    getRoutingStats,
    RagPipeline,
  };
}
