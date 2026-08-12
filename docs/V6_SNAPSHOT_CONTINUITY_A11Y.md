# V6 Snapshot Continuity and Accessibility

The Dashboard, Settings and Jobs views use the existing read-only snapshot poll. Initial navigation and an explicit route change move focus to the main landmark; a background snapshot refresh does not remount an active safe control, steal focus, reset scroll, or announce the whole page. The compact status region reports only fixed local UI states such as received, deferred, preserved, or unavailable.

The skip link reaches `#main-content`. Job filters, recovery controls, and opaque artifact preview openers use fixed client focus tokens. Closing an artifact preview with Escape or its close control returns focus to the opener when it still exists, otherwise to the main landmark. These tokens never contain server IDs, paths, secrets, or artifact text.

Validation uses synthetic loopback fixtures and bounded browser checks at desktop widths 1280x720, 1920x1080 and 2560x1440, plus the mobile sidebar behavior. The fixture does not start the Hub or a runtime worker. Snapshot continuity proves interaction behavior only; it does not claim server freshness, execution, media processing, GPU/model availability, or operational runtime capability.
