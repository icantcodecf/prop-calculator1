import streamlit as st
import pandas as pd
import requests
import re
import easyocr  
from PIL import Image
import numpy as np

# --- ENTER YOUR OBTAINED ODDS-API KEY HERE ---
API_KEY = "YOUR_THE_ODDS_API_KEY"  
BASE_URL = "https://api.the-odds-api.com/v4/sports"

# --- CUSTOM THEME & GRAPHICS CSS ---
st.set_page_config(page_title="PROPS SHARP", layout="centered")

# Injecting clean UI CSS injection to style mobile containers
st.markdown("""
    <style>
        @import url('https://googleapis.com');
        
        /* Main page wrapper */
        .main .block-container {
            padding-top: 1.5rem !important;
            max-width: 500px !important;
        }
        
        /* App Branding header */
        .brand-header {
            text-align: center;
            padding: 12px;
            background: linear-gradient(135deg, #111827 0%, #1f2937 100%);
            border: 1px solid #374151;
            border-radius: 12px;
            margin-bottom: 20px;
            box-shadow: 0 4px 6px -1px rgba(0,0,0,0.1);
        }
        .brand-title {
            color: #10B981 !important;
            font-family: 'JetBrains Mono', monospace !important;
            font-weight: 700 !important;
            font-size: 24px !important;
            letter-spacing: 1px;
            margin: 0 !important;
        }
        
        /* Action Result Card Styling */
        .card {
            background-color: #1F2937;
            border-radius: 12px;
            padding: 16px;
            margin-bottom: 12px;
            border-left: 5px solid #6B7280;
        }
        .card-over { border-left: 5px solid #10B981; background: linear-gradient(90deg, #064E3B 0%, #1F2937 40%); }
        .card-under { border-left: 5px solid #EF4444; background: linear-gradient(90deg, #7F1D1D 0%, #1F2937 40%); }
        .card-abort { border-left: 5px solid #F59E0B; background: linear-gradient(90deg, #78350F 0%, #1F2937 40%); }
        
        .card-player { font-size: 18px; font-weight: 700; color: #FFFFFF; }
        .card-meta { font-size: 13px; color: #9CA3AF; margin-top: 2px; }
        .card-action { font-size: 15px; font-weight: 700; color: #F3F4F6; margin-top: 8px; }
        .card-system { font-size: 12px; color: #10B981; font-family: 'JetBrains Mono', monospace; margin-top: 4px; }
    </style>
""", unsafe_allow_html=True)

