/** Correlate private solver requests with allowlisted final frontend evidence. */
const ERROR_CODES = new Set([
  'INVALID_ARGUMENT', 'RESOURCE_CHANGED', 'PERMISSION_DENIED',
  'EXECUTION_FAILED', 'PERSISTENCE_OUTCOME_UNKNOWN'
])
const ISSUE_CODES = new Set([
  'CHILD_SET_MISMATCH', 'CONTAINER_CHANGE_REJECTED', 'CONTAINER_NOT_FOUND',
  'CONTENT_SIZE_FALLBACK', 'CONTENT_SIZING_LIMIT', 'DUPLICATE_WIDGET',
  'GROUPING_COMMITTED_CONFLICT', 'GROUPING_CONFIRMATION_REQUIRED', 'GROUPING_CONTENT_UNAVAILABLE',
  'GROUPING_CONTEXT_CHANGED', 'GROUPING_INVALID_PROPOSAL', 'GROUPING_NOT_AVAILABLE',
  'GROUPING_NOT_WRITABLE', 'GROUPING_PENDING_WRITES', 'GROUPING_PENDING_WRITES_FAILED',
  'GROUPING_PENDING_WRITES_TIMEOUT', 'GROUPING_PREFLIGHT_FAILED', 'GROUPING_RELOAD_REQUIRED',
  'GROUPING_PREVIEW_REQUIRED', 'GROUPING_SAVE_REJECTED', 'GROUPING_SAVE_UNCONFIRMED',
  'GROUPING_UNSAVED_CHANGES', 'LAYOUT_BELOW_MINIMUM', 'LAYOUT_BOUNDARY_ALIGNMENT',
  'LAYOUT_CANCELLED', 'LAYOUT_CANVAS_CHANGED', 'LAYOUT_CANVAS_UNAVAILABLE',
  'LAYOUT_CHILD_OVERFLOW', 'LAYOUT_COMPARISON_STACKED', 'LAYOUT_CONFLICT',
  'LAYOUT_CONTAINER_CYCLE', 'LAYOUT_CONTAINER_UNSUPPORTED', 'LAYOUT_CONTAINER_WHITESPACE',
  'LAYOUT_CONTENT_CHANGED',
  'LAYOUT_CONTENT_NOT_READY_BEFORE_DEADLINE', 'LAYOUT_CONTENT_OVERFLOW',
  'LAYOUT_CONTENT_UNAVAILABLE', 'LAYOUT_EMPTY_CANVAS', 'LAYOUT_EMPTY_CHANGE',
  'LAYOUT_EXTERNAL_EMPTY_RATIO', 'LAYOUT_FRAME_ALIGNMENT', 'LAYOUT_HIDDEN_CONTENT_UNAVAILABLE',
  'LAYOUT_INCOMPLETE', 'LAYOUT_INPUT_UNAVAILABLE', 'LAYOUT_INVALID_BOUNDS',
  'LAYOUT_INVALID_IDENTITY', 'LAYOUT_INVALID_OVERRIDE', 'LAYOUT_INVALID_SHAPE',
  'LAYOUT_LARGE_EMPTY_REGION', 'LAYOUT_LEADERBOARD_SHAPE', 'LAYOUT_LINEAR_FALLBACK',
  'LAYOUT_MEASUREMENT_LIMIT', 'LAYOUT_MEASUREMENT_TIMEOUT', 'LAYOUT_NATIVE_PROJECTION_CHANGED',
  'LAYOUT_NESTED_CONTAINER_UNSUPPORTED', 'LAYOUT_NO_READABLE_CANDIDATES',
  'LAYOUT_ORDER_INVALID', 'LAYOUT_ORDER_MISMATCH', 'LAYOUT_OUT_OF_BOUNDS',
  'LAYOUT_OVERLAP', 'LAYOUT_POSITIONS_RETAINED', 'LAYOUT_REGIONAL_EMPTY_RATIO',
  'LAYOUT_REMAINING_SPACE', 'LAYOUT_RULE_INCOMPLETE',
  'LAYOUT_GROUPING_ONLY',
  'LAYOUT_SCOPE_UNSUPPORTED', 'LAYOUT_STALE_BEFORE_SAVE', 'LAYOUT_VERIFICATION_FALLBACK',
  'NOT_A_LAYOUT_CONTAINER', 'NOT_A_TAB_LAYOUT', 'PERSISTENCE_OUTCOME_UNKNOWN',
  'PERSIST_FAILED', 'READ_ONLY', 'RESOURCE_STALE', 'WIDGET_NOT_FOUND'
])
const PERSISTENCE_STAGES = new Set(['transport', 'backend_response', 'receipt_validation'])
const PERSISTENCE_REASONS = new Set([
  'transport_failed', 'backend_rejected', 'response_unconfirmed', 'receipt_incomplete',
  'local_state_conflict'
])
const PREFLIGHT_STAGES = new Set([
  'pending_writes', 'snapshot_validation', 'context_validation',
  'proposal_validation', 'capability_validation', 'permission_validation',
  'content_validation', 'unknown'
])
const PERSISTENCE_PAIRS = new Map([
  ['transport', 'transport_failed'],
  ['backend_response', new Set(['backend_rejected', 'response_unconfirmed'])],
  ['receipt_validation', new Set(['receipt_incomplete', 'local_state_conflict'])]
])

function diagnosticCode(value, allowlist) {
  return typeof value === 'string' && allowlist.has(value) ? value : null
}

