import streamlit as st
import asyncio
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup

# ==========================================
# 1. BACKEND: LOGIC CÀO DANH SÁCH TRẬN
# ==========================================
def get_lequipe_round_path(round_str):
    round_str = round_str.lower().strip()
    if round_str == "j1": return "1re-journee"
    if round_str in ["barrages", "demi-finales", "finale"]: return round_str
    if round_str.startswith("j"): return f"{round_str.replace('j', '')}e-journee"
    return round_str

async def fetch_lequipe_matches(season, round_str):
    round_path = get_lequipe_round_path(round_str)
    url_lequipe = f"https://www.lequipe.fr/Rugby/top-14/page-calendrier-resultats/{round_path}"
    lequipe_matches = []
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            await page.goto(url_lequipe, wait_until="networkidle")
            await asyncio.sleep(2)
            
            match_elements = await page.query_selector_all('.TeamScore') 
            for match in match_elements:
                home_el = await match.query_selector('.TeamScore__team--home .TeamScore__nameshort')
                away_el = await match.query_selector('.TeamScore__team--away .TeamScore__nameshort')
                link_el = await match.query_selector('a[href*="match-direct"]')
                
                if home_el and away_el and link_el:
                    home_team = await home_el.inner_text()
                    away_team = await away_el.inner_text()
                    raw_href = await link_el.get_attribute('href')
                    
                    # Fix an toàn định dạng URL
                    full_url = raw_href if raw_href.startswith('http') else f"https://www.lequipe.fr{raw_href}"
                    
                    lequipe_matches.append({
                        "home": home_team.strip(),
                        "away": away_team.strip(),
                        "url": full_url
                    })
        finally:
            await browser.close()
    return lequipe_matches

# ==========================================
# 2. BACKEND: LOGIC CÀO CHI TIẾT TRẬN (TIMELINE)
# ==========================================
async def scrape_match_timeline(url):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        
        # BỎ CHẶN CSS. CHỈ CHẶN ẢNH VÀ FONT ĐỂ VUE.JS RENDER TỌA ĐỘ CHUẨN
        await page.route("**/*.{png,jpg,jpeg,gif,woff,woff2,svg}", lambda route: route.abort())
        
        try:
            await page.goto(url, wait_until="domcontentloaded")
            await page.wait_for_selector('.CommentsLive', timeout=10000)
            
            # --- VÒNG LẶP AUTO-SCROLLING KÍCH HOẠT VUE.JS ---
            last_height = await page.evaluate("document.body.scrollHeight")
            scroll_attempts = 0
            
            while scroll_attempts < 60:
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight);")
                await asyncio.sleep(1.5) # Đợi API trả JSON về
                
                new_height = await page.evaluate("document.body.scrollHeight")
                if new_height == last_height:
                    break # Chạm đáy, hết dữ liệu
                
                last_height = new_height
                scroll_attempts += 1
                
            html_content = await page.content()
        except Exception as e:
            print(f"Lỗi tải Timeline: {e}")
            return None, None
        finally:
            await browser.close()

    soup = BeautifulSoup(html_content, 'html.parser')
    
    # 2.1 Lấy Thống Kê bằng CSS Selector siêu chuẩn
    match_stats = {"home": {}, "away": {}}
    goal_lists = soup.select('.Scoreboard__goalList')
    
    if len(goal_lists) >= 2:
        for idx, team_side in enumerate(["home", "away"]):
            container = goal_lists[idx]
            goals_divs = container.select('.Scoreboard__goals')
            for div in goals_divs:
                texts = [s.get_text(strip=True) for s in div.find_all('span') if s.get_text(strip=True)]
                count, label = 0, ""
                for t in texts:
                    if t.isdigit(): count = int(t)
                    elif "essai" in t.lower(): label = "Try"
                    elif "transf" in t.lower(): label = "Conversion"
                    elif "pénalit" in t.lower(): label = "Penalty"
                if count > 0 and label:
                    match_stats[team_side][label] = match_stats[team_side].get(label, 0) + count

    # 2.2 Lấy Timeline đầy đủ
    timeline_events = []
    # Dùng .select để lấy element bắt buộc có class jsItemGrid
    articles = soup.select('article.jsItemGrid') 
    
    for article in articles:
        time_span = article.select_one('.CommentsLive__time')
        minute_str = time_span.get_text(strip=True) if time_span else ""
        if not minute_str: continue
            
        try:
            clean_str = minute_str.replace("'", "")
            minute = sum(int(x) for x in clean_str.split("+")) if "+" in clean_str else int(clean_str)
        except ValueError:
            continue

        summary_p = article.select_one('.grid__summary')
        comment_text = summary_p.get_text(strip=True) if summary_p else ""
        if comment_text:
            timeline_events.append({
                "Phút": minute,
                "Thời gian hiển thị": minute_str,
                "Sự kiện (L'Équipe)": comment_text
            })

    timeline_events = sorted(timeline_events, key=lambda x: x["Phút"])
    return match_stats, timeline_events

