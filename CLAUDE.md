## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Note: `graphify` is not on PATH on this Windows machine. Invoke it via `python -m uv tool run --from graphifyy graphify` in all commands below.

Rules:
- For codebase questions, ALWAYS use Graphify first: `python -m uv tool run --from graphifyy graphify query "<question>"` when graphify-out/graph.json exists. Use `python -m uv tool run --from graphifyy graphify path "<A>" "<B>"` for relationships and `python -m uv tool run --from graphifyy graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- Only read source files after Graphify has named which files matter. Do NOT grep the repository first — grep is a fallback for when Graphify cannot answer.
- Before implementing any feature, ask Graphify whether similar functionality already exists (`query`/`explain`) so nothing is reinvented.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `python -m uv tool run --from graphifyy graphify update .` to keep the graph current (AST-only, no API cost). A PostToolUse hook in .claude/settings.json runs this automatically after Edit/Write.
