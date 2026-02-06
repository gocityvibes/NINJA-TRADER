from __future__ import annotations
import os

# Bot mode served to Ninja (Ninja still must allow live locally)
BOT_MODE = os.getenv("BOT_MODE", "PAPER").upper()  # PAPER | LIVE
TIMEZONE = os.getenv("TIMEZONE", "America/Chicago")

# RTH in Central time
RTH_START = os.getenv("RTH_START", "08:30")
RTH_END   = os.getenv("RTH_END",   "16:00")

# Strategy thresholds (defaults loaded from your settings_live.py values)
PAMM_MIN = float(os.getenv("PAMM_MIN", "60.0"))
PAMM_MAX = float(os.getenv("PAMM_MAX", "130.0"))

# ATR multipliers
ATR_STOP_MULT   = float(os.getenv("ATR_STOP_MULT", "1.5"))
ATR_TARGET_MULT = float(os.getenv("ATR_TARGET_MULT", "3.0"))

# Elite filter thresholds
ADX_MIN = float(os.getenv("ADX_MIN", "0.0"))  # Set to 0 to disable, 22+ for strong trends
REL_VOL_MIN = float(os.getenv("REL_VOL_MIN", "0.0"))  # Set to 0 to disable, 1.2+ for high volume
REL_VOL_MAX = float(os.getenv("REL_VOL_MAX", "999.0"))
RSI_LONG_MIN = float(os.getenv("RSI_LONG_MIN", "0.0"))
RSI_SHORT_MAX = float(os.getenv("RSI_SHORT_MAX", "100.0"))

# Elite filter toggles
USE_VWAP = os.getenv("USE_VWAP", "false").lower() in ("1","true","yes")
USE_REGIME_FILTER = os.getenv("USE_REGIME_FILTER", "false").lower() in ("1","true","yes")
USE_CANDLE_PATTERNS = os.getenv("USE_CANDLE_PATTERNS", "false").lower() in ("1","true","yes")
USE_MULTI_TF_MACD = os.getenv("USE_MULTI_TF_MACD", "false").lower() in ("1","true","yes")

# Risk controls
COOLDOWN_SECONDS = int(os.getenv("COOLDOWN_SECONDS", "0"))
KILL_SWITCH = os.getenv("KILL_SWITCH", "0").lower() in ("1","true","yes")

# =====================
# Runtime management (optional, but recommended)
# =====================
# If NinjaTrader is the execution layer, Render can still provide *runtime* guidance:
# - trailing stop ladder updates (price-based, after +50 pts)
# - PAMM early exit for weak trades (before +50 pts only)
# - reversal flip signal
# - cooldown enforcement after stop
# - automatic kill switch based on daily realized P&L

# Enable the runtime manager (safe default: on)
RUNTIME_MANAGER_ENABLED = os.getenv("RUNTIME_MANAGER_ENABLED", "true").lower() in ("1","true","yes")

# PAMM early exit thresholds (ONLY used before +50 pts - cuts weak trades)
# Rule 1: Exit if PAMM < 90 after 4 bars (20 minutes)
# Rule 2: Exit if PAMM < 70 at any time
# After +50 pts: PAMM is IGNORED (strong trends have PAMM dips)
EARLY_EXIT_PAMM = float(os.getenv("EARLY_EXIT_PAMM", "70.0"))  # Threshold for Rule 2
REVERSAL_PAMM_THRESHOLD = float(os.getenv("REVERSAL_PAMM_THRESHOLD", "60.0"))

# Trailing stop ladder in *points* (price units). Tune for MBT/Bitcoin futures.
# Example: "no trailing until 50 points" -> set TRAIL_L1_PNL_PTS=50
TRAIL_L1_PNL_PTS = float(os.getenv("TRAIL_L1_PNL_PTS", "50"))
TRAIL_L1_OFFSET_PTS = float(os.getenv("TRAIL_L1_OFFSET_PTS", "25"))
TRAIL_L2_PNL_PTS = float(os.getenv("TRAIL_L2_PNL_PTS", "100"))
TRAIL_L2_OFFSET_PTS = float(os.getenv("TRAIL_L2_OFFSET_PTS", "50"))
TRAIL_L3_PNL_PTS = float(os.getenv("TRAIL_L3_PNL_PTS", "200"))
TRAIL_L3_OFFSET_PTS = float(os.getenv("TRAIL_L3_OFFSET_PTS", "75"))

# Safety clamps
MIN_STOP_MOVE_PTS = float(os.getenv("MIN_STOP_MOVE_PTS", "0.25"))
MAX_STOP_TIGHTEN_PER_POLL_PTS = float(os.getenv("MAX_STOP_TIGHTEN_PER_POLL_PTS", "2000"))

# Automatic kill switch based on daily realized P&L (USD). Requires Ninja to POST fills.
# FINAL CLEAN SPEC: 3 losing trades OR any single -$5 trade → stop
ENABLE_AUTO_KILL_SWITCH = os.getenv("ENABLE_AUTO_KILL_SWITCH", "true").lower() in ("1","true","yes")
MAX_DAILY_LOSS_USD = float(os.getenv("MAX_DAILY_LOSS_USD", "7.50"))  # 3 trades × $2.50 = $7.50 max

# Futures sizing helpers (optional; used only for P&L estimates on Render)
POINT_VALUE_USD = float(os.getenv("POINT_VALUE_USD", "5.0"))  # MBT = $5/point

# DB
DB_PATH = os.getenv("DB_PATH", "bot.db")

# Optional auth
API_KEY = os.getenv("API_KEY", "")
