import { isRequestKilled } from '../requests/requestState.js';

const cancellationError = (code, message) => Object.assign(new Error(message), { code });

/** Create an invocation-local cancellation signal and monitor its request killswitch. */
export function createRequestCancellation({
    user,
    requestId,
    responseStream,
    requestOptions,
    pollIntervalMs = 500,
    checkKilled = isRequestKilled,
    pollImmediately = true,
    onAbort,
    parentSignal
}) {
    const controller = new AbortController();
    let pollTimer;
    let stopped = false;
    let pollInFlight = false;

    const abort = error => {
        if (!controller.signal.aborted) {
            controller.abort(error);
            onAbort?.(error);
        }
    };
    const onStreamClose = () => {
        if (!responseStream?.writableEnded && !responseStream?.writableFinished) {
            abort(cancellationError('CLIENT_DISCONNECTED', 'Client disconnected'));
        }
    };
    if (responseStream?.destroyed) {
        onStreamClose();
    }

    responseStream?.on?.('close', onStreamClose);
    const onParentAbort = () => abort(parentSignal.reason || cancellationError('REQUEST_CANCELLED', 'Request cancelled'));
    if (parentSignal?.aborted) onParentAbort();
    else parentSignal?.addEventListener('abort', onParentAbort, { once: true });
    requestOptions.signal = controller.signal;

    const poll = async () => {
        if (stopped || controller.signal.aborted || pollInFlight || !user || !requestId) return;
        pollInFlight = true;
        try {
            const killed = await checkKilled(user, requestId);
            if (!stopped && killed) abort(cancellationError('KILLSWITCH_CANCELLED', 'Request cancelled by killswitch'));
        } catch {
            // A transient state-store failure must not turn an active chat into a failure.
        } finally {
            pollInFlight = false;
            if (!stopped && !controller.signal.aborted) {
                pollTimer = setTimeout(poll, pollIntervalMs);
                pollTimer.unref?.();
            }
        }
    };
    let ready;
    if (pollImmediately) ready = poll();
    else {
        pollTimer = setTimeout(poll, pollIntervalMs);
        pollTimer.unref?.();
        ready = Promise.resolve();
    }

    return {
        signal: controller.signal,
        ready,
        abort,
        cleanup() {
            stopped = true;
            clearTimeout(pollTimer);
            responseStream?.removeListener?.('close', onStreamClose);
            parentSignal?.removeEventListener('abort', onParentAbort);
            if (requestOptions.signal === controller.signal) delete requestOptions.signal;
        }
    };
}

export { cancellationError };
