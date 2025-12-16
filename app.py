import streamlit as st
import pandas as pd
import plotly.express as px
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime
import re

# --- 設定頁面 ---
st.set_page_config(page_title="我的自動化投資儀表板", layout="wide")

# --- 連接 Google Sheets 的函數 ---
@st.cache_resource
def connect_to_gsheets():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    try:
        creds_dict = dict(st.secrets["gcp_service_account"])
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        client = gspread.authorize(creds)
        return client
    except Exception as e:
        st.error(f"連線失敗，請檢查 Secrets 設定: {e}")
        return None

def clean_currency_value(val):
    """將含有 $ , 等符號的字串轉為 float"""
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, str):
        # 移除 $ , 和空白
        clean_val = re.sub(r'[$,\s]', '', val)
        try:
            return float(clean_val)
        except ValueError:
            return 0.0
    return 0.0

def main():
    st.title("💰 我的投資戰情室")
    st.markdown("---")

    client = connect_to_gsheets()
    if not client:
        st.stop()

    try:
        sheet_url = st.secrets["private_gsheets_url"]
        sh = client.open_by_url(sheet_url)
    except Exception as e:
        st.error("無法開啟試算表，請確認網址與權限。")
        st.stop()

    # --- 側邊欄 ---
    st.sidebar.header("功能選單")
    menu = st.sidebar.radio("請選擇", ["資產總覽 (分幣別)", "個別基金明細", "📝 新增交易"])

    # ==========================================
    # 功能 1: 資產總覽 (分幣別統計)
    # ==========================================
    if menu == "資產總覽 (分幣別)":
        if st.button("🔄 重新整理"):
            st.cache_data.clear()
        
        try:
            ws = sh.worksheet("總和")
            data = ws.get_all_records()
            df_summary = pd.DataFrame(data)

            # 1. 識別關鍵欄位 (模糊搜尋)
            try:
                # 幣別欄位
                curr_col = [c for c in df_summary.columns if "幣別" in c or "Currency" in c][0]
                # 數值欄位
                val_col = [c for c in df_summary.columns if "總現值" in c and "含息" in c][0]
                profit_col = [c for c in df_summary.columns if "損益" in c and "含息" in c][0]
                name_col = [c for c in df_summary.columns if "基金名稱" in c][0]
                
                # 2. 資料清理 (轉數字)
                df_summary[val_col] = df_summary[val_col].apply(clean_currency_value)
                df_summary[profit_col] = df_summary[profit_col].apply(clean_currency_value)
                
                # 確保幣別是大寫 (避免 usd 和 USD 分開)
                df_summary[curr_col] = df_summary[curr_col].astype(str).str.upper().str.strip()

                st.subheader("📊 各幣別資產統計")
                
                # 3. 依幣別分組顯示
                unique_currencies = df_summary[curr_col].unique()
                
                # 為了版面美觀，我們動態建立 columns
                cols = st.columns(len(unique_currencies))
                
                for i, currency in enumerate(unique_currencies):
                    # 篩選該幣別的資料
                    df_curr = df_summary[df_summary[curr_col] == currency]
                    
                    total_assets = df_curr[val_col].sum()
                    total_profit = df_curr[profit_col].sum()
                    
                    # 計算報酬率 (避免除以零)
                    total_cost = total_assets - total_profit
                    roi = (total_profit / total_cost * 100) if total_cost != 0 else 0
                    
                    with cols[i]:
                        st.markdown(f"### 💱 {currency}")
                        st.metric("總現值 (含息)", f"{total_assets:,.2f}")
                        st.metric("總損益", f"{total_profit:,.2f}", 
                                  delta=f"{roi:.2f}%")
                        st.caption(f"包含 {len(df_curr)} 檔標的")

                st.divider()

                # --- 圖表區 (依幣別篩選) ---
                c1, c2 = st.columns([1, 1])
                
                with c1:
                    st.subheader("資產分佈 (可選幣別)")
                    selected_curr_chart = st.selectbox("選擇幣別查看圖表", unique_currencies)
                    
                    # 只畫選定幣別的圖
                    df_chart = df_summary[df_summary[curr_col] == selected_curr_chart]
                    
                    fig_pie = px.pie(df_chart, values=val_col, names=name_col, 
                                     title=f"{selected_curr_chart} 資產配置", hole=0.4)
                    st.plotly_chart(fig_pie, use_container_width=True)

                with c2:
                    st.subheader("損益排行")
                    # 混和顯示或分開顯示皆可，這裡顯示全部但標示幣別
                    df_summary['標籤'] = df_summary[name_col] + " (" + df_summary[curr_col] + ")"
                    df_sorted = df_summary.sort_values(by=profit_col, ascending=False)
                    
                    fig_bar = px.bar(df_sorted, x=profit_col, y='標籤', orientation='h',
                                     color=profit_col, color_continuous_scale="RdYlGn",
                                     title="全資產損益金額排行")
                    st.plotly_chart(fig_bar, use_container_width=True)

                # 詳細表格
                st.subheader("詳細清單")
                st.dataframe(df_summary)

            except IndexError:
                st.error("欄位識別失敗：請確認 Google Sheet '總和' 分頁中是否包含 [幣別, 總現值(含息), 損益(含息), 基金名稱] 等欄位。")
                st.write("目前讀取到的欄位:", df_summary.columns.tolist())

        except Exception as e:
            st.error(f"讀取資料發生錯誤: {e}")

    # ==========================================
    # 功能 2: 個別基金明細 (修正重複欄位版)
    # ==========================================
    elif menu == "個別基金明細":
        ignore_sheets = ["總和", "配息", "工作表1", "Lists", "Dropdowns"] 
        all_sheets = [s.title for s in sh.worksheets() if s.title not in ignore_sheets]
        
        selected_fund = st.selectbox("選擇基金", all_sheets)
        
        if selected_fund:
            try:
                ws = sh.worksheet(selected_fund)
                data = ws.get_all_values()
                
                if len(data) > 0:
                    raw_headers = data[0]
                    rows = data[1:]
                    
                    # 處理重複與空白標題
                    final_headers = []
                    header_count = {}

                    for i, col_name in enumerate(raw_headers):
                        col_name = col_name.strip()
                        if not col_name:
                            col_name = f"空欄_{i}" 
                        
                        if col_name in header_count:
                            header_count[col_name] += 1
                            new_name = f"{col_name}_{header_count[col_name]}"
                        else:
                            header_count[col_name] = 0
                            new_name = col_name
                        final_headers.append(new_name)

                    df = pd.DataFrame(rows, columns=final_headers)
                    st.subheader(f"📂 {selected_fund} 交易紀錄")
                    st.dataframe(df, use_container_width=True)
                else:
                    st.warning("此分頁沒有資料。")
            except Exception as e:
                st.error(f"讀取錯誤: {e}")

    # ==========================================
    # 功能 3: 新增交易
    # ==========================================
    elif menu == "📝 新增交易":
        st.header("新增一筆交易")
        ignore_sheets = ["總和", "配息", "工作表1"] 
        all_sheets = [s.title for s in sh.worksheets() if s.title not in ignore_sheets]
        target_fund = st.selectbox("選擇基金", all_sheets)
        
        with st.form("add_transaction"):
            col1, col2 = st.columns(2)
            with col1:
                date_val = st.date_input("交易日期", datetime.today())
                trans_type = st.selectbox("交易類別", ["買入", "賣出", "配息再投資", "轉換入", "轉換出"])
                price = st.number_input("成交淨值", min_value=0.0, format="%.4f")
            with col2:
                amount = st.number_input("交易總金額", min_value=0.0, format="%.2f")
                fee = st.number_input("手續費", min_value=0.0, format="%.2f", value=0.0)
            
            # 簡易計算預覽
            est_units = 0.0
            if price > 0:
                est_units = (amount - fee) / price if trans_type == "買入" else amount / price
            st.info(f"預估單位數: {est_units:,.4f}")
            
            submit_btn = st.form_submit_button("確認新增")

        if submit_btn:
            try:
                ws = sh.worksheet(target_fund)
                # 依照您的 CSV 格式建立 row
                new_row = [
                    str(date_val), "", trans_type, amount, price, "", fee, "", "", est_units
                ]
                ws.append_row(new_row)
                st.success(f"✅ 已寫入 {target_fund}")
                st.cache_data.clear()
            except Exception as e:
                st.error(f"寫入失敗: {e}")

if __name__ == "__main__":
    main()