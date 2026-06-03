---
title: Telegram Workspace Topics
description: Configure friendly topic aliases, guides, drift checks, and diagnostics.
---

Hermes can keep an operator-authored registry of messaging topics under
`workspace.topic_registry.topics`.  The registry maps delivery refs such as
`telegram:-1001234567890:15` to friendly names and purposes.  It is used by
`send_message(action="list")`, `/whereami`, generated workspace guides, drift
checks, and ops indexes.

Example:

```yaml
workspace:
  topic_registry:
    topics:
      dev-logs:
        target: telegram:-1001234567890:15
        name: "Eternal / #dev-logs"
        purpose: builds/CI/errors

telegram:
  channel_prompts:
    "-1001234567890:15": "This topic is for builds, CI, and error reports."
```

Commands:

```bash
hermes workspace list
hermes workspace guide
hermes workspace ops-index
hermes workspace drift
```

In Telegram, send `/whereami` inside any chat or topic to verify the chat ID,
thread ID, alias, purpose, channel prompt status, and delivery target Hermes sees.

For Blaze's Eternal group, this dry-run helper shows the safe migration; add
`--apply` to merge the example registry and prompts for topics `707` and `708`
after creating a config backup:

```bash
hermes workspace eternal-example
hermes workspace eternal-example --apply
```
