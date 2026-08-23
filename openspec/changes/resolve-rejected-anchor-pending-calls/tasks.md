## 1. Exact settlement contract

- [x] Add a sanitized, content-free fixture for all three observed suffix
      shapes and reproduce the legacy rejection.
- [x] Accept bounded follow-ups only after exact durable call-id/type settlement.
- [x] Reject unrelated output ids and a second tool loop after settlement.
- [x] Add negatives for partial, duplicate, call/output-order-invalid,
      type/status drift,
      malformed agent messages, and generic developer/user tails.
- [x] Require exact settlement instead of the broader retained-output path when
      the durable manifest is non-empty.

## 2. Durable retention and exactly-once behavior

- [x] Exclude marker-bearing rows from startup, closed, and abandoned purges.
- [x] Add repository behavior coverage for all three cleanup paths, process
      replacement, and normal purge after terminal marker clear.
- [x] Add HTTP integration for all three sanitized shapes proving same-account
      unanchored send, one marker/journal claim under concurrency, unchanged
      historical tool-effect count, and terminal checkpoint binding to the
      original complete input.

## 3. Verification

- [x] Prove statically that incomplete streams do not publish a new durable
      response anchor.
- [x] Run focused replay-safety, durable repository, streaming integration,
      architecture, Ruff, Ruff format, ty, diff-check, and strict OpenSpec
      validation.
