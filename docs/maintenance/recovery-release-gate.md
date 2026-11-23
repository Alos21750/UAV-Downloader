# Download recovery release gate

A passing suite or a window that opens is not sufficient evidence for a recovery fix.

1. Reproduce the reported sequence on the previous version. Record the failed
   assertion and where the failure occurred (transfer, queue, restart or export).
2. Run `tests/test_recovery_lifecycle.py` as part of the Windows build. Exercise
   an explicit 403 alongside transient errors, a later permitted retry, and a
   Watcher source failure with an existing cache. Do not retry refused segments
   within the same attempt or promote an incomplete video to history.
3. For Browser, use the normal Retry action, fail the next attempt, save the queue,
   restart in a separate interpreter, remux with the release FFmpeg, independently
   decode the result and verify the original was not published or replaced.
   Alter a cached segment and confirm export fails without creating another file.
4. In the frozen Browser, restore an isolated synthetic failed row and exercise
   the actual Export partial action. Check the resulting file and queue state.
   Use the exact attested executable and publish the unchanged bytes only after
   the desktop and public asset checks pass.
5. A generated fixture verifies client behavior, not access to a particular
   third-party video. Do not close a provider-availability report based on it.