export function createLayoutDiagnostics({ api }) {
  const runs = new Map()
  const keyOf = scope => JSON.stringify([scope.sessionId, scope.toolCallId])
  return {
    async solve(problem, scope, signal) {
      const { preparationDiagnostics, ...geometry } = problem
      const body = { toolCallId: scope.toolCallId, layoutAttempt: scope.layoutAttempt, problem: geometry }
      const base = `/api/sessions/${encodeURIComponent(scope.sessionId)}`
      let legacy = false
      const checkCancelled = () => {
        if (signal?.aborted) throw Object.assign(new Error('Layout cancelled'), { name: 'AbortError' })
      }
      checkCancelled()
      if (geometry.orderSources !== undefined || geometry.requiredBefore !== undefined) {
        let capabilities
        try {
          capabilities = await api(`${base}/dashboardLayoutCapabilities`, { method: 'GET', signal })
        } catch (error) {
          if (error?.status !== 404) throw error
        }
        checkCancelled()
        legacy = capabilities?.readingContract !== 'local-regions-v1'
        if (legacy) {
          // An old server can enforce the seed order, but cannot enforce new hard edges.
          if (geometry.requiredBefore !== undefined &&
              (!Array.isArray(geometry.requiredBefore) || geometry.requiredBefore.length)) {
            throw new Error('Layout server does not support required reading constraints')
          }
          const { orderSources, requiredBefore, ...strictGeometry } = geometry
          body.problem = strictGeometry
        }
      }
      if (preparationDiagnostics !== undefined) body.preparationDiagnostics = preparationDiagnostics
      const result = await api(
        `${base}/dashboardLayoutSolve`,
        { method: 'POST', signal, body: JSON.stringify(body) }
      )
      if (typeof result?.layoutRunId === 'string') {
        const key = keyOf(scope)
        const ids = runs.get(key) || []
        runs.set(key, [...ids, result.layoutRunId].slice(-4))
        if (runs.size > 64) runs.delete(runs.keys().next().value)
      }
      return legacy ? { ...result, readingContract: 'legacy-strict' } : result
    },
    async report(scope, envelope) {
      const key = keyOf(scope)
      const ids = runs.get(key) || []
      runs.delete(key)
      const data = envelope?.data || {}
      const summary = data.summary || {}
      const firstIssue = Array.isArray(envelope?.issues) ? envelope.issues[0] : null
      const constraints = firstIssue?.constraints || {}
      const status = ['success', 'partial', 'error'].includes(envelope?.status) ? envelope.status : 'unknown'
      const persisted = data.persisted === true
      const failureEvidence = status === 'error' && !persisted
      const committed = failureEvidence && envelope?.error?.committed === true ? true : null
      const persistenceStage = failureEvidence && PERSISTENCE_STAGES.has(constraints.persistenceStage)
        ? constraints.persistenceStage : null
      const persistenceReason = failureEvidence && PERSISTENCE_REASONS.has(constraints.persistenceReason)
        ? constraints.persistenceReason : null
      const expectedReason = PERSISTENCE_PAIRS.get(persistenceStage)
      const coherentPersistence = persistenceStage !== null && constraints.writeDispatched === true &&
        (expectedReason instanceof Set
          ? expectedReason.has(persistenceReason)
          : expectedReason === persistenceReason)
      const preflightStage = failureEvidence && constraints.writeDispatched === false &&
        PREFLIGHT_STAGES.has(constraints.preflightStage) ? constraints.preflightStage : null
      const preflightError = preflightStage !== null && typeof constraints.preflightError === 'string' &&
        constraints.preflightError.length > 0 && constraints.preflightError.length <= 300
        ? constraints.preflightError : null
      const outcome = {
        toolCallId: scope.toolCallId,
        status,
        persisted,
        executionMode: ['remote', 'linear_fallback'].includes(summary.executionMode) ? summary.executionMode : null,
        fallbackReason: ['remote_unavailable', 'candidate_validation_failed'].includes(summary.fallbackReason)
          ? summary.fallbackReason : null,
        errorCode: failureEvidence ? diagnosticCode(envelope?.error?.code, ERROR_CODES) : null,
        firstIssueCode: failureEvidence ? diagnosticCode(firstIssue?.code, ISSUE_CODES) : null,
        writeDispatched: failureEvidence && typeof constraints.writeDispatched === 'boolean'
          ? constraints.writeDispatched : null,
        committed,
        persistenceStage: coherentPersistence ? persistenceStage : null,
        persistenceReason: coherentPersistence ? persistenceReason : null,
        ...(preflightStage !== null && preflightError !== null
          ? { preflightStage, preflightError } : {}),
        changes: (Array.isArray(data.layoutChanges) ? data.layoutChanges : []).slice(0, 200).map(change => ({
          id: String(change.widgetId), x: change.after?.x, y: change.after?.y,
          w: change.after?.width, h: change.after?.height
        })),
        changesTruncated: data.layoutChangesTruncated === true || data.layoutChanges?.length > 200
      }
      // Reporting is bounded and best-effort; it cannot turn a saved layout into an error.
      await Promise.allSettled(ids.map(async id => {
        const controller = new AbortController()
        const timer = setTimeout(() => controller.abort(), 2000)
        try {
          await api(`/api/sessions/${encodeURIComponent(scope.sessionId)}/dashboardLayoutRuns/${encodeURIComponent(id)}/outcome`,
            { method: 'POST', signal: controller.signal, body: JSON.stringify(outcome) })
        } finally { clearTimeout(timer) }
      }))
    }
  }
}
