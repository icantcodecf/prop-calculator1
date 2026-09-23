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
            recommendation = "ABORT: Maximum Conflict"
            confidence = "❌"
        elif under_signals > 0:
            recommendation = f"Bet UNDER Line {row.get('dk_line')}"
            confidence = "⭐" * under_signals
        elif over_signals > 0:
            recommendation = f"Bet OVER Line {row.get('dk_line')}"
            confidence = "⭐" * over_signals
        else:
            recommendation = "PASS"
            confidence = "None"

        if triggers:
            results.append({
                "Target": row.get('player_or_team'),
                "Prop Type": row.get('prop_type'),
                "Line": row.get('dk_line'),
                "Systems Hit": " + ".join(triggers),
                "Confidence": confidence,
                "Action": recommendation
            })
    return pd.DataFrame(results)

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

    for event in events:
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
    # st.cache_resource persists this across Streamlit reruns for the whole
    # session, so the model only loads once instead of on every button tap.
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
    """
    Scores each individual trigger (not the combined recommendation) against
    what actually happened, so conflicting triggers on the same row don't
    hide whether either one has real signal on its own.
    Expects columns: dk_line, actual_value, and the same trigger columns
    used elsewhere (public_money_pct, reverse_line_movement, is_letdown_spot,
    is_elite_schematic_matchup).
    """
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
            continue  # skip rows we can't score

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
        return pd.DataFrame(columns=['Trigger', 'Fired', 'Wins', 'Losses', 'Pushes', 'Win Rate', '_win_rate_num'])

    rec_df = pd.DataFrame(records)
    summary = []
    for trigger_name, group in rec_df.groupby('trigger'):
        wins = int((group['outcome'] == 'win').sum())
        losses = int((group['outcome'] == 'loss').sum())
        pushes = int((group['outcome'] == 'push').sum())
        fired = len(group)
        decided = wins + losses
        win_rate = wins / decided if decided > 0 else None
        summary.append({
            'Trigger': trigger_name,
            'Fired': fired,
            'Wins': wins,
            'Losses': losses,
            'Pushes': pushes,
            'Win Rate': f"{win_rate:.1%}" if win_rate is not None else "N/A",
            '_win_rate_num': win_rate,
        })
    return pd.DataFrame(summary)


BACKTEST_SAMPLE = pd.DataFrame([
    {"player_or_team": "P. Mahomes", "prop_type": "Pass Yds", "dk_line": 275.5, "actual_value": 301,
     "public_money_pct": 82, "reverse_line_movement": True, "is_letdown_spot": False, "is_elite_schematic_matchup": False},
    {"player_or_team": "T. Kelce", "prop_type": "Receptions", "dk_line": 6.5, "actual_value": 4,
     "public_money_pct": 70, "reverse_line_movement": False, "is_letdown_spot": True, "is_elite_schematic_matchup": False},
    {"player_or_team": "CMC", "prop_type": "Rush Yds", "dk_line": 85.5, "actual_value": 112,
     "public_money_pct": 55, "reverse_line_movement": False, "is_letdown_spot": False, "is_elite_schematic_matchup": True},
])

# --- STREAMLIT IPHONE UI INTERFACE ---
st.set_page_config(page_title="Prop Calculator", layout="centered")
st.title("📱 Prop Betting Model Calculator")

# Sidebar setup for easy API input on mobile
with st.sidebar:
    st.subheader("Config Settings")
    active_api_key = st.text_input("Odds API Key", value=API_KEY, type="password")

if "props_df" not in st.session_state:
    st.session_state.props_df = pd.DataFrame()

# 1. Fetch live lines layout
st.subheader("🔄 Live DraftKings Lines")
sport = st.selectbox(
    "Sport Selection",
    ["americanfootball_nfl", "basketball_nba", "baseball_mlb", "icehockey_nhl"],
    index=0,
)
if st.button("Fetch Live Lines"):
    if not active_api_key or active_api_key == "YOUR_THE_ODDS_API_KEY":
        st.error("Enter your Odds API key in the sidebar first.")
    else:
        with st.spinner("Fetching data from API..."):
            try:
                live_df = build_props_dataframe(sport_key=sport, api_key=active_api_key)
                if live_df.empty:
                    st.warning("No live lines found for this sport market right now.")
                else:
                    st.session_state.props_df = pd.concat([st.session_state.props_df, live_df], ignore_index=True)
                    st.success(f"Added {len(live_df)} live lines to target data frame!")
            except requests.HTTPError as e:
                st.error(f"API error: {e}")
            except requests.RequestException as e:
                st.error(f"Network error: {e}")