# --- SYSTEM LOGIC ---
def normalize_boolean(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ['true', 'yes', '1', 'y', 'checked']
    return False

def calculate_perfect_prop_triggers(df_props):
    results = []
    for idx, row in df_props.iterrows():
        is_letdown = normalize_boolean(row.get('is_letdown_spot'))
        is_rlm = normalize_boolean(row.get('reverse_line_movement'))
        is_mismatch = normalize_boolean(row.get('is_elite_schematic_matchup'))

        try:
            public_pct = int(row.get('public_money_pct', 50))
        except (ValueError, TypeError):
            public_pct = 50

        triggers = []
        under_signals, over_signals = 0, 0

        if is_letdown and public_pct >= 65:
            triggers.append("Emotional Letdown")
            under_signals += 1
        if public_pct >= 80 and is_rlm:
            triggers.append("Asymmetric Public Fade")
            under_signals += 1
        if is_mismatch:
            triggers.append("Schematic Matchup")
            over_signals += 1

        if under_signals > 0 and over_signals > 0:
            rec, conf, style = "ABORT: Maximum System Conflict Detected", "❌", "card-abort"
        elif under_signals > 0:
            rec, conf, style = f"Bet UNDER DraftKings Line {row.get('dk_line')}", "⭐" * under_signals, "card-under"
        elif over_signals > 0:
            rec, conf, style = f"Bet OVER DraftKings Line {row.get('dk_line')}", "⭐" * over_signals, "card-over"
        else:
            rec, conf, style = "PASS", "None", "card"

        if triggers:
            results.append({
                "Target": row.get('player_or_team'),
                "Prop Type": row.get('prop_type'),
                "Line": row.get('dk_line'),
                "Systems Hit": " + ".join(triggers),
                "Confidence Rating": conf,
                "Calculated Action": rec,
                "Style": style
            })
    return results

# --- LIVE ODDS API INTEGRATION ---
PLAYER_PROP_MARKETS = {
    "Pass Yds": "player_pass_yds",
    "Rush Yds": "player_rush_yds",
    "Rec Yds": "player_reception_yds",
    "Receptions": "player_receptions",
    "Pass TDs": "player_pass_tds",
}

def fetch_live_events(sport_key, api_key):
    url = f"{BASE_URL}/{sport_key}/events"
    resp = requests.get(url, params={"apiKey": api_key}, timeout=15)
    resp.raise_for_status()
    return resp.json()

def fetch_event_player_props(sport_key, event_id, markets, api_key):
    url = f"{BASE_URL}/{sport_key}/events/{event_id}/odds"
    params = {
        "apiKey": api_key,
        "regions": "us",
        "markets": ",".join(markets),
        "oddsFormat": "american",
        "bookmakers": "draftkings",
    }
    resp = requests.get(url, params=params, timeout=15)
    resp.raise_for_status()
    return resp.json()

def build_props_dataframe(sport_key, api_key, selected_markets=None):
    if selected_markets is None:
        selected_markets = list(PLAYER_PROP_MARKETS.values())

    events = fetch_live_events(sport_key, api_key)
    rows = []

    for event in events[:5]:  # Cap event loop to optimize free mobile speed
        event_id = event["id"]
        try:
            data = fetch_event_player_props(sport_key, event_id, selected_markets, api_key)
        except requests.HTTPError:
            continue

        for bookmaker in data.get("bookmakers", []):
            if bookmaker["key"] != "draftkings":
                continue
            for market in bookmaker.get("markets", []):
                market_label = next(
                    (k for k, v in PLAYER_PROP_MARKETS.items() if v == market["key"]),
                    market["key"],
                )
                for outcome in market.get("outcomes", []):
                    rows.append({
                        "player_or_team": outcome.get("description", outcome.get("name")),
                        "prop_type": market_label,
                        "dk_line": outcome.get("point"),
                        "public_money_pct": 50,
                        "reverse_line_movement": False,
                        "is_letdown_spot": False,
                        "is_elite_schematic_matchup": False,
                    })

    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df = df.drop_duplicates(subset=["player_or_team", "prop_type", "dk_line"])
    return df

# --- OCR SCREENSHOT PARSING ---
PROP_LINE_REGEX = re.compile(
    r"(?P<player>[A-Za-z.\-' ]{3,30}?)\s+"
    r"(?P<side>Over|Under|O|U)\s+"
    r"(?P<line>\d+\.?\d*)\s+"
    r"(?P<prop>[A-Za-z ]{2,25})",
    re.IGNORECASE,
)

@st.cache_resource(show_spinner=False)
def get_ocr_reader():
    return easyocr.Reader(['en'], gpu=False)

def parse_screenshot_to_rows(image: Image.Image):
    reader = get_ocr_reader()
    results = reader.readtext(np.array(image), detail=0)  
    full_text = " ".join(results)

    rows = []
    for match in PROP_LINE_REGEX.finditer(full_text):
        rows.append({
            "player_or_team": match.group("player").strip(),
            "prop_type": match.group("prop").strip(),
            "dk_line": float(match.group("line")),
            "public_money_pct": 50,
            "reverse_line_movement": False,
            "is_letdown_spot": False,
            "is_elite_schematic_matchup": False,
        })
    return pd.DataFrame(rows)

# --- BACKTESTING ---
def run_backtest(df_hist):
    records = []
    for idx, row in df_hist.iterrows():
        is_letdown = normalize_boolean(row.get('is_letdown_spot'))
        is_rlm = normalize_boolean(row.get('reverse_line_movement'))
        is_mismatch = normalize_boolean(row.get('is_elite_schematic_matchup'))

        try:
            public_pct = int(row.get('public_money_pct', 50))
        except (ValueError, TypeError):
            public_pct = 50

        try:
            dk_line = float(row.get('dk_line'))
            actual_value = float(row.get('actual_value'))
        except (ValueError, TypeError):
            continue  

        if actual_value > dk_line:
            actual_side = 'over'
        elif actual_value < dk_line:
            actual_side = 'under'
        else:
            actual_side = 'push'

        signals = {}
        if is_letdown and public_pct >= 65:
            signals['Emotional Letdown'] = 'under'
        if public_pct >= 80 and is_rlm:
            signals['Asymmetric Public Fade'] = 'under'
        if is_mismatch:
            signals['Schematic Matchup'] = 'over'

        for trigger_name, direction in signals.items():
            if actual_side == 'push':
                outcome = 'push'
            elif direction == actual_side:
                outcome = 'win'
            else:
                outcome = 'loss'
            records.append({'trigger': trigger_name, 'outcome': outcome})

    if not records:
        return pd.DataFrame(columns=['Trigger', 'Fired', 'Wins', 'Losses', 'Pushes', 'Win Rate'])

    rec_df = pd.DataFrame(records)
    summary = []
    for trigger_name, group in rec_df.groupby('trigger'):
        wins = int((group['outcome'] == 'win').sum())
        losses = int((group['outcome'] == 'loss').sum())
        pushes = int((group['outcome'] == 'push').sum())
        fired = len(group)
        decided = wins + losses
        win_rate = f"{(wins / decided * 100):.1f}%" if decided > 0 else "0.0%"
        summary.append({
