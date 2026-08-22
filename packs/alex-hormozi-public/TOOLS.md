---
title: "Alex Hormozi Public Source Pack — Integration"
type: meta
tags: [meta, integration, mcp, alex-hormozi]
pack: alex-hormozi-public
retrieval_strategy: navigation
---

# MCP Integration

EP MCP serves this pack as a retrieval and provenance service. Its initial tool
surface is intentionally read-only:

| Tool | Purpose |
|---|---|
| `ep_search` | Retrieve relevant source-backed atoms. |
| `ep_read` | Read the complete atom or a cited source fragment. |
| `ep_list_topics` | Discover the pack's supported topics. |
| `ep_graph_traverse` | Follow explicit atom relationships once the graph exists. |

An execution agent may use these tools to form a decision brief, but it must
keep company-data access and external actions in separate MCP servers with
their own permission and approval policies.