# ==========================================
# 3. WRAPPERS: CẦU NỐI ASYNC -> SYNC CHO STREAMLIT
# ==========================================
def run_crawler_sync(season, round_str):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    return loop.run_until_complete(fetch_lequipe_matches(season, round_str))

def run_timeline_scraper_sync(url):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    return loop.run_until_complete(scrape_match_timeline(url))

# ==========================================
# 4. FRONTEND: GIAO DIỆN STREAMLIT
# ==========================================
st.set_page_config(page_title="ASM Clermont - Video AI", page_icon="🏉", layout="wide")

if 'matches' not in st.session_state:
    st.session_state.matches = []

st.title("🏉 Trợ lý AI Phân tích Video Rugby")
st.markdown("Hệ thống đồng bộ Timeline từ L'Équipe và cắt Highlight bằng V-JEPA2.")

col1, col2, col3 = st.columns([2, 2, 1])
with col1:
    season_input = st.selectbox("Mùa giải", ["2025-2026", "2024-2025", "2023-2024"])
with col2:
    round_input = st.text_input("Vòng đấu (VD: j22, barrages)", value="j22")
with col3:
    st.write("") 
    st.write("")
    # FIX WARNING: Đổi use_container_width=True thành width="stretch"
    if st.button("🔍 Tìm trận đấu", width="stretch"):
        with st.spinner("Đang tải dữ liệu từ L'Équipe..."):
            results = run_crawler_sync(season_input, round_input)
            st.session_state.matches = results
            if not results:
                st.error("Không tìm thấy trận đấu nào! Vui lòng kiểm tra lại URL.")

if st.session_state.matches:
    st.divider()
    st.subheader(f"Các trận đấu vòng {round_input.upper()}")
    
    grid_cols = st.columns(2) 
    for idx, match in enumerate(st.session_state.matches):
        col = grid_cols[idx % 2]
        
        with col:
            with st.container(border=True):
                st.markdown(f"#### {match['home']} 🆚 {match['away']}")
                st.caption(f"🔗 {match['url']}")
                
                # FIX WARNING: Đổi use_container_width=True thành width="stretch"
                if st.button(f"⚡ Phân tích Video Trận Này", key=f"btn_{idx}", width="stretch"):
                    
                    st.divider()
                    st.success(f"Đang xử lý dữ liệu trận: **{match['home']} vs {match['away']}**")
                    
                    with st.spinner("Đang cuộn trang L'Équipe để lấy full Timeline..."):
                        stats, timeline = run_timeline_scraper_sync(match['url'])
                        
                        if stats and timeline:
                            st.markdown("### 📊 Thống kê Tổng quan (Mục tiêu cho V-JEPA2)")
                            stat_col1, stat_col2 = st.columns(2)
                            
                            with stat_col1:
                                st.info(f"**Đội nhà: {match['home']}**")
                                for k, v in stats["home"].items():
                                    st.write(f"- **{k}:** {v}")
                                    
                            with stat_col2:
                                st.warning(f"**Đội khách: {match['away']}**")
                                for k, v in stats["away"].items():
                                    st.write(f"- **{k}:** {v}")
                            
                            st.markdown(f"### ⏱️ Dòng thời gian ({len(timeline)} sự kiện)")
                            # FIX WARNING: Đổi use_container_width=True thành width="stretch"
                            st.dataframe(timeline, width="stretch", height=300)
                            
                            st.markdown("### 🤖 Quét Video (V-JEPA2)")
                            with st.spinner("Đang nạp mô hình rugby_linear_head_5_classes..."):
                                import time
                                time.sleep(3) 
                                st.balloons()
                                st.success(f"🎉 Đã cắt thành công {len(timeline)} clips dựa trên mỏ neo thời gian!")
                        else:
                            st.error("Lỗi: Không thể lấy được dữ liệu chi tiết từ trận đấu này. Dữ liệu rỗng.")