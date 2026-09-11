# Fixed-frame MCU `1.0.1-hil.4` clean HIL evidence

This directory archives the controlled physical clean/bag-swap run performed
on 2026-09-10 China Standard Time (2026-09-09 UTC).

## Scope

- The operator confirmed that the delivery door was closed, the mechanism was
  unobstructed, and nobody was in the motion area before the test.
- `ecobin-business.service` was stopped before the one-time raw UART start and
  restored on exit.
- The test sent `EE 01 EE` exactly once after matching firmware identity
  `1.0.1-hil.4 / 10004 / 391ce0b83076c981` and a valid `0 g` F1 result.
- The first clean unlock energized and then automatically de-energized. The
  operator replaced the bag, closed the clean door, and pressed page8 `b1`
  exactly once after the final weight remained valid and stable.

## Terminal evidence

The long-running SSH capture disconnected before the operator pressed `b1`.
Its EXIT trap restored the business service, which then received and durably
stored the single clean result in `mcu_event_inbox` row 52. The adapter's
`rawFrameHex` is populated directly from the received frame bytes:

```text
EF 00 00 00 00 00 00 00 EF
```

This decodes as pre-weight `0 g`, post-weight `0 g`, and infrared not blocked.
Only one `COMPAT_CLEAN_RESULT` was stored after the run; no duplicate EF event
was observed after the service continuously resumed UART reading.

An isolated post-check sent only the read-only F2/F0 queries and captured:

```text
F3 01 00 02 00 00 27 14 0B 31 2E 30 2E 31 2D 68 69 6C 2E 34
00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00
39 1C E0 B8 30 76 C9 81 0F F3
F1 03 00 00 00 00 00 F1
```

`SAFE_FLAGS=0F` proves the MCU returned to idle with the delivery actuator and
clean lock outputs off. F1 remained valid at `0 g`.

## Safety-ledger boundary

This raw HIL did not create a cloud clean operation and did not clear or alter
the pre-existing recovery condition. After the run, the business work slot was
still the old `DELIVERY / RECOVERY_REQUIRED` work
`fca37401-7b2e-4b42-93cf-2fc8c6d72fb2`, and permanent physical action
`a21742a1-52bd-4fce-ac71-95c528e7897b` remained `ARMED` without a confirmed
outcome. Both business and remote-support services were active.

## Archived files and SHA-256

| File | Bytes | SHA-256 |
| --- | ---: | --- |
| `clean-hil4-20260909T202553Z.log` | 117656 | `5b2c46444ad92a48bcf6accbfda061a24942755933f7c48d89957b4237c4885e` |
| `clean-hil4-20260909T202553Z.rx.bin` | 10452 | `2317f8810d679c05ebccbdb31675c169305a38f03fef20019f3474713c28a052` |
| `clean-hil4-20260909T203102Z-postcheck.log` | 729 | `c429b7e2f88d06071aaac3ac2b7b03dfa216b97f37b6d849f429dec38bcdf27c` |
| `clean-hil4-20260909T203102Z-postcheck.rx.bin` | 59 | `7448bde37ba16e20893ef7ad84bc9adf082ebe0c9b5a3e6b60df787b0b0d3da2` |
| `ef-event.json` | 634 | `4b42f73855678c2492c020d066cef1d01be76e31c0617335d91b3b8bef4eeee4` |
