---
title: "Pack Dashboard"
type: meta
tags: [dashboard, meta, alex-hormozi]
pack: alex-hormozi-public
retrieval_strategy: navigation
---

# Pack Dashboard

Use this page in Obsidian to monitor hydration and validation. It is
navigation-only and must not be treated as expert-source content.

```dataview
TABLE rows.file.link AS Files, length(rows) AS Count
FROM ""
WHERE type != null AND type != "meta" AND type != "index"
GROUP BY type
SORT length(rows) DESC
```
