---
name: qa-pass
description: Run before saying a client site is done.
---

# QA pass

1. `npx tsc --noEmit`
2. Keyboard: Tab through header, main, forms, footer. Focus never disappears.
3. Forms: submit empty, confirm the error text is in the DOM, not color-only.
4. Images: every meaningful img has alt. Decorative imgs have alt="".
5. No invented metrics on screen.
6. Skip link reaches `#main`.
7. Mobile 375px: no horizontal scroll, CTA reachable.

If any step fails, fix it. Do not claim done.
