import sqlite3
import matplotlib.pyplot as plt

# ====================== LOAD DATA & CALCULATE PIVOT POINTS ======================
db_file = "ibkr_spx_daytrader.db"

conn = sqlite3.connect(db_file)
cur = conn.cursor()

# Get High, Low (entire 3 months) + latest Close
h = cur.execute("SELECT MAX(high) FROM spx_5min").fetchone()[0]
l = cur.execute("SELECT MIN(low) FROM spx_5min").fetchone()[0]
c = cur.execute("SELECT close FROM spx_5min ORDER BY date DESC LIMIT 1").fetchone()[0]

pivot = (h + l + c) / 3
r2 = pivot + (h - l)
r1 = 2 * pivot - l
s1 = 2 * pivot - h
s2 = pivot - (h - l)

print("✅ Pivot Point Support / Resistance Levels (full 3-month data):")
print(f"   R2 (Strong Resistance) : {r2:.2f}")
print(f"   R1 (Resistance)        : {r1:.2f}")
print(f"   S1 (Support)           : {s1:.2f}")
print(f"   S2 (Strong Support)    : {s2:.2f}")

# Get last 1000 bars for clean plotting (≈ 2 weeks)
bars = cur.execute("""
    SELECT close 
    FROM spx_5min 
    ORDER BY date DESC 
    LIMIT 1000
""").fetchall()
closes = [row[0] for row in reversed(bars)]   # chronological order

conn.close()

# ====================== PLOT WITH PRICE LABELS ALONG LINES ======================
fig, ax = plt.subplots(figsize=(14, 7))

# Price line
ax.plot(range(len(closes)), closes, label="SPX Close (5-min)", color="#1f77b4", linewidth=1.2)

# Horizontal S/R lines + price labels on the right
levels = [
    (r2, "R2", "#d62728"),
    (r1, "R1", "#ff7f0e"),
    (s1, "S1", "#2ca02c"),
    (s2, "S2", "#17becf")
]

for price, label, color in levels:
    ax.axhline(price, color=color, linestyle="--", linewidth=1.8, alpha=0.85)
    # Price number right-aligned along the line
    ax.text(len(closes) * 0.97, price, f"{label} {price:.2f}",
            fontsize=11, fontweight="bold", verticalalignment="center",
            bbox=dict(boxstyle="round,pad=0.3", facecolor="white", alpha=0.9))

ax.set_title("SPX 5-min Chart — Support & Resistance Levels (Pivot Points)", fontsize=14, pad=20)
ax.set_xlabel("Last 1,000 bars (≈ 2 weeks)")
ax.set_ylabel("Price")
ax.legend(loc="upper left")
ax.grid(True, alpha=0.3)

plt.tight_layout()
plt.savefig("spx_support_resistance.png", dpi=300, bbox_inches="tight")
plt.show()

print("\n✅ Plot saved as: spx_support_resistance.png")