# 2. Screenshot upload layout
st.subheader("📸 Upload Underdog Screenshot")
uploaded_file = st.file_uploader("Upload Image", type=["png", "jpg", "jpeg"])

if uploaded_file is not None:
    with st.spinner("Processing OCR text variables..."):
        image = Image.open(uploaded_file).convert("RGB")
        ocr_df = parse_screenshot_to_rows(image)
        if ocr_df.empty:
            st.error("Could not parse matching props from image. Check screenshot crop.")
        else:
            st.session_state.props_df = pd.concat([st.session_state.props_df, ocr_df], ignore_index=True)
            st.success(f"Extracted {len(ocr_df)} player props from image layout!")

# 3. Interactive Editor and Calculation Engine
if not st.session_state.props_df.empty:
    st.subheader("📊 Interactive Dashboard Data")
    st.info("Mobile Note: Tap individual grid fields below to override percentages or toggle system logic checkboxes.")
    
    # Custom interactive editor built for mobile views
    edited_df = st.data_editor(
        st.session_state.props_df,
        column_config={
            "public_money_pct": st.column_config.NumberColumn("Public %", min_value=0, max_value=100, step=1),
            "reverse_line_movement": st.column_config.CheckboxColumn("RLM Spot?"),
            "is_letdown_spot": st.column_config.CheckboxColumn("Letdown Spot?"),
            "is_elite_schematic_matchup": st.column_config.CheckboxColumn("Elite Matchup?"),
        },
        disabled=["player_or_team", "prop_type", "dk_line"],
        use_container_width=True,
    )
    st.session_state.props_df = edited_df

    if st.button("🧮 Run Matchup Model Calculator", type="primary"):
        results_df = calculate_perfect_prop_triggers(edited_df)
        st.subheader("🎯 Model Betting Recommendations")
        if results_df.empty:
            st.info("No system triggers cleared your minimum thresholds (e.g. 80% RLM or 65% Letdown).")
        else:
            st.dataframe(results_df, use_container_width=True)
            
    if st.button("🗑️ Clear Target Table"):
        st.session_state.props_df = pd.DataFrame()
        st.rerun()

# 4. Backtesting
st.divider()
st.subheader("🧪 Backtest Your Triggers")
st.caption(
    "Upload closed props (with what actually happened) to see whether each trigger "
    "beats a coin flip, scored individually so a conflict on one row doesn't hide "
    "whether either side of it actually has signal."
)

st.download_button(
    "Download sample CSV format",
    BACKTEST_SAMPLE.to_csv(index=False),
    file_name="backtest_template.csv",
    mime="text/csv",
)

backtest_file = st.file_uploader(
    "Upload closed props CSV (needs dk_line + actual_value columns)",
    type=["csv"],
    key="backtest_uploader",
)

if backtest_file is not None:
    hist_df = pd.read_csv(backtest_file)
    required_cols = {"dk_line", "actual_value"}
    missing = required_cols - set(hist_df.columns)
    if missing:
        st.error(f"CSV is missing required column(s): {', '.join(sorted(missing))}")
    else:
        backtest_results = run_backtest(hist_df)
        if backtest_results.empty:
            st.warning("No trigger fired on any row in this file — nothing to score.")
        else:
            st.dataframe(
                backtest_results.drop(columns=['_win_rate_num']),
                use_container_width=True,
            )
            chart_data = backtest_results.dropna(subset=['_win_rate_num']).set_index('Trigger')['_win_rate_num']
            if not chart_data.empty:
                st.bar_chart(chart_data)
            st.caption(
                "Win Rate excludes pushes. 50% is coin-flip; at standard -110 odds you need "
                "roughly 52.4% just to break even after the vig."
            )
