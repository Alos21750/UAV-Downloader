# Interruptible shared rate limiting

An active byte-budget wait used to retain the original rate decision. Changing
the cap to unlimited could leave that wait running indefinitely, and cancelling
a download did not stop an HLS or direct-transfer worker inside the limiter.

The shared token bucket now uses monotonic time. Rate changes notify all
waiters, preserve spent credit while adjusting an existing cap, and recheck
the current cap. The optional nonblocking cancellation callback returns `False`
before the waiting consumer writes its chunk. While throttled, cancellation is
checked at intervals of at most 50 ms, excluding thread scheduling delays.
Cancelling one consumer does not disable throttling for other downloads.

HLS, serial direct transfers and parallel direct ranges pass their existing
cancel flags. Parallel ranges also observe their existing peer-failure event,
so a failed range does not leave another range stuck waiting for byte credit.
Existing one-argument limiter callers can continue ignoring its return value.

This change retains Tk entry points and layouts, settings, queue/history
formats, public CLI options and site transport hooks. It introduces no new
dependencies or diagnostic output. It does not interrupt blocked DNS/TLS,
network reads or filesystem operations, and does not provide a general atomic
cancellation/publication boundary or file-ownership protocol.

Regressions in `tests/test_speed_limiter.py` use controlled threads/transports
and a virtual clock. Four behavioral cases fail on official master `bb65896`
and pass with the fix: disabling a cap with existing waiters and cancelling
throttled HLS, serial direct and parallel direct transfers. Full-suite and local
executable checks are contributor evidence, not maintainer acceptance.
