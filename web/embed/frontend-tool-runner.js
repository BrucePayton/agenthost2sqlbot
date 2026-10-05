function toolErrorMessage(toolCallId, cause, contextVersion) {
  const code = cause?.code || 'RUN_ERROR'
  return {
    id: null,
    role: 'tool',
    toolCallId,
    error: code,
    content: JSON.stringify({
      schemaVersion: 'davinci-tool-error-v1',
      ok: false,
      code,
      message: cause?.message || 'Frontend Tool failed.',
      contextVersion: contextVersion ?? null
    })
  }
}

function successToolMessage(toolCallId, result) {
  return {
    id: null,
    role: 'tool',
    toolCallId,
    content: JSON.stringify(result ?? {})
  }
}

export function nativeToolMessage(toolCallId, envelope) {
  return {
    id: null,
    role: 'tool',
    toolCallId,
    ...(envelope?.status === 'error'
      ? { error: envelope?.error?.code || 'EXECUTION_FAILED' }
      : {}),
    content: JSON.stringify(envelope ?? {})
  }
}

export function createFrontendToolRunner({
  agent,
  bridge,
  createRunId,
  createMessageId,
  getTools,
  getContextItems = () => [],
  initialContext = null,
  buildAgentState = (context) => context,
  getForwardedProps = () => ({}),
  onRunError = () => {},
  onRecoveryChange = () => {},
  maxFrontendSteps = 32
}) {
  let currentContext = initialContext
  let activeRunId = null
  let aborted = false
  let lastRunToolSetId
  let lastRunCatalogDigest
  let pendingDelivery = null

  const runOnce = async (runId = createRunId()) => {
    activeRunId = runId
    const deferredCalls = []
    let runError = null
    const toolSetId = typeof currentContext?.toolSetId === 'string'
      ? currentContext.toolSetId
      : null
    const catalogDigest = typeof currentContext?.catalogDigest === 'string'
      ? currentContext.catalogDigest
      : null
    const toolSetChanges = lastRunToolSetId !== undefined &&
      lastRunToolSetId !== toolSetId
      ? 1
      : 0
    const catalogDigestChanges = lastRunCatalogDigest !== undefined &&
      lastRunCatalogDigest !== catalogDigest
      ? 1
      : 0
    lastRunToolSetId = toolSetId
    lastRunCatalogDigest = catalogDigest
    try {
      await agent.runAgent(
        {
          runId,
          tools: getTools(currentContext),
          context: getContextItems(currentContext),
          forwardedProps: {
            ...getForwardedProps(),
            profile: 'davinci-agui-native-v2',
            ...(typeof currentContext?.profileId === 'string'
              ? { profileId: currentContext.profileId }
              : {}),
            ...(typeof currentContext?.catalogDigest === 'string'
              ? { catalogDigest: currentContext.catalogDigest }
              : {}),
            ...(typeof currentContext?.toolSetId === 'string'
              ? { toolSetId: currentContext.toolSetId }
              : {}),
            toolSetChanges,
            catalogDigestChanges
          }
        },
        {
          onToolCallEndEvent(call) {
            deferredCalls.push(call)
          },
          onRunErrorEvent({ event }) {
            if (!aborted) {
              runError = Object.assign(new Error(event.message || 'Agent run failed'), {
                code: event.code,
                runId
              })
              onRunError(event)
            }
          }
        }
      )
      if (runError) throw runError
    } catch (cause) {
      if (!aborted) {
        const error = cause instanceof Error ? cause : new Error(String(cause))
        error.runId = runId
        throw error
      }
    }
    return deferredCalls
  }

  const continueToolCall = async (call) => {
    let message
    const isNative = typeof bridge.executeToolCall === 'function'
    try {
      const ack = isNative
        ? await bridge.executeToolCall(
            call.event.toolCallId,
            call.toolCallName,
            call.toolCallArgs
          )
        : await bridge.execute(call.toolCallName, call.toolCallArgs)
      if (isNative) {
        message = nativeToolMessage(call.event.toolCallId, ack)
      } else {
        if (ack?.status !== 'executed') {
          const cause = new Error(
            ack?.error?.message || 'Davinci frontend Tool failed.'
          )
          cause.code = ack?.error?.code || 'RUN_ERROR'
          throw cause
        }
        message = successToolMessage(call.event.toolCallId, ack.result)
      }
    } catch (cause) {
      message = isNative
        ? nativeToolMessage(call.event.toolCallId, {
            status: 'error',
            error: {
              code: cause?.code || 'EXECUTION_FAILED',
              message: cause?.message || 'Frontend Tool failed.',
              retryable: false,
              layer: 'bridge'
            },
            issues: []
          })
        : toolErrorMessage(
            call.event.toolCallId,
            cause,
            currentContext?.contextVersion
          )
    }
    return message
  }

  const submitToolResults = async (messages) => {
    currentContext = await bridge.requestContext()
    agent.setState(buildAgentState(currentContext))
    messages.forEach((message) => {
      if (!message.id) message.id = createMessageId()
      if (!agent.messages?.some(saved => saved.id === message.id)) agent.addMessage(message)
    })
  }

  const runUntilComplete = async (nextRunId = null) => {
    for (let step = 0; step <= maxFrontendSteps; step += 1) {
      if (aborted) return
      const deferredCalls = await runOnce(nextRunId || createRunId())
      nextRunId = null
      if (!aborted && pendingDelivery) {
        pendingDelivery = null
        onRecoveryChange(null)
      }
      if (!deferredCalls.length || aborted) return
      if (step === maxFrontendSteps) {
        throw new Error('Frontend Tool step limit exceeded.')
      }
      pendingDelivery = {
        originRunId: activeRunId,
        continuationRunId: createRunId(),
        toolCallIds: deferredCalls.map(call => call.event.toolCallId),
        messages: []
      }
      // Save only identifiers outside this runner; receipts remain in trusted page memory.
      onRecoveryChange({ originRunId: pendingDelivery.originRunId,
        continuationRunId: pendingDelivery.continuationRunId, toolCallIds: pendingDelivery.toolCallIds })
      const messages = await Promise.all(deferredCalls.map(continueToolCall))
      messages.forEach(message => { message.id = createMessageId() })
      pendingDelivery.messages = messages
      await submitToolResults(messages)
      nextRunId = pendingDelivery.continuationRunId
    }
  }

  return {
    async runUserMessage(text) {
      if (pendingDelivery) throw Object.assign(new Error('请先恢复上次工具结果'), { code: 'TOOL_CONTINUATION_REQUIRED' })
      aborted = false
      currentContext = await bridge.requestContext()
      agent.setState(buildAgentState(currentContext))
      agent.addMessage({
        id: createMessageId(),
        role: 'user',
        content: text
      })
      try {
        await runUntilComplete()
        return { aborted }
      } finally {
        activeRunId = null
      }
    },
    /** Resubmit receipts with the original continuation identity; never execute their tools. */
    async resumeToolResults(messages, continuationRunId, originRunId = null) {
      if (!continuationRunId || !messages.length) throw new Error('缺少原工具回执或续接标识')
      aborted = false
      pendingDelivery = { originRunId, continuationRunId,
        toolCallIds: messages.map(message => message.toolCallId), messages: structuredClone(messages) }
      onRecoveryChange({ originRunId, continuationRunId, toolCallIds: pendingDelivery.toolCallIds })
      try {
        await submitToolResults(pendingDelivery.messages)
        await runUntilComplete(continuationRunId)
        return { aborted }
      } finally {
        activeRunId = null
      }
    },
    /** Return this runner's in-memory receipts for recovery without another page execution. */
    getPendingToolResults() {
      return pendingDelivery ? structuredClone(pendingDelivery) : null
    },
    continueToolCall,
    getCurrentContext() {
      return currentContext
    },
    setCurrentContext(context) {
      currentContext = context
    },
    getCurrentRunId() {
      return activeRunId
    },
    abort() {
      aborted = true
      agent.abortRun?.()
    }
  }
}
