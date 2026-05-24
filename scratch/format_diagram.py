import sys

left_box = [
    "   FastAPI Web Server     ",
    "   (http://localhost:8000)",
    "                          ",
    "  GET  /api/data          ",
    "  POST /api/chat          ",
    "  POST /api/save_basket   ",
    "  POST /api/save_watch    "
]

right_box = [
    "    Background Data Daemon    ",
    "   (fetch_stocks.py thread)   ",
    "                              ",
    "  Polls Yahoo Finance / SSoT  ",
    "  Updates GLOBAL_STATE every  ",
    "  30 seconds                  ",
    "                              "
]

# Generate main box lines
lines = []
lines.append("┌" + "─" * 69 + "┐")
lines.append("│" + "web_server.py (Entry Point)".center(69) + "│")
lines.append("│" + " " * 69 + "│")

# Inner boxes header
lines.append("│  ┌" + "─" * 26 + "┐   ┌" + "─" * 30 + "┐    │")

for l, r in zip(left_box, right_box):
    lines.append(f"│  │{l}│   │{r}│    │")

# Inner boxes footer with connectors
lines.append("│  └" + "─"*10 + "┬" + "─"*15 + "┘   └" + "─"*14 + "┬" + "─"*15 + "┘    │")

# Connector lines going down
lines.append("│" + " " * 13 + "│" + " " * 34 + "│" + " " * 20 + "│")
lines.append("│" + " " * 13 + "▼" + " " * 34 + "▼" + " " * 20 + "│")

# Texts
t1 = "Model Router"
t2 = "GLOBAL_STATE"
line_text = "│" + " " * 8 + t1 + " " * (43 - 8 - len(t1)) + t2 + " " * (69 - 43 - len(t2)) + "│"
lines.append(line_text)

t3 = "(Logic Tier)"
t4 = "(Shared Mem)"
line_subtext = "│" + " " * 8 + t3 + " " * (43 - 8 - len(t3)) + t4 + " " * (69 - 43 - len(t4)) + "│"
lines.append(line_subtext)

# Bottom border of the main box
lines.append("└" + "─" * 13 + "┬" + "─" * 34 + "┬" + "─" * 20 + "┘")

# Lower boxes
bottom = [
    "              │                                  │",
    "        ┌─────▼────────────────┐           ┌─────▼────────────────┐",
    "        │  GEMINI (THINKING)   │           │     GEMMA 4 31B      │",
    "        │  (Reasoning & Search)│           │  (Precision Logic)   │",
    "        └──────────────────────┘           └──────────────────────┘"
]

# Print lengths
print("=== UPPER BOX ===")
for i, line in enumerate(lines):
    print(f"Line {i+1:02d}: Length = {len(line)}")

print("\n=== BOTTOM BOX ===")
for i, line in enumerate(bottom):
    print(f"Line {i+1:02d}: Length = {len(line)}")

# Write diagram to file
with open("scratch/diagram.txt", "w", encoding="utf-8") as f:
    for line in lines:
        f.write(line + "\n")
    for line in bottom:
        f.write(line + "\n")
print("\nDiagram successfully written to scratch/diagram.txt in UTF-8 format.")